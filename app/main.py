import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="DevGordon", version="0.1.0")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
JENKINS_URL = os.getenv("JENKINS_URL", "http://localhost:8080")
SONAR_URL = os.getenv("SONAR_URL", "http://localhost:9000")


# ---- In-memory conversation history + selected model ----
_conversation_history: list[dict] = []
_selected_model: str = ""  # Will be auto-set on first /models call

# ---- Request / response models ----

class ChatMessage(BaseModel):
    message: str
    history: list[dict] = []   # [{"role": "user"|"assistant", "content": "..."}]

class ApprovalRequest(BaseModel):
    tool: str
    args: dict

# ---- Tool definitions ----

DEVOPS_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_ansible_playbook",
            "description": (
                "Generate and run an Ansible playbook to perform a DevOps task. "
                "The playbook will be scanned before execution and shown to the user for approval."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "playbook_yaml": {
                        "type": "string",
                        "description": "Complete YAML content of the Ansible playbook to run."
                    },
                    "description": {
                        "type": "string",
                        "description": "Plain-English explanation of what this playbook does and why."
                    }
                },
                "required": ["playbook_yaml", "description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_jenkins_job",
            "description": "Trigger a Jenkins build job by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_name": {"type": "string", "description": "Name of the Jenkins job to trigger."},
                    "description": {"type": "string", "description": "Plain-English explanation of what this will do."}
                },
                "required": ["job_name", "description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "kubectl_command",
            "description": "Run a kubectl command against the Minikube cluster.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The kubectl command to run, e.g. 'get pods -n default'"},
                    "description": {"type": "string", "description": "Plain-English explanation of what this command does."}
                },
                "required": ["command", "description"]
            }
        }
    },
]

SYSTEM_PROMPT = """You are DevGordon, a DevOps assistant running in a local Kubernetes lab.
Your stack includes: Ansible, Jenkins, Minikube/kubectl, Docker, and SonarQube.

You can:
- Answer questions about your DevOps stack
- Explain what you could do (describe kubectl commands, Ansible playbooks, Jenkins jobs)
- Provide guidance and reasoning

You CANNOT execute commands directly — that requires user approval in the UI.

Be concise and clear. When a user asks what you can do, explain the capabilities.
Do NOT pretend to execute commands. Always suggest what could be done and wait for approval."""


@app.get("/models")
async def list_models():
    """List available Ollama models and return the selected one."""
    global _selected_model
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
    except Exception as e:
        models = []
        return {
            "error": str(e),
            "models": [],
            "selected_model": None,
            "message": "Ollama is not reachable. Make sure it's running and at least one model is installed."
        }
    
    if not models:
        return {
            "error": "no_models",
            "models": [],
            "selected_model": None,
            "message": "No models installed. Run: docker exec devgordon-ollama ollama pull <model-name>"
        }
    
    # Auto-select first available if current selection is missing
    if not _selected_model or _selected_model not in models:
        _selected_model = models[0]
    
    return {
        "models": models,
        "selected_model": _selected_model,
        "message": "Models available"
    }


@app.post("/select-model")
async def select_model(body: dict):
    """Select which model to use for chat."""
    global _selected_model
    model_name = body.get("model", "")
    
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            available = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        available = []
    
    if model_name not in available:
        return {"error": f"Model '{model_name}' not found. Available: {', '.join(available)}"}
    
    _selected_model = model_name
    return {"status": "ok", "selected_model": _selected_model}


# ---- Routes ----

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the chat UI."""
    return (Path(__file__).parent / "static" / "index.html").read_text()


@app.get("/history")
async def get_history():
    """Return the in-memory conversation history for this session."""
    return {"history": _conversation_history}


@app.delete("/history")
async def clear_history():
    """Clear conversation history (used by New Conversation button)."""
    _conversation_history.clear()
    return {"status": "cleared"}


@app.get("/health")
async def health():
    """Quick liveness check — also verifies Ollama is reachable and has models."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        models = []
        ollama_status = "unreachable"
    else:
        ollama_status = "ok" if models else "no_models"

    return {
        "status": "ok",
        "ollama": ollama_status,
        "models_available": models,
        "selected_model": _selected_model if ollama_status == "ok" else None,
        "jenkins": JENKINS_URL,
        "sonarqube": SONAR_URL,
    }


@app.post("/chat")
async def chat(msg: ChatMessage):
    """
    Send a message to the selected Ollama model.
    """
    global _selected_model
    
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += msg.history
    messages.append({"role": "user", "content": msg.message})

    # Validate model exists
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            available = [m["name"] for m in r.json().get("models", [])]
    except Exception as e:
        raise HTTPException(503, f"Cannot reach Ollama: {str(e)}")
    
    if not available:
        raise HTTPException(503, "No models installed in Ollama. Run: ollama pull <model-name>")
    
    # Fall back if selected model doesn't exist
    if not _selected_model or _selected_model not in available:
        _selected_model = available[0]
    
    payload = {
        "model": _selected_model,
        "messages": messages,
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
    except httpx.ConnectError:
        raise HTTPException(503, "Ollama is not reachable. Is it running?")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(503, f"Model '{_selected_model}' not found in Ollama. Try: ollama pull {_selected_model}")
        raise HTTPException(503, f"Ollama error: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(503, f"Ollama error: {str(e)}")

    response_message = data.get("message", {})
    content = response_message.get("content", "(no response)")
    
    _conversation_history.append({"role": "user", "content": msg.message})
    _conversation_history.append({"role": "assistant", "content": content})
    
    return {
        "type": "message",
        "content": content,
        "model_used": _selected_model,
    }


@app.post("/execute")
async def execute(body: dict):
    """Execute a tool call after the user approves it."""
    import subprocess, tempfile, shlex

    tool_name = body.get("tool")
    tool_args = body.get("args", {})

    if tool_name == "run_ansible_playbook":
        playbook_yaml = tool_args.get("playbook_yaml", "")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False, prefix="/tmp/devgordon_"
        ) as f:
            f.write(playbook_yaml)
            tmp_path = f.name

        result = subprocess.run(
            ["ansible-playbook", tmp_path],
            capture_output=True, text=True, timeout=120
        )
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return {
            "result": {
                "success": result.returncode == 0,
                "output": output or "(no output)",
                "exit_code": result.returncode,
            }
        }

    elif tool_name == "trigger_jenkins_job":
        job_name = tool_args.get("job_name")
        jenkins_user = os.getenv("JENKINS_USER", "admin")
        jenkins_token = os.getenv("JENKINS_TOKEN", "")
        url = f"{JENKINS_URL}/job/{job_name}/build"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(url, auth=(jenkins_user, jenkins_token))
            success = r.status_code in (200, 201)
            return {
                "result": {
                    "success": success,
                    "output": f"Build triggered (HTTP {r.status_code})" if success else f"Failed: {r.status_code}",
                }
            }
        except Exception as e:
            return {"result": {"success": False, "output": str(e)}}

    elif tool_name == "kubectl_command":
        command = tool_args.get("command", "")
        args = ["kubectl"] + shlex.split(command)
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return {
            "result": {
                "success": result.returncode == 0,
                "output": output or "(no output)",
                "exit_code": result.returncode,
            }
        }

    raise HTTPException(400, f"Unknown tool: {tool_name}")


@app.post("/reject")
async def reject(body: dict):
    """Reject an approval request."""
    _conversation_history.append({
        "role": "assistant",
        "content": "Action rejected. I'll suggest an alternative."
    })
    return {"status": "rejected"}


@app.post("/reset")
async def reset_conversation():
    """Clear conversation history (used by New Conversation button)."""
    _conversation_history.clear()
    return {"status": "cleared"}


# ---- Helpers ----

async def pre_scan(tool_name: str, args: dict) -> dict:
    """
    Scan a proposed action before showing it to the user.
    Currently lints Ansible playbooks with ansible-lint.
    """
    if tool_name != "run_ansible_playbook":
        return {"status": "ok", "message": "No scan required for this tool."}

    import subprocess, tempfile

    playbook_yaml = args.get("playbook_yaml", "")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yml", delete=False, prefix="/tmp/scan_"
    ) as f:
        f.write(playbook_yaml)
        tmp_path = f.name

    result = subprocess.run(
        ["ansible-lint", tmp_path, "--nocolor"],
        capture_output=True, text=True, timeout=30
    )

    if result.returncode == 0:
        return {"status": "ok", "message": "No issues found — playbook is safe to run."}
    else:
        issues = result.stdout or result.stderr
        return {"status": "warning", "message": issues[:500]}  # cap output length


# ---- MCP (Model Context Protocol) Endpoints ----
# These expose DevGordon tools as standardized MCP endpoints.
# External LLM clients can discover and call tools via /mcp/tools and /mcp/call

@app.get("/mcp/tools")
async def mcp_list_tools():
    """
    MCP endpoint: List all available tools.
    Response matches MCP specification for tool discovery.
    """
    from tools import TOOL_DEFINITIONS
    
    return {
        "tools": [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "inputSchema": t["function"].get("parameters", {})
            }
            for t in TOOL_DEFINITIONS
        ]
    }


@app.post("/mcp/call")
async def mcp_call_tool(body: dict):
    """
    MCP endpoint: Call a tool by name.
    External clients send: {"tool": "tool_name", "arguments": {...}}
    Returns: {"success": bool, "output": str, "returncode": int}
    """
    from tools import execute_tool
    
    tool_name = body.get("tool")
    args = body.get("arguments", {})
    
    if not tool_name:
        return {"error": "Missing 'tool' field", "success": False}
    
    result = execute_tool(tool_name, args)
    return {
        "success": result.get("success", False),
        "output": result.get("output", ""),
        "returncode": result.get("returncode", -1)
    }


# ---- CODE QUALITY SCANNING ----
# Scan generated code/playbooks before execution

@app.post("/scan-code")
async def scan_code(body: dict):
    """
    Pre-execution code quality scan.
    Called before showing approval card to user.
    
    Payload:
    {
      "tool_name": "run_ansible_playbook",
      "tool_args": {"playbook_content": "..."}
    }
    
    Returns scan results with violations.
    """
    from scanner import pre_scan
    
    tool_name = body.get("tool_name")
    tool_args = body.get("tool_args", {})
    
    if not tool_name:
        return {"error": "Missing tool_name"}
    
    scan_result = pre_scan(tool_name, tool_args)
    
    # Flatten result for UI
    return {
        "tool": tool_name,
        "scan_status": scan_result.get("status"),
        "issues": scan_result.get("issues", []),
        "recommendation": scan_result.get("recommendation", ""),
        "can_approve": scan_result.get("status") not in ["error"]
    }


@app.get("/sonarqube-standards")
async def sonarqube_standards():
    """
    Fetch current SonarQube project standards/recent issues.
    Shows the user what code quality gates are enforced.
    """
    from scanner import get_sonarqube_project_issues
    
    issues = get_sonarqube_project_issues()
    return {
        "status": issues.get("status"),
        "project": "devgordon",
        "recent_issues": issues.get("issues", []),
        "sonar_url": SONAR_URL
    }
