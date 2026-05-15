"""
main.py — DevGordon FastAPI backend
=====================================
Architecture:
  - Any OpenAI-compatible LLM endpoint (Groq, Ollama /v1, OpenAI, OpenRouter, Together…)
  - Single unified config: LLM_BASE_URL + LLM_API_KEY + LLM_MODEL
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

load_dotenv()

app = FastAPI(title="DevGordon", version="4.0.0")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ============================================================================
# LLM CONFIG — single source of truth, OpenAI-compatible
# ============================================================================
# Examples:
#   Groq:       LLM_BASE_URL=https://api.groq.com/openai/v1
#   Ollama:     LLM_BASE_URL=http://localhost:11434/v1
#   OpenAI:     LLM_BASE_URL=https://api.openai.com/v1
#   OpenRouter: LLM_BASE_URL=https://openrouter.ai/api/v1
#   Together:   LLM_BASE_URL=https://api.together.xyz/v1

LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_API_KEY: str = os.getenv(
    "LLM_API_KEY",
    os.getenv("GROQ_API_KEY", ""),  # backward-compat shim
)
LLM_MODEL: str = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")

# ---- Infrastructure URLs ----
JENKINS_URL: str = os.getenv("JENKINS_URL", "")
JENKINS_TOKEN: str = os.getenv("JENKINS_TOKEN", "")
SONAR_URL: str = os.getenv("SONAR_URL", "")
SONAR_TOKEN: str = os.getenv("SONAR_TOKEN", "")

# ---- Approval mode ----
_approval_mode: str = "always"

READ_ONLY_TOOLS = {"kubectl_command", "jenkins_status", "docker_operation"}

WRITE_PREFIXES: dict[str, tuple] = {
    "kubectl_command": (
        "apply",
        "delete",
        "create",
        "patch",
        "scale",
        "rollout",
        "exec",
        "cp",
        "port-forward",
        "drain",
        "cordon",
        "taint",
        "label",
        "annotate",
    ),
    "docker_operation": (
        "run",
        "stop",
        "rm",
        "rmi",
        "pull",
        "push",
        "build",
        "exec",
        "kill",
        "start",
        "restart",
        "rename",
        "update",
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

SECRETS_DIR = Path(os.getenv("SECRETS_DIR", "/app/secrets"))
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
    global LLM_API_KEY, JENKINS_TOKEN, SONAR_TOKEN
    s = _read_secrets_file()
    # Support both new key name and legacy groq_api_key
    if s.get("llm_api_key"):
        LLM_API_KEY = s["llm_api_key"]
    elif s.get("groq_api_key"):
        LLM_API_KEY = s["groq_api_key"]
    if s.get("jenkins_token"):
        JENKINS_TOKEN = s["jenkins_token"]
    if s.get("sonar_token"):
        SONAR_TOKEN = s["sonar_token"]


_load_secrets_on_startup()


@app.get("/secrets")
async def get_secrets():
    s = _read_secrets_file()
    return {
        "llm_api_key": bool(s.get("llm_api_key") or s.get("groq_api_key")),
        "jenkins_token": bool(s.get("jenkins_token")),
        "sonar_token": bool(s.get("sonar_token")),
    }


@app.post("/secrets")
async def save_secrets(body: dict):
    global LLM_API_KEY, JENKINS_TOKEN, SONAR_TOKEN
    existing = _read_secrets_file()
    saved: list[str] = []

    for field, store_key in {
        "llm_api_key": "llm_api_key",
        "jenkins_token": "jenkins_token",
        "sonar_token": "sonar_token",
    }.items():
        val = body.get(field, "").strip()
        if val:
            existing[store_key] = val
            saved.append(field)

    _write_secrets_file(existing)

    if existing.get("llm_api_key"):
        LLM_API_KEY = existing["llm_api_key"]
    if existing.get("jenkins_token"):
        JENKINS_TOKEN = existing["jenkins_token"]
    if existing.get("sonar_token"):
        SONAR_TOKEN = existing["sonar_token"]

    return {"status": "saved", "keys_saved": saved}


@app.delete("/secrets/{key}")
async def delete_secret(key: str):
    allowed = {"llm_api_key", "jenkins_token", "sonar_token"}
    if key not in allowed:
        raise HTTPException(400, f"Unknown key '{key}'")
    existing = _read_secrets_file()
    existing.pop(key, None)
    _write_secrets_file(existing)
    return {"status": "deleted", "key": key}


# ============================================================================
# CONVERSATION PERSISTENCE
# ============================================================================

CONVERSATIONS_DIR = Path(
    os.getenv("CONVERSATIONS_DIR", str(Path(__file__).parent / "conversations"))
)
CONVERSATIONS_DIR.mkdir(exist_ok=True)

_conversation_history: list[dict] = []
_active_conversation_id: str | None = None


def _conversation_path(conv_id: str) -> Path:
    return CONVERSATIONS_DIR / f"{conv_id}.json"


def _save_conversation(
    conv_id: str, messages: list[dict], title: str | None = None
) -> dict:
    existing_path = _conversation_path(conv_id)
    if not title:
        for m in messages:
            if m.get("role") == "user" and m.get("content"):
                raw = str(m["content"])
                title = raw[:60] + ("…" if len(raw) > 60 else "")
                break
        title = title or "Untitled conversation"

    now = datetime.now(timezone.utc).isoformat()
    created_at = now
    if existing_path.exists():
        try:
            existing = json.loads(existing_path.read_text())
            created_at = existing.get("created_at", now)
            title = title or existing.get("title", title)
        except Exception:
            pass

    metadata = {
        "id": conv_id,
        "title": title,
        "created_at": created_at,
        "updated_at": now,
        "message_count": len(
            [m for m in messages if m.get("role") in ("user", "assistant")]
        ),
        "messages": messages,
    }
    existing_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
    return {k: v for k, v in metadata.items() if k != "messages"}


def _list_conversations() -> list[dict]:
    result = []
    for p in CONVERSATIONS_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text())
            result.append(
                {
                    "id": data["id"],
                    "title": data.get("title", "Untitled"),
                    "created_at": data.get("created_at", ""),
                    "updated_at": data.get("updated_at", ""),
                    "message_count": data.get("message_count", 0),
                }
            )
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
# LLM — single OpenAI-compatible client
# ============================================================================


def _build_headers() -> dict:
    """Auth header. Ollama works with no key; other providers need Bearer."""
    if LLM_API_KEY:
        return {
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
        }
    return {"Content-Type": "application/json"}


def _sanitise_messages(messages: list[dict]) -> list[dict]:
    """
    Normalise conversation history to strict OpenAI format:
    - role:tool  → role:user  (tool-result wrapper)
    - assistant  → strip tool_calls from all but the last assistant turn
                   (re-sending old tool_calls confuses most providers)
    - ensure content is always a string, never None
    """
    out: list[dict] = []
    for i, m in enumerate(messages):
        role = m.get("role", "")
        content = m.get("content") or ""

        if role == "tool":
            out.append({"role": "user", "content": f"[tool result]\n{content}"})
        elif role == "assistant":
            entry: dict = {"role": "assistant", "content": content}
            # Only forward tool_calls on the very last assistant message
            if m.get("tool_calls") and i == len(messages) - 1:
                tcs = []
                for tc in m["tool_calls"]:
                    args = tc.get("function", {}).get("arguments", {})
                    tcs.append(
                        {
                            "id": tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                            "type": "function",
                            "function": {
                                "name": tc["function"]["name"],
                                # OpenAI spec requires arguments as a JSON string
                                "arguments": json.dumps(args)
                                if isinstance(args, dict)
                                else (args or "{}"),
                            },
                        }
                    )
                entry["tool_calls"] = tcs
            out.append(entry)
        else:
            out.append({"role": role, "content": content})
    return out


async def _call_llm(messages: list[dict]) -> dict:
    """
    POST to any OpenAI-compatible /chat/completions endpoint.
    Returns a normalised dict: {"message": {"content": str, "tool_calls": list}}
    """
    from tools import TOOL_DEFINITIONS

    if not messages or messages[0].get("role") != "system":
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    tools = [
        {
            "type": "function",
            "function": {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "parameters": t["function"].get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            },
        }
        for t in TOOL_DEFINITIONS
    ]

    payload = {
        "model": LLM_MODEL,
        "messages": _sanitise_messages(messages),
        "tools": tools,
        "tool_choice": "auto",
        "max_tokens": 1024,
    }

    url = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, headers=_build_headers(), json=payload)
    except httpx.ConnectError:
        raise HTTPException(
            503, f"Cannot connect to LLM at {LLM_BASE_URL}. Is it running?"
        )
    except httpx.ReadTimeout:
        raise HTTPException(503, "LLM timed out. Try a smaller/faster model.")
    except Exception as e:
        raise HTTPException(503, f"LLM request error: {e}")

    if resp.status_code != 200:
        raise HTTPException(503, f"LLM error ({resp.status_code}): {resp.text}")

    data = resp.json()
    choice = data.get("choices", [{}])[0]
    msg = choice.get("message", {})
    content = msg.get("content") or ""

    # Normalise tool_calls: arguments stored as dict internally
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

        normalised_tool_calls.append(
            {
                "id": tc.get("id", ""),
                "function": {
                    "name": tc["function"]["name"],
                    "arguments": parsed_args,
                },
            }
        )

    return {
        "message": {
            "content": content,
            "tool_calls": normalised_tool_calls,
        }
    }


async def _interpret(history: list[dict]) -> str:
    """Ask the LLM to summarise tool output — text reply only, no tools."""
    clean = _sanitise_messages(history)
    # Strip tool_calls from all messages for the interpret pass
    for m in clean:
        m.pop("tool_calls", None)

    payload = {
        "model": LLM_MODEL,
        "messages": clean,
        "max_tokens": 512,
    }

    url = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=_build_headers(), json=payload)
            if resp.status_code == 200:
                choice = resp.json().get("choices", [{}])[0]
                return choice.get("message", {}).get("content", "") or ""
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
    status = "unknown"
    models: list[str] = []

    url = f"{LLM_BASE_URL.rstrip('/')}/models"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(url, headers=_build_headers())
            if r.status_code == 200:
                models = [m["id"] for m in r.json().get("data", [])]
                status = (
                    "ok" if any(LLM_MODEL in m for m in models) else "model_not_found"
                )
            else:
                status = "error"
    except Exception:
        status = "unreachable"

    return {
        "status": "ok" if status == "ok" else "degraded",
        "llm_base_url": LLM_BASE_URL,
        "llm_status": status,
        "model_selected": LLM_MODEL,
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
        assert _active_conversation_id is not None
        _save_conversation(_active_conversation_id, list(_conversation_history))
        saved_id = _active_conversation_id

    _conversation_history.clear()
    _active_conversation_id = str(uuid.uuid4())

    return {
        "status": "cleared",
        "saved_as": saved_id,
        "new_session_id": _active_conversation_id,
    }


# ============================================================================
# SERVER CONFIG
# ============================================================================


@app.get("/server-config")
async def get_server_config():
    return {
        "llm_base_url": LLM_BASE_URL,
        "llm_model": LLM_MODEL,
        "llm_key_set": bool(LLM_API_KEY),
        "jenkins_url": JENKINS_URL,
        "jenkins_key_set": bool(JENKINS_TOKEN),
        "sonar_url": SONAR_URL,
        "sonar_key_set": bool(SONAR_TOKEN),
    }


@app.post("/server-config")
async def set_server_config(body: dict):
    global LLM_BASE_URL, LLM_API_KEY, LLM_MODEL
    global JENKINS_URL, JENKINS_TOKEN, SONAR_URL, SONAR_TOKEN

    if body.get("llm_base_url"):
        LLM_BASE_URL = body["llm_base_url"].strip()
    if body.get("llm_model"):
        LLM_MODEL = body["llm_model"].strip()
    if body.get("llm_api_key"):
        LLM_API_KEY = body["llm_api_key"]
    if body.get("jenkins_url"):
        JENKINS_URL = body["jenkins_url"].strip()
    if body.get("jenkins_token"):
        JENKINS_TOKEN = body["jenkins_token"]
    if body.get("sonar_url"):
        SONAR_URL = body["sonar_url"].strip()
    if body.get("sonar_token"):
        SONAR_TOKEN = body["sonar_token"]

    return {
        "status": "ok",
        "llm_base_url": LLM_BASE_URL,
        "llm_model": LLM_MODEL,
        "llm_key_set": bool(LLM_API_KEY),
        "jenkins_url": JENKINS_URL,
        "jenkins_key_set": bool(JENKINS_TOKEN),
        "sonar_url": SONAR_URL,
        "sonar_key_set": bool(SONAR_TOKEN),
    }


@app.post("/select-model")
async def select_model(body: dict):
    global LLM_MODEL
    model_name = body.get("model", "").strip()
    if not model_name:
        raise HTTPException(400, "Missing 'model' field")
    LLM_MODEL = model_name
    return {"status": "ok", "model": LLM_MODEL}


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
        assert _active_conversation_id is not None
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
    if not _delete_conversation(conv_id):
        raise HTTPException(404, f"Conversation '{conv_id}' not found")
    return {"status": "deleted", "id": conv_id}


@app.post("/conversations/save-current")
async def save_current_conversation(body: dict = {}):
    _ensure_active_session()
    if not _conversation_history:
        return {"status": "nothing_to_save"}
    assert _active_conversation_id is not None
    meta = _save_conversation(
        _active_conversation_id, list(_conversation_history), title=body.get("title")
    )
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
        raise HTTPException(503, f"LLM error: {e}")

    message_obj = data.get("message", {})
    tool_calls = message_obj.get("tool_calls", [])
    text = message_obj.get("content", "")

    _conversation_history.append({"role": "user", "content": msg.message})

    if tool_calls:
        tool_call = tool_calls[0]
        call_id = tool_call.get("id", "")
        func = tool_call.get("function", {})
        tool_name = func.get("name", "")

        raw_args = func.get("arguments", {})
        if isinstance(raw_args, str):
            try:
                tool_args = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError):
                tool_args = {}
        else:
            tool_args = raw_args or {}

        explanation = text or f"Running {tool_name} to fulfil your request."

        _conversation_history.append(
            {
                "role": "assistant",
                "content": text,
                "tool_calls": tool_calls,
            }
        )

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
                _conversation_history.append(
                    {"role": "assistant", "content": interpretation}
                )

            return {
                "type": "auto_executed",
                "tool_name": tool_name,
                "tool_args": tool_args,
                "result": result,
                "interpretation": interpretation or explanation,
                "model_used": LLM_MODEL,
            }

        return {
            "type": "pending_approval",
            "call_id": call_id,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "explanation": explanation,
            "scan": scan_result,
            "model_used": LLM_MODEL,
        }

    _conversation_history.append({"role": "assistant", "content": text})

    return {
        "type": "message",
        "content": text or "(no response from LLM)",
        "model_used": LLM_MODEL,
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
        "model_used": LLM_MODEL,
    }


@app.post("/reject")
async def reject(body: dict):
    if body.get("tool_call_id"):
        _conversation_history.append(
            {
                "role": "tool",
                "content": "User rejected this action. Do not retry it.",
            }
        )

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
