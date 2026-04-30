"""
main.py — DevGordon FastAPI backend
=====================================
Architecture:
  - Ollama (local, tool-capable) OR Groq (remote, fast) handles reasoning + tool selection
  - qwen3:8b / llama3.1:8b (Ollama) or llama-3.3-70b-versatile (Groq) — full function calling
  - All infrastructure execution (kubectl, ansible, jenkins, docker) runs locally
  - Pre-scan via scanner.py before every approval card
  - Full agentic loop: user → LLM → tool_call → scan → approve → execute → interpret
  - Persistent conversation history saved to disk (./conversations/)
"""

import os
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Load environment variables from .env file (if present)
load_dotenv()

app = FastAPI(title="DevGordon", version="3.0.0")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ---- LLM Provider Configuration ----
# LLM_PROVIDER: "ollama" (default, local) or "groq" (remote, faster)
LLM_PROVIDER    = os.getenv("LLM_PROVIDER", "ollama").lower()
OLLAMA_URL      = os.getenv("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "qwen3:8b")
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL      = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_API_BASE   = "https://api.groq.com/openai/v1"

# ---- Convenience: active model name for responses ----
def _active_model() -> str:
    return GROQ_MODEL if LLM_PROVIDER == "groq" else OLLAMA_MODEL

# ---- Infrastructure URLs ----
JENKINS_URL = os.getenv("JENKINS_URL")
SONAR_URL   = os.getenv("SONAR_URL")

# ---- Approval mode ----
# "always" → approval card for every tool call (default)
# "writes" → auto-execute read-only tools, card only for write operations
# "never"  → auto-execute everything, no approval cards
_approval_mode: str = "always"

READ_ONLY_TOOLS = {"kubectl_command", "jenkins_status", "docker_operation"}

WRITE_PREFIXES = {
    "kubectl_command": (
        "apply", "delete", "create", "patch", "scale", "rollout",
        "exec", "cp", "port-forward", "drain", "cordon", "taint",
        "label", "annotate",
    ),
    "docker_operation": (
        "run", "stop", "rm", "rmi", "pull", "push", "build",
        "exec", "kill", "start", "restart", "rename", "update",
    ),
}


def _is_read_only(tool_name: str, tool_args: dict) -> bool:
    if tool_name not in READ_ONLY_TOOLS:
        return False
    write_prefixes = WRITE_PREFIXES.get(tool_name, ())
    if write_prefixes:
        cmd = tool_args.get("command", tool_args.get("operation", ""))
        first = cmd.strip().split()[0].lower() if cmd.strip() else ""
        if first in write_prefixes:
            return False
    return True


# ============================================================================
# CONVERSATION PERSISTENCE
# ============================================================================

CONVERSATIONS_DIR = Path(__file__).parent / "conversations"
CONVERSATIONS_DIR.mkdir(exist_ok=True)

# In-memory conversation history for the active session
_conversation_history: list[dict] = []
_active_conversation_id: str | None = None


def _conversation_path(conv_id: str) -> Path:
    return CONVERSATIONS_DIR / f"{conv_id}.json"


def _save_conversation(conv_id: str, messages: list[dict], title: str | None = None) -> dict:
    """Persist a conversation to disk. Returns the metadata dict."""
    existing_path = _conversation_path(conv_id)

    # Derive title from first user message if not provided
    if not title:
        for m in messages:
            if m.get("role") == "user" and m.get("content"):
                raw = str(m["content"])
                title = raw[:60] + ("…" if len(raw) > 60 else "")
                break
        title = title or "Untitled conversation"

    now = datetime.now(timezone.utc).isoformat()

    if existing_path.exists():
        try:
            existing = json.loads(existing_path.read_text())
            created_at = existing.get("created_at", now)
            # Keep original title unless one is explicitly given
            if not title:
                title = existing.get("title", title)
        except Exception:
            created_at = now
    else:
        created_at = now

    metadata = {
        "id": conv_id,
        "title": title,
        "created_at": created_at,
        "updated_at": now,
        "message_count": len([m for m in messages if m.get("role") in ("user", "assistant")]),
        "messages": messages,
    }

    existing_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
    return {k: v for k, v in metadata.items() if k != "messages"}  # return meta without messages


def _list_conversations() -> list[dict]:
    """Return metadata for all saved conversations, newest first."""
    result = []
    for p in CONVERSATIONS_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text())
            result.append({
                "id": data["id"],
                "title": data.get("title", "Untitled"),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "message_count": data.get("message_count", 0),
            })
        except Exception:
            pass
    result.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return result


def _load_conversation(conv_id: str) -> dict | None:
    p = _conversation_path(conv_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _delete_conversation(conv_id: str) -> bool:
    p = _conversation_path(conv_id)
    if p.exists():
        p.unlink()
        return True
    return False


def _ensure_active_session():
    """Create a new session ID if there isn't one."""
    global _active_conversation_id
    if not _active_conversation_id:
        _active_conversation_id = str(uuid.uuid4())


# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class ChatMessage(BaseModel):
    message: str
    history: list[dict] = []


# ============================================================================
# SYSTEM PROMPT
# ============================================================================

SYSTEM_PROMPT = """You are DevGordon, a conversational DevOps assistant running in a local lab.

Your stack:
- Kubernetes (Minikube) — container orchestration
- Docker — containerization
- Jenkins — CI/CD pipelines
- Ansible — infrastructure automation
- SonarQube — code quality analysis
- LXC/LXD — lightweight Linux containers

Your behaviour:
1. Answer questions directly when no tool is needed.
2. Call tools whenever the user wants something actually DONE on the infrastructure.
3. Before calling a tool, write ONE short sentence explaining what you're about to do.
4. Be terse. Terminal-style. No fluff, no filler.

Tool routing:
- Kubernetes questions / pod status / deployments → kubectl_command
- Infrastructure config / package install / service management → run_ansible_playbook
- Build / deploy / pipeline status → trigger_jenkins_job or jenkins_status
- Container listing / logs / images → docker_operation

IMPORTANT: You never execute without user approval. Calling a tool triggers an approval
card in the UI — the user sees the command, the security scan result, and approves/rejects.
UNLESS the specific approval mode has been toggled."""


# ============================================================================
# LLM CALL — Ollama or Groq
# ============================================================================

async def _call_ollama(messages: list[dict]) -> dict:
    """
    Call Ollama /api/chat endpoint with tool calling.
    Returns Ollama-format response: {"message": {"content": "...", "tool_calls": [...]}}
    """
    from tools import TOOL_DEFINITIONS

    tools = [
        {
            "type": "function",
            "function": {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "parameters": t["function"].get("parameters", {"type": "object", "properties": {}}),
            },
        }
        for t in TOOL_DEFINITIONS
    ]

    if not messages or messages[0].get("role") != "system":
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "tools": tools,
        "stream": False,
        "options": {"think": False},
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            if resp.status_code != 200:
                raise HTTPException(503, f"Ollama error ({resp.status_code}): {resp.text}")
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(503, f"Cannot connect to Ollama at {OLLAMA_URL}. Is it running?")
    except httpx.ReadTimeout:
        raise HTTPException(503, "Ollama timed out — model too slow. Try: ollama pull llama3.1:8b")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"Ollama request error: {str(e)}")


async def _call_groq(messages: list[dict]) -> dict:
    """
    Call Groq's OpenAI-compatible /chat/completions endpoint.
    Normalises the response into Ollama-style format so the rest of the
    code works identically regardless of provider.
    """
    from tools import TOOL_DEFINITIONS

    if not GROQ_API_KEY:
        raise HTTPException(503, "GROQ_API_KEY is not set. Add it to your .env file.")

    tools = [
        {
            "type": "function",
            "function": {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "parameters": t["function"].get("parameters", {"type": "object", "properties": {}}),
            },
        }
        for t in TOOL_DEFINITIONS
    ]

    if not messages or messages[0].get("role") != "system":
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    # Groq doesn't accept role="tool" without a matching tool_call_id in the
    # preceding assistant message.  Sanitise history: any "tool" turn becomes a
    # "user" turn so the conversation stays valid.
    sanitised: list[dict] = []
    for m in messages:
        if m.get("role") == "tool":
            sanitised.append({"role": "user", "content": f"[tool result] {m.get('content', '')}"})
        else:
            # Strip non-standard keys (Ollama puts tool_calls on assistant turns)
            entry = {"role": m["role"], "content": m.get("content") or ""}
            if m.get("tool_calls") and m["role"] == "assistant":
                entry["tool_calls"] = m["tool_calls"]
            sanitised.append(entry)

    payload = {
        "model": GROQ_MODEL,
        "messages": sanitised,
        "tools": tools,
        "tool_choice": "auto",
        "max_tokens": 1024,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{GROQ_API_BASE}/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if resp.status_code != 200:
                raise HTTPException(503, f"Groq error ({resp.status_code}): {resp.text}")

            data = resp.json()

    except httpx.ConnectError:
        raise HTTPException(503, "Cannot connect to Groq API. Check your internet connection.")
    except httpx.ReadTimeout:
        raise HTTPException(503, "Groq API timed out.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"Groq request error: {str(e)}")

    # --- Normalise to Ollama-style response ---
    choice = data.get("choices", [{}])[0]
    msg = choice.get("message", {})
    content = msg.get("content") or ""

    # Groq tool_calls → Ollama-style tool_calls
    # Groq:  [{id, type, function: {name, arguments}}]  (arguments is JSON string)
    # Ollama: [{id, function: {name, arguments}}]        (arguments is dict)
    normalised_tool_calls = []
    for tc in msg.get("tool_calls") or []:
        raw_args = tc.get("function", {}).get("arguments", "{}")
        if isinstance(raw_args, str):
            try:
                parsed_args = json.loads(raw_args)
            except Exception:
                parsed_args = {}
        else:
            parsed_args = raw_args or {}

        normalised_tool_calls.append({
            "id": tc.get("id", ""),
            "function": {
                "name": tc["function"]["name"],
                "arguments": parsed_args,
            },
        })

    return {
        "message": {
            "content": content,
            "tool_calls": normalised_tool_calls or [],
        },
        "done": True,
    }


async def _call_llm(messages: list[dict]) -> dict:
    """Route to the configured LLM provider."""
    if LLM_PROVIDER == "groq":
        return await _call_groq(messages)
    return await _call_ollama(messages)


async def _interpret(history: list[dict]) -> str:
    """Ask the LLM to interpret tool output — text reply only, no tools."""
    try:
        if LLM_PROVIDER == "groq":
            # Strip tool turns for Groq
            clean = []
            for m in history:
                if m.get("role") == "tool":
                    clean.append({"role": "user", "content": f"[tool result] {m.get('content', '')}"})
                else:
                    clean.append({"role": m["role"], "content": m.get("content") or ""})

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{GROQ_API_BASE}/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                    json={"model": GROQ_MODEL, "messages": clean, "max_tokens": 512},
                )
                if resp.status_code == 200:
                    choice = resp.json().get("choices", [{}])[0]
                    return choice.get("message", {}).get("content", "") or ""
        else:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{OLLAMA_URL}/api/chat",
                    json={"model": OLLAMA_MODEL, "messages": list(history), "stream": False},
                )
                if resp.status_code == 200:
                    return resp.json().get("message", {}).get("content", "") or ""
    except Exception:
        pass
    return ""


# ============================================================================
# ROUTES — Meta / Config
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def index():
    return (Path(__file__).parent / "static" / "index.html").read_text()


@app.get("/health")
async def health():
    provider_status = "unknown"
    models: list[str] = []

    if LLM_PROVIDER == "groq":
        if GROQ_API_KEY:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    r = await client.get(
                        f"{GROQ_API_BASE}/models",
                        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                    )
                    if r.status_code == 200:
                        models = [m["id"] for m in r.json().get("data", [])]
                        provider_status = "ok" if any(GROQ_MODEL in m for m in models) else "model_not_found"
                    else:
                        provider_status = "error"
            except Exception:
                provider_status = "unreachable"
        else:
            provider_status = "no_api_key"
    else:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{OLLAMA_URL}/api/tags")
                models = [m["name"] for m in r.json().get("models", [])]
            provider_status = "ok" if OLLAMA_MODEL in models else "model_not_found"
        except Exception:
            provider_status = "unreachable"

    return {
        "status": "ok" if provider_status == "ok" else "degraded",
        "provider": LLM_PROVIDER,
        "provider_status": provider_status,
        "model_selected": _active_model(),
        "models_available": models,
        "jenkins": JENKINS_URL,
        "sonarqube": SONAR_URL,
    }


@app.get("/approval-mode")
async def get_approval_mode():
    return {"mode": _approval_mode}


@app.post("/approval-mode")
async def set_approval_mode(body: dict):
    global _approval_mode
    mode = body.get("mode", "")
    if mode not in ("always", "writes", "never"):
        raise HTTPException(400, "mode must be 'always', 'writes', or 'never'")
    _approval_mode = mode
    return {"mode": _approval_mode}


@app.get("/history")
async def get_history():
    return {"history": _conversation_history}


@app.delete("/history")
async def clear_history():
    _conversation_history.clear()
    return {"status": "cleared"}


@app.post("/reset")
async def reset_conversation():
    """
    Start a new conversation.
    Auto-saves the current session to disk before clearing if it has messages.
    """
    global _active_conversation_id
    _ensure_active_session()

    saved_id = None
    if _conversation_history:
        _save_conversation(_active_conversation_id, list(_conversation_history))
        saved_id = _active_conversation_id

    _conversation_history.clear()
    _active_conversation_id = str(uuid.uuid4())

    return {"status": "cleared", "saved_as": saved_id, "new_session_id": _active_conversation_id}


# ============================================================================
# SERVER CONFIG  (hot-swap URL / model / key at runtime)
# ============================================================================

@app.get("/server-config")
async def get_server_config():
    """Return current LLM connection settings (key is masked)."""
    return {
        "provider":    LLM_PROVIDER,
        "ollama_url":  OLLAMA_URL,
        "ollama_model": OLLAMA_MODEL,
        "groq_model":  GROQ_MODEL,
        "groq_key_set": bool(GROQ_API_KEY),
    }


@app.post("/server-config")
async def set_server_config(body: dict):
    """
    Hot-swap LLM connection at runtime — no restart needed.

    Accepted fields (all optional — omit to leave unchanged):
      provider   : "ollama" | "groq"
      url        : Ollama-compatible base URL
                   e.g. "http://localhost:11434"      (local Ollama)
                        "https://api.groq.com/openai/v1"  (Groq)
                        "http://192.168.1.50:11434"   (remote Ollama box)
      model      : model name for the chosen provider
      api_key    : bearer token (stored in memory only, never written to disk)
    """
    global LLM_PROVIDER, OLLAMA_URL, OLLAMA_MODEL, GROQ_API_KEY, GROQ_MODEL

    provider = body.get("provider", "").lower()
    url      = body.get("url",      "").strip()
    model    = body.get("model",    "").strip()
    api_key  = body.get("api_key",  "")

    if provider in ("ollama", "groq"):
        LLM_PROVIDER = provider

    if url:
        # Auto-detect provider from URL if not explicitly given
        if not provider:
            if "groq.com" in url or "openai.com" in url:
                LLM_PROVIDER = "groq"
            else:
                LLM_PROVIDER = "ollama"

        if LLM_PROVIDER == "groq":
            # Store base URL override (not used yet — Groq URL is fixed, but future-proof)
            pass
        else:
            OLLAMA_URL = url

    if model:
        if LLM_PROVIDER == "groq":
            GROQ_MODEL = model
        else:
            OLLAMA_MODEL = model

    if api_key:
        GROQ_API_KEY = api_key

    return {
        "status":      "ok",
        "provider":    LLM_PROVIDER,
        "ollama_url":  OLLAMA_URL,
        "model":       _active_model(),
        "groq_key_set": bool(GROQ_API_KEY),
    }


@app.post("/select-model")
async def select_model(body: dict):
    """
    Select a model. For Ollama, verifies it's available locally.
    For Groq, just updates the global without verification.
    """
    global OLLAMA_MODEL, GROQ_MODEL, LLM_PROVIDER
    model_name = body.get("model", "")
    provider   = body.get("provider", LLM_PROVIDER).lower()

    if provider not in ("ollama", "groq"):
        return {"error": "provider must be 'ollama' or 'groq'"}

    LLM_PROVIDER = provider

    if provider == "groq":
        if model_name:
            GROQ_MODEL = model_name
        return {"status": "ok", "provider": "groq", "selected_model": GROQ_MODEL}

    # Ollama path — verify model exists
    available: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            available = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass

    if model_name and model_name not in available:
        return {"error": f"Model '{model_name}' not found. Available: {available}"}

    if model_name:
        OLLAMA_MODEL = model_name

    return {"status": "ok", "provider": "ollama", "selected_model": OLLAMA_MODEL, "available_models": available}


# ============================================================================
# ROUTES — Conversation History Sidebar
# ============================================================================

@app.get("/conversations")
async def list_conversations():
    """List all saved conversations (newest first) for the sidebar."""
    return {"conversations": _list_conversations()}


@app.get("/conversations/{conv_id}")
async def load_conversation(conv_id: str):
    """Load a full saved conversation (including messages)."""
    data = _load_conversation(conv_id)
    if not data:
        raise HTTPException(404, f"Conversation '{conv_id}' not found")
    return data


@app.post("/conversations/{conv_id}/restore")
async def restore_conversation(conv_id: str):
    """
    Restore a saved conversation as the active session.
    Auto-saves the current session first if it has messages.
    """
    global _active_conversation_id

    data = _load_conversation(conv_id)
    if not data:
        raise HTTPException(404, f"Conversation '{conv_id}' not found")

    _ensure_active_session()
    if _conversation_history:
        _save_conversation(_active_conversation_id, list(_conversation_history))

    _conversation_history.clear()
    _conversation_history.extend(data.get("messages", []))
    _active_conversation_id = conv_id

    return {
        "status": "restored",
        "id": conv_id,
        "title": data.get("title", ""),
        "message_count": len(_conversation_history),
    }


@app.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    """Delete a saved conversation from disk."""
    deleted = _delete_conversation(conv_id)
    if not deleted:
        raise HTTPException(404, f"Conversation '{conv_id}' not found")
    return {"status": "deleted", "id": conv_id}


@app.post("/conversations/save-current")
async def save_current_conversation(body: dict = {}):
    """Explicitly save the current in-memory session to disk."""
    _ensure_active_session()
    if not _conversation_history:
        return {"status": "nothing_to_save"}
    title = body.get("title")
    meta = _save_conversation(_active_conversation_id, list(_conversation_history), title=title)
    return {"status": "saved", **meta}


# ============================================================================
# CORE: CHAT  →  APPROVAL CARD
# ============================================================================

@app.post("/chat")
async def chat(msg: ChatMessage):
    """
    Send a message to DevGordon.

    Flow:
      1. Build conversation from server-side history (or override with msg.history)
      2. Call LLM (Ollama or Groq) with tool schema
      3a. No tool_calls → plain text reply
      3b. tool_calls → run pre_scan, return pending_approval card
    """
    _ensure_active_session()

    messages = list(_conversation_history) if not msg.history else list(msg.history)
    messages.append({"role": "user", "content": msg.message})

    try:
        data = await _call_llm(messages)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"LLM error: {str(e)}")

    message_obj  = data.get("message", {})
    tool_calls   = message_obj.get("tool_calls", [])
    text         = message_obj.get("content", "")

    # Update server-side history
    _conversation_history.append({"role": "user", "content": msg.message})

    # ---- Tool call path ----
    if tool_calls:
        tool_call = tool_calls[0]
        call_id   = tool_call.get("id", "")
        func      = tool_call.get("function", {})
        tool_name = func.get("name", "")

        raw_args  = func.get("arguments", {})
        if isinstance(raw_args, str):
            try:
                tool_args = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError):
                tool_args = {}
        else:
            tool_args = raw_args or {}

        explanation = text or f"Running {tool_name} to fulfil your request."

        _conversation_history.append({
            "role": "assistant",
            "content": text,
            "tool_calls": tool_calls,
        })

        from scanner import pre_scan
        scan_result = pre_scan(tool_name, tool_args)

        needs_card = True
        if _approval_mode == "never":
            needs_card = False
        elif _approval_mode == "writes" and _is_read_only(tool_name, tool_args):
            needs_card = False

        if not needs_card:
            from tools import execute_tool
            result = execute_tool(tool_name, tool_args)
            output_snippet = str(result.get("output", ""))[:2000]
            _conversation_history.append({"role": "tool", "content": output_snippet})

            interpretation = await _interpret(list(_conversation_history))
            if interpretation:
                _conversation_history.append({"role": "assistant", "content": interpretation})

            return {
                "type": "auto_executed",
                "tool_name": tool_name,
                "tool_args": tool_args,
                "result": result,
                "interpretation": interpretation or explanation,
                "model_used": _active_model(),
            }

        return {
            "type": "pending_approval",
            "call_id": call_id,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "explanation": explanation,
            "scan": scan_result,
            "model_used": _active_model(),
        }

    # ---- Plain text path ----
    _conversation_history.append({"role": "assistant", "content": text})

    return {
        "type": "message",
        "content": text or "(no response from LLM)",
        "model_used": _active_model(),
    }


# ============================================================================
# CORE: EXECUTE  →  INTERPRET
# ============================================================================

@app.post("/execute")
async def execute(body: dict):
    """
    Execute an approved tool call, then ask the LLM to interpret the output.

    Expects: { "tool_name": "...", "tool_args": {...}, "tool_call_id": "..." }
    Returns: { "result": {...}, "interpretation": "..." }
    """
    from tools import execute_tool

    tool_name = body.get("tool_name")
    tool_args = body.get("tool_args", {})

    if not tool_name:
        raise HTTPException(400, "Missing tool_name")

    result = execute_tool(tool_name, tool_args)

    output_snippet = str(result.get("output", ""))[:2000]
    _conversation_history.append({"role": "tool", "content": output_snippet})

    interpretation = await _interpret(list(_conversation_history))
    if interpretation:
        _conversation_history.append({"role": "assistant", "content": interpretation})

    return {
        "result": result,
        "interpretation": interpretation,
        "model_used": _active_model(),
    }


@app.post("/reject")
async def reject(body: dict):
    """
    User rejected a pending tool call.
    Feed a tool_result back to the LLM and ask for an alternative.
    """
    tool_call_id = body.get("tool_call_id", "")

    if tool_call_id:
        _conversation_history.append({
            "role": "tool",
            "content": "User rejected this action. Do not retry it.",
        })

    suggestion = "Understood. Let me know what you'd like to do differently."
    try:
        interp = await _interpret(list(_conversation_history))
        if interp:
            suggestion = interp
            _conversation_history.append({"role": "assistant", "content": suggestion})
    except Exception:
        pass

    return {"status": "rejected", "suggestion": suggestion}


# ============================================================================
# MCP ENDPOINTS  (external agents / tool discovery)
# ============================================================================

@app.get("/mcp/tools")
async def mcp_list_tools():
    from tools import TOOL_DEFINITIONS
    return {
        "tools": [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "inputSchema": t["function"].get("parameters", {}),
            }
            for t in TOOL_DEFINITIONS
        ]
    }


@app.post("/mcp/call")
async def mcp_call_tool(body: dict):
    from tools import execute_tool
    tool_name = body.get("tool")
    args = body.get("arguments", {})
    if not tool_name:
        return {"error": "Missing 'tool' field", "success": False}
    result = execute_tool(tool_name, args)
    return {
        "success": result.get("success", False),
        "output": result.get("output", ""),
        "returncode": result.get("returncode", -1),
    }


# ============================================================================
# CODE QUALITY  (SonarQube / ansible-lint)
# ============================================================================

@app.post("/scan-code")
async def scan_code(body: dict):
    from scanner import pre_scan
    tool_name = body.get("tool_name")
    tool_args = body.get("tool_args", {})
    if not tool_name:
        return {"error": "Missing tool_name"}
    scan_result = pre_scan(tool_name, tool_args)
    return {
        "tool": tool_name,
        "scan_status": scan_result.get("status"),
        "issues": scan_result.get("issues", []),
        "recommendation": scan_result.get("recommendation", ""),
        "can_approve": scan_result.get("status") not in ["error"],
    }


@app.get("/sonarqube-standards")
async def sonarqube_standards():
    from scanner import get_sonarqube_project_issues
    issues = get_sonarqube_project_issues()
    return {
        "status": issues.get("status"),
        "project": "devgordon",
        "recent_issues": issues.get("issues", []),
        "sonar_url": SONAR_URL,
    }