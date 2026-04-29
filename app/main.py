"""
main.py — DevGordon FastAPI backend
=====================================
Architecture:
  - Ollama (local, tool-capable) handles reasoning + tool selection
  - qwen3:8b or llama3.1:8b — full function calling support, no external APIs
  - All infrastructure execution (kubectl, ansible, jenkins, docker) runs locally
  - Pre-scan via scanner.py before every approval card
  - Full agentic loop: user → Ollama → tool_call → scan → approve → execute → interpret
"""

import os
import json
from pathlib import Path
from dotenv import load_dotenv

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Load environment variables from .env file (if present)
load_dotenv()

app = FastAPI(title="DevGordon", version="2.0.0")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Configuration — read from environment variables (set in .env or docker-compose.yml)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")
JENKINS_URL = os.getenv("JENKINS_URL", "http://localhost:8080")
SONAR_URL = os.getenv("SONAR_URL", "http://localhost:9000")

# ---- In-memory conversation history ----
_conversation_history: list[dict] = []


# ---- Pydantic models ----
class ChatMessage(BaseModel):
    message: str
    history: list[dict] = []


# ---- System prompt ----
SYSTEM_PROMPT = """You are DevGordon, a conversational DevOps assistant running in a local lab.

Your stack:
- Kubernetes (Minikube) — container orchestration
- Docker — containerization
- Jenkins — CI/CD pipelines
- Ansible — infrastructure automation
- SonarQube — code quality analysis

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
card in the UI — the user sees the command, the security scan result, and approves/rejects."""


# ---- Ollama chat with tool calling ----
async def _call_ollama(messages: list[dict]) -> dict:
    """
    Call Ollama /api/chat endpoint with tool calling.
    Messages format: [{"role": "user/assistant", "content": "..."}]
    Returns: {"message": {"content": "...", "tool_calls": [...]}, "done": bool}
    
    Raises:
        HTTPException: 503 if Ollama is unavailable or returns error
    """
    from tools import TOOL_DEFINITIONS

    # Convert tool definitions to OpenAI/Ollama format
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

    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "tools": tools,
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json=payload,
            )
            if resp.status_code != 200:
                error_text = resp.text
                raise HTTPException(503, f"Ollama error ({resp.status_code}): {error_text}")
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(503, f"Cannot connect to Ollama at {OLLAMA_URL}. Is it running?")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"Ollama request error: {str(e)}")




# ============================================================================
# ROUTES
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def index():
    return (Path(__file__).parent / "static" / "index.html").read_text()


@app.get("/history")
async def get_history():
    return {"history": _conversation_history}


@app.delete("/history")
async def clear_history():
    _conversation_history.clear()
    return {"status": "cleared"}


@app.post("/reset")
async def reset_conversation():
    _conversation_history.clear()
    return {"status": "cleared"}


@app.get("/health")
async def health():
    # Check Ollama and list available models
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
        ollama_status = "ok" if OLLAMA_MODEL in models else "model_not_found"
    except Exception:
        models = []
        ollama_status = "unreachable"

    return {
        "status": "ok" if ollama_status == "ok" else "degraded",
        "ollama": ollama_status,
        "model_selected": OLLAMA_MODEL,
        "models_available": models,
        "jenkins": JENKINS_URL,
        "sonarqube": SONAR_URL,
    }


@app.post("/select-model")
async def select_model(body: dict):
    """
    Select a different Ollama model. Changes OLLAMA_MODEL env var for new connections.
    Note: Already-connected clients continue with previous model.
    """
    global OLLAMA_MODEL
    model_name = body.get("model", "")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            available = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        available = []

    if model_name and model_name not in available:
        return {"error": f"Model '{model_name}' not found in Ollama. Available: {available}"}

    if model_name:
        OLLAMA_MODEL = model_name
        return {
            "status": "ok",
            "selected_model": model_name,
            "message": f"Switched to {model_name}. New conversations will use this model."
        }

    return {
        "status": "ok",
        "selected_model": OLLAMA_MODEL,
        "available_models": available,
    }


# ============================================================================
# CORE: CHAT  →  APPROVAL CARD
# ============================================================================

@app.post("/chat")
async def chat(msg: ChatMessage):
    """
    Send a message to DevGordon (locally via Ollama with tool calling).

    Flow:
      1. Build conversation from server-side history (or override with msg.history)
      2. Call Ollama /api/chat with tool schema
      3a. No tool_calls in response → plain text reply
      3b. tool_calls present → run pre_scan, return pending_approval card
    """
    # Build message list
    messages = list(_conversation_history) if not msg.history else list(msg.history)
    messages.append({"role": "user", "content": msg.message})

    # Call Ollama with tools
    try:
        data = await _call_ollama(messages)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"Ollama error: {str(e)}")

    message_obj = data.get("message", {})
    tool_calls = message_obj.get("tool_calls", [])
    text = message_obj.get("content", "")

    # ---- Update server-side history ----
    _conversation_history.append({"role": "user", "content": msg.message})

    # ---- Tool call path ----
    if tool_calls:
        # Ollama returns: [{"id": "...", "function": {"name": "...", "arguments": "..."}}]
        tool_call = tool_calls[0]  # Take the first tool call
        call_id = tool_call.get("id", "")
        func = tool_call.get("function", {})
        tool_name = func.get("name", "")
        
        # Arguments come as JSON string, parse them
        try:
            tool_args = json.loads(func.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            tool_args = {}

        explanation = text or f"Running {tool_name} to fulfil your request."

        # Store assistant response with tool_calls
        _conversation_history.append({
            "role": "assistant",
            "content": text,
            "tool_calls": tool_calls,
        })

        # Pre-execution security scan
        from scanner import pre_scan
        scan_result = pre_scan(tool_name, tool_args)

        return {
            "type": "pending_approval",
            "call_id": call_id,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "explanation": explanation,
            "scan": scan_result,
            "model_used": OLLAMA_MODEL,
        }

    # ---- Plain text path ----
    _conversation_history.append({"role": "assistant", "content": text})

    return {
        "type": "message",
        "content": text or "(no response from Ollama)",
        "model_used": OLLAMA_MODEL,
    }


# ============================================================================
# CORE: EXECUTE  →  INTERPRET
# ============================================================================

@app.post("/execute")
async def execute(body: dict):
    """
    Execute an approved tool call, then ask Claude to interpret the output.

    Expects:
      { "tool_name": "...", "tool_args": {...}, "tool_call_id": "..." }

    Returns:
      { "result": {...}, "interpretation": "..." }
    """
    from tools import execute_tool

    tool_name = body.get("tool_name")
    tool_args = body.get("tool_args", {})
    tool_call_id = body.get("tool_call_id", "")

    if not tool_name:
        raise HTTPException(400, "Missing tool_name")

    # Run the actual command / playbook / API call
    result = execute_tool(tool_name, tool_args)

    # Append tool_result turn to history so Claude has context
    output_snippet = str(result.get("output", ""))[:2000]
    _conversation_history.append({
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": tool_call_id,
            "content": output_snippet,
        }],
    })

    # Ask Ollama to interpret (no tools on this call — just a text reply)
    interpretation = ""
    try:
        # For interpretation, we don't need to pass tools — just ask for text
        interp_payload = {
            "model": OLLAMA_MODEL,
            "messages": list(_conversation_history),
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            interp_resp = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json=interp_payload,
            )
            if interp_resp.status_code == 200:
                interp_data = interp_resp.json()
                interpretation = interp_data.get("message", {}).get("content", "")
                if interpretation:
                    _conversation_history.append({"role": "assistant", "content": interpretation})
    except Exception:
        # Interpretation is best-effort; don't fail the whole request
        pass

    return {
        "result": result,
        "interpretation": interpretation,
        "model_used": OLLAMA_MODEL,
    }


@app.post("/reject")
async def reject(body: dict):
    """
    User rejected a pending tool call.
    1. Feed a tool_result back to Claude (required to keep conversation valid).
    2. Ask Claude for an alternative — return that as a suggestion.
    """
    tool_call_id = body.get("tool_call_id", "")

    if tool_call_id:
        _conversation_history.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": "User rejected this action. Do not retry it.",
            }],
        })

    # Ask Ollama what to suggest instead (no tools — just a text reply)
    suggestion = "Understood. Let me know what you'd like to do differently."
    try:
        suggest_payload = {
            "model": OLLAMA_MODEL,
            "messages": list(_conversation_history),
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            suggest_resp = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json=suggest_payload,
            )
            if suggest_resp.status_code == 200:
                suggest_data = suggest_resp.json()
                text = suggest_data.get("message", {}).get("content", "")
                if text:
                    suggestion = text
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