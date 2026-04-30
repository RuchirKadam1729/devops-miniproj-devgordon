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
LLM_PROVIDER    = os.getenv("LLM_PROVIDER", "ollama").lower()
OLLAMA_URL      = os.getenv("OLLAMA_URL")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "qwen3:8b")
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL      = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_API_BASE   = "https://api.groq.com/openai/v1"

def _active_model() -> str:
    return GROQ_MODEL if LLM_PROVIDER == "groq" else OLLAMA_MODEL

# ---- Infrastructure URLs ----
JENKINS_URL   = os.getenv("JENKINS_URL", "")
JENKINS_TOKEN = os.getenv("JENKINS_TOKEN", "")
SONAR_URL     = os.getenv("SONAR_URL", "")
SONAR_TOKEN   = os.getenv("SONAR_TOKEN", "")

# ---- Approval mode ----
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
# SECRETS — persisted API keys that survive container resets
# ============================================================================

# Mount ./secrets:/app/secrets in docker-compose.yml
SECRETS_DIR  = Path(os.getenv("SECRETS_DIR", "/app/secrets"))
SECRETS_FILE = SECRETS_DIR / "keys.json"


def _read_secrets_file() -> dict:
    if SECRETS_FILE.exists():
        try:
            return json.loads(SECRETS_FILE.read_text())
        except Exception:
            pass
    return {}


def _write_secrets_file(data: dict) -> None:
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    SECRETS_FILE.write_text(json.dumps(data, indent=2))


def _load_secrets_on_startup() -> None:
    """
    Apply any persisted keys to the in-memory globals.
    Called once at import time so Groq / Jenkins / Sonar work without
    the user having to re-enter keys after a container restart.
    """
    global GROQ_API_KEY, JENKINS_TOKEN, SONAR_TOKEN
    s = _read_secrets_file()
    if s.get("groq_api_key"):
        GROQ_API_KEY = s["groq_api_key"]
    if s.get("jenkins_token"):
        JENKINS_TOKEN = s["jenkins_token"]
    if s.get("sonar_token"):
        SONAR_TOKEN = s["sonar_token"]


_load_secrets_on_startup()


@app.get("/secrets")
async def get_secrets():
    """Return which keys are saved (boolean only — never expose values)."""
    s = _read_secrets_file()
    return {
        "groq_api_key":  bool(s.get("groq_api_key")),
        "jenkins_token": bool(s.get("jenkins_token")),
        "sonar_token":   bool(s.get("sonar_token")),
    }


@app.post("/secrets")
async def save_secrets(body: dict):
    """
    Persist one or more keys to secrets/keys.json.
    Only non-empty values are written; omitted keys keep their existing value.
    Also updates the in-memory globals immediately so the running server
    picks them up without a restart.
    """
    global GROQ_API_KEY, JENKINS_TOKEN, SONAR_TOKEN

    existing = _read_secrets_file()
    saved: list[str] = []

    mapping = {
        "groq_api_key":  "groq_api_key",
        "jenkins_token": "jenkins_token",
        "sonar_token":   "sonar_token",
    }

    for field, store_key in mapping.items():
        val = body.get(field, "").strip()
        if val:
            existing[store_key] = val
            saved.append(field)

    _write_secrets_file(existing)

    # Propagate into memory immediately
    if existing.get("groq_api_key"):
        GROQ_API_KEY = existing["groq_api_key"]
    if existing.get("jenkins_token"):
        JENKINS_TOKEN = existing["jenkins_token"]
    if existing.get("sonar_token"):
        SONAR_TOKEN = existing["sonar_token"]

    return {"status": "saved", "keys_saved": saved}


@app.delete("/secrets/{key}")
async def delete_secret(key: str):
    """Remove a single key from the secrets file."""
    allowed = {"groq_api_key", "jenkins_token", "sonar_token"}
    if key not in allowed:
        raise HTTPException(400, f"Unknown key '{key}'")
    existing = _read_secrets_file()
    existing.pop(key, None)
    _write_secrets_file(existing)
    return {"status": "deleted", "key": key}


# ============================================================================
# CONVERSATION PERSISTENCE
# ============================================================================

# Allow overriding via env var so the docker-compose volume mount can point
# anywhere (fixes the /app/conversations vs /app/app/conversations mismatch).
CONVERSATIONS_DIR = Path(
    os.getenv("CONVERSATIONS_DIR", str(Path(__file__).parent / "conversations"))
)
CONVERSATIONS_DIR.mkdir(exist_ok=True)

_conversation_history: list[dict] = []
_active_conversation_id: str | None = None


def _conversation_path(conv_id: str) -> Path:
    return CONVERSATIONS_DIR / f"{conv_id}.json"


def _save_conversation(conv_id: str, messages: list[dict], title: str | None = None) -> dict:
    existing_path = _conversation_path(conv_id)
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
    return {k: v for k, v in metadata.items() if k != "messages"}


def _list_conversations() -> list[dict]:
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
        "think": False,   # top-level param (not inside "options"); set True if tool calls stop working
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


def _serialise_tool_calls_for_groq(tool_calls: list) -> list:
    """
    Groq requires tool_calls[].function.arguments to be a JSON *string*,
    not a parsed dict.  Our normalisation layer stores them as dicts for
    convenience, so we re-serialise here before sending history back to Groq.
    """
    out = []
    for tc in tool_calls:
        raw = tc.get("function", {}).get("arguments", {})
        out.append({
            "id":   tc.get("id", ""),
            "type": "function",
            "function": {
                "name":      tc["function"]["name"],
                "arguments": json.dumps(raw) if isinstance(raw, dict) else (raw or "{}"),
            },
        })
    return out


async def _call_groq(messages: list[dict]) -> dict:
    from tools import TOOL_DEFINITIONS

    if not GROQ_API_KEY:
        raise HTTPException(503, "GROQ_API_KEY is not set. Add it via Settings → Saved Keys.")

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

    # ---- Sanitise history for Groq ----
    # • role:"tool" → role:"user"   (Groq needs tool_call_id pairing we don't track)
    # • assistant tool_calls: re-serialise arguments as JSON strings  ← KEY FIX
    sanitised: list[dict] = []
    for m in messages:
        if m.get("role") == "tool":
            sanitised.append({"role": "user", "content": f"[tool result] {m.get('content', '')}"})
        else:
            entry: dict = {"role": m["role"], "content": m.get("content") or ""}
            if m.get("tool_calls") and m["role"] == "assistant":
                entry["tool_calls"] = _serialise_tool_calls_for_groq(m["tool_calls"])
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

    # ---- Normalise to Ollama-style response ----
    choice = data.get("choices", [{}])[0]
    msg    = choice.get("message", {})
    content = msg.get("content") or ""

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
                "name":      tc["function"]["name"],
                "arguments": parsed_args,   # stored as dict internally
            },
        })

    return {
        "message": {
            "content":    content,
            "tool_calls": normalised_tool_calls or [],
        },
        "done": True,
    }


async def _call_llm(messages: list[dict]) -> dict:
    if LLM_PROVIDER == "groq":
        return await _call_groq(messages)
    return await _call_ollama(messages)


async def _interpret(history: list[dict]) -> str:
    """Ask the LLM to interpret tool output — text reply only, no tools."""
    try:
        if LLM_PROVIDER == "groq":
            clean = []
            for m in history:
                if m.get("role") == "tool":
                    clean.append({"role": "user", "content": f"[tool result] {m.get('content', '')}"})
                else:
                    # Strip tool_calls entirely for the interpret pass — we only want text back
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
# SERVER CONFIG
# ============================================================================

@app.get("/server-config")
async def get_server_config():
    return {
        "provider":         LLM_PROVIDER,
        "ollama_url":       OLLAMA_URL,
        "ollama_model":     OLLAMA_MODEL,
        "groq_model":       GROQ_MODEL,
        "groq_key_set":     bool(GROQ_API_KEY),
        "jenkins_url":      JENKINS_URL,
        "jenkins_key_set":  bool(JENKINS_TOKEN),
        "sonar_url":        SONAR_URL,
        "sonar_key_set":    bool(SONAR_TOKEN),
    }


@app.post("/server-config")
async def set_server_config(body: dict):
    global LLM_PROVIDER, OLLAMA_URL, OLLAMA_MODEL, GROQ_API_KEY, GROQ_MODEL
    global JENKINS_URL, JENKINS_TOKEN, SONAR_URL, SONAR_TOKEN

    provider = body.get("provider", "").lower()
    url      = body.get("url",      "").strip()
    model    = body.get("model",    "").strip()
    api_key  = body.get("api_key",  "")

    if provider in ("ollama", "groq"):
        LLM_PROVIDER = provider

    if url:
        if not provider:
            LLM_PROVIDER = "groq" if ("groq.com" in url or "openai.com" in url) else "ollama"
        if LLM_PROVIDER != "groq":
            OLLAMA_URL = url

    if model:
        if LLM_PROVIDER == "groq":
            GROQ_MODEL = model
        else:
            OLLAMA_MODEL = model

    if api_key:
        GROQ_API_KEY = api_key

    if body.get("jenkins_url"):
        JENKINS_URL = body["jenkins_url"].strip()
    if body.get("jenkins_token"):
        JENKINS_TOKEN = body["jenkins_token"]
    if body.get("sonar_url"):
        SONAR_URL = body["sonar_url"].strip()
    if body.get("sonar_token"):
        SONAR_TOKEN = body["sonar_token"]

    return {
        "status":          "ok",
        "provider":        LLM_PROVIDER,
        "ollama_url":      OLLAMA_URL,
        "model":           _active_model(),
        "groq_key_set":    bool(GROQ_API_KEY),
        "jenkins_url":     JENKINS_URL,
        "jenkins_key_set": bool(JENKINS_TOKEN),
        "sonar_url":       SONAR_URL,
        "sonar_key_set":   bool(SONAR_TOKEN),
    }


@app.post("/select-model")
async def select_model(body: dict):
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
    return {"conversations": _list_conversations()}


@app.get("/conversations/{conv_id}")
async def load_conversation(conv_id: str):
    data = _load_conversation(conv_id)
    if not data:
        raise HTTPException(404, f"Conversation '{conv_id}' not found")
    return data


@app.post("/conversations/{conv_id}/restore")
async def restore_conversation(conv_id: str):
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
    deleted = _delete_conversation(conv_id)
    if not deleted:
        raise HTTPException(404, f"Conversation '{conv_id}' not found")
    return {"status": "deleted", "id": conv_id}


@app.post("/conversations/save-current")
async def save_current_conversation(body: dict = {}):
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

    _conversation_history.append({"role": "user", "content": msg.message})

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
# MCP ENDPOINTS
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
# CODE QUALITY
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