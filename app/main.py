import os
import uuid
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
_selected_model: str = ""

# ---- Request models ----

class ChatMessage(BaseModel):
    message: str
    history: list[dict] = []


# ---- Tool definitions (passed to Ollama on every /chat so it can call them) ----
# IMPORTANT: Descriptions are read by the LLM to decide WHEN and HOW to call a tool.
# Vague descriptions = bad tool selection.

DEVOPS_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "kubectl_command",
            "description": (
                "Run a kubectl command on the Kubernetes cluster (Minikube). "
                "Use for: checking pod status, viewing logs, applying manifests, "
                "scaling deployments, describing resources, getting cluster info. "
                "Examples: 'get pods', 'get pods -n kube-system', 'describe deployment myapp', "
                "'get nodes', 'get services', 'logs pod-name'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "kubectl subcommand WITHOUT the 'kubectl' prefix. E.g. 'get pods -n default'"
                    },
                    "description": {
                        "type": "string",
                        "description": "Plain-English explanation of what this command does and why."
                    }
                },
                "required": ["command", "description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_ansible_playbook",
            "description": (
                "Generate and run an Ansible playbook to automate infrastructure tasks. "
                "Use for: deploying applications, configuring servers, installing packages, "
                "managing services. The playbook will be scanned for security issues "
                "before the user is asked to approve it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "playbook_content": {
                        "type": "string",
                        "description": "Complete YAML content of the Ansible playbook to run."
                    },
                    "description": {
                        "type": "string",
                        "description": "Plain-English explanation of what this playbook does."
                    }
                },
                "required": ["playbook_content", "description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_jenkins_job",
            "description": (
                "Trigger a Jenkins CI/CD build job. "
                "Use when the user wants to run a build, deploy, or test pipeline."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_name": {
                        "type": "string",
                        "description": "Exact name of the Jenkins job to trigger."
                    },
                    "description": {
                        "type": "string",
                        "description": "Plain-English explanation of what triggering this job will do."
                    }
                },
                "required": ["job_name", "description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "docker_operation",
            "description": (
                "Perform Docker operations: list containers/images, view logs, "
                "check resource usage. Use for inspecting Docker state."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "description": "One of: ps, images, logs, stats, pull, prune, inspect",
                        "enum": ["ps", "images", "logs", "stats", "pull", "prune", "inspect"]
                    },
                    "args": {
                        "type": "string",
                        "description": "Additional arguments, e.g. container name for logs.",
                        "default": ""
                    },
                    "description": {
                        "type": "string",
                        "description": "Plain-English explanation of what this does."
                    }
                },
                "required": ["operation", "description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "jenkins_status",
            "description": (
                "Check Jenkins health and job status without triggering anything. "
                "Use BEFORE triggering jobs to verify the job exists, "
                "or to show the user recent build results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "One of: list_jobs, job_status, build_log, health",
                        "enum": ["list_jobs", "job_status", "build_log", "health"]
                    },
                    "job_name": {
                        "type": "string",
                        "description": "Job name (required for job_status and build_log)",
                        "default": ""
                    },
                    "description": {
                        "type": "string",
                        "description": "Plain-English explanation of what this check will show."
                    }
                },
                "required": ["action", "description"]
            }
        }
    }
]


# ---- System prompt ----
# KEY: Do NOT tell the LLM it "cannot" execute. It should call tools freely.
# The approval gate is in the UI — the LLM's job is to choose the right tool.

SYSTEM_PROMPT = """You are DevGordon, a DevOps assistant for a local Kubernetes lab.

Your stack: Ansible, Jenkins, Minikube/kubectl, Docker, SonarQube.

When a user asks you to DO something with infrastructure, call the appropriate tool.
When a user asks a question that needs live data (pods, builds, images), call a tool to get it.
Do not explain how to do things manually — just call the tool.

The user will see an approval card before anything executes. You don't need to ask permission first.

Tools available:
- kubectl_command: run any kubectl command (get pods, describe, logs, etc.)
- run_ansible_playbook: generate and run Ansible playbooks
- trigger_jenkins_job: trigger a Jenkins build
- docker_operation: list containers, images, logs
- jenkins_status: check Jenkins health and job status

Be concise. If you're calling a tool, a brief one-sentence explanation is enough.
If you're answering a question (no tool needed), keep it short and practical."""


# ---- Routes ----

@app.get("/", response_class=HTMLResponse)
async def index():
    return (Path(__file__).parent / "static" / "index.html").read_text()


@app.get("/health")
async def health():
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


@app.get("/models")
async def list_models():
    global _selected_model
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
    except Exception as e:
        return {
            "error": str(e),
            "models": [],
            "selected_model": None,
            "message": "Ollama is not reachable."
        }

    if not models:
        return {
            "error": "no_models",
            "models": [],
            "selected_model": None,
            "message": "No models installed. Run: docker exec devgordon-ollama ollama pull llama3"
        }

    if not _selected_model or _selected_model not in models:
        _selected_model = models[0]

    return {"models": models, "selected_model": _selected_model}


@app.post("/select-model")
async def select_model(body: dict):
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


@app.post("/chat")
async def chat(msg: ChatMessage):
    """
    Send a message to Ollama WITH tools. If Ollama calls a tool,
    return a pending_approval card (with scan results) instead of plain text.
    """
    global _selected_model

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += msg.history
    messages.append({"role": "user", "content": msg.message})

    # Validate Ollama is up and has a model
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            available = [m["name"] for m in r.json().get("models", [])]
    except Exception as e:
        raise HTTPException(503, f"Cannot reach Ollama: {str(e)}")

    if not available:
        raise HTTPException(503, "No models installed. Run: ollama pull llama3")

    if not _selected_model or _selected_model not in available:
        _selected_model = available[0]

    payload = {
        "model": _selected_model,
        "messages": messages,
        "tools": DEVOPS_TOOLS,   # <-- THIS is the fix. Without this, LLM never uses tools.
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
    except httpx.ConnectError:
        raise HTTPException(503, "Ollama is not reachable.")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(503, f"Model '{_selected_model}' not found. Try: ollama pull {_selected_model}")
        raise HTTPException(503, f"Ollama error: {e.response.status_code}")

    response_message = data.get("message", {})
    tool_calls = response_message.get("tool_calls", [])

    # ---- Tool call: show approval card ----
    if tool_calls:
        tool_call = tool_calls[0]
        fn = tool_call.get("function", {})
        tool_name = fn.get("name", "")
        tool_args = fn.get("arguments", {})

        # The explanation comes from the text content alongside the tool call (if any)
        explanation = response_message.get("content") or tool_args.get("description") or f"I'll run `{tool_name}` for you."

        # Scan before showing the approval card
        from scanner import pre_scan
        scan_result = pre_scan(tool_name, tool_args)

        call_id = str(uuid.uuid4())[:8]

        _conversation_history.append({"role": "user", "content": msg.message})
        _conversation_history.append({"role": "assistant", "content": explanation})

        return {
            "type": "pending_approval",
            "tool_name": tool_name,
            "tool_args": tool_args,
            "explanation": explanation,
            "scan": {
                "status": scan_result.get("status", "skipped"),
                "issues": scan_result.get("issues", [])
            },
            "call_id": call_id,
            "model_used": _selected_model,
        }

    # ---- Plain text response ----
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
    """
    Execute a tool after the user approves it.
    Frontend sends: tool_name, tool_args, tool_call_id
    """
    import subprocess, tempfile, shlex

    # FIX: frontend sends "tool_name" and "tool_args", not "tool" and "args"
    tool_name = body.get("tool_name") or body.get("tool")
    tool_args = body.get("tool_args") or body.get("args", {})

    if not tool_name:
        raise HTTPException(400, "Missing tool_name")

    # ---- kubectl ----
    if tool_name == "kubectl_command":
        command = tool_args.get("command", "")
        args = ["kubectl"] + shlex.split(command)
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=30)
            output = result.stdout + ("\n" + result.stderr if result.stderr else "")
            return {"result": {"success": result.returncode == 0, "output": output or "(no output)"}}
        except FileNotFoundError:
            return {"result": {"success": False, "output": "kubectl not found. Is Minikube running?"}}
        except subprocess.TimeoutExpired:
            return {"result": {"success": False, "output": "kubectl timed out after 30s"}}

    # ---- ansible ----
    elif tool_name == "run_ansible_playbook":
        # Support both "playbook_content" (from tools.py) and "playbook_yaml" (legacy)
        playbook_content = tool_args.get("playbook_content") or tool_args.get("playbook_yaml", "")
        if not playbook_content:
            return {"result": {"success": False, "output": "No playbook content provided."}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False, prefix="/tmp/devgordon_") as f:
            f.write(playbook_content)
            tmp_path = f.name
        try:
            result = subprocess.run(
                ["ansible-playbook", tmp_path, "-i", "localhost,", "--connection=local"],
                capture_output=True, text=True, timeout=120
            )
            output = result.stdout + ("\n" + result.stderr if result.stderr else "")
            return {"result": {"success": result.returncode == 0, "output": output or "(no output)"}}
        except subprocess.TimeoutExpired:
            return {"result": {"success": False, "output": "ansible-playbook timed out after 120s"}}
        except FileNotFoundError:
            return {"result": {"success": False, "output": "ansible-playbook not found in container."}}
        finally:
            import os; os.unlink(tmp_path)

    # ---- jenkins trigger ----
    elif tool_name == "trigger_jenkins_job":
        job_name = tool_args.get("job_name", "")
        jenkins_user = os.getenv("JENKINS_USER", "admin")
        jenkins_token = os.getenv("JENKINS_TOKEN", "")
        if not jenkins_token:
            return {"result": {"success": False, "output": "JENKINS_TOKEN not set. Add it to .env and restart."}}
        url = f"{JENKINS_URL}/job/{job_name}/build"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(url, auth=(jenkins_user, jenkins_token))
            success = r.status_code in (200, 201)
            return {"result": {
                "success": success,
                "output": f"Build triggered (HTTP {r.status_code})" if success else f"Jenkins returned {r.status_code}: {r.text}"
            }}
        except Exception as e:
            return {"result": {"success": False, "output": str(e)}}

    # ---- docker ----
    elif tool_name == "docker_operation":
        operation = tool_args.get("operation", "ps")
        extra_args = tool_args.get("args", "").strip()
        op_map = {
            "ps":      ["docker", "ps", "-a"],
            "images":  ["docker", "images"],
            "logs":    ["docker", "logs"],
            "stats":   ["docker", "stats", "--no-stream"],
            "pull":    ["docker", "pull"],
            "prune":   ["docker", "system", "prune", "-f"],
            "inspect": ["docker", "inspect"],
        }
        cmd = op_map.get(operation, ["docker", operation])
        if extra_args:
            cmd += extra_args.split()
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            output = result.stdout + (result.stderr if result.stderr else "")
            return {"result": {"success": result.returncode == 0, "output": output or "(no output)"}}
        except FileNotFoundError:
            return {"result": {"success": False, "output": "Docker not found or not running."}}

    # ---- jenkins status (read-only) ----
    elif tool_name == "jenkins_status":
        from tools import execute_tool
        result = execute_tool(tool_name, tool_args)
        return {"result": result}

    else:
        raise HTTPException(400, f"Unknown tool: {tool_name}")


@app.post("/reject")
async def reject(body: dict):
    _conversation_history.append({
        "role": "assistant",
        "content": "Action rejected by user."
    })
    return {"status": "rejected"}


# ---- MCP Endpoints ----

@app.get("/mcp/tools")
async def mcp_list_tools():
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


# ---- Code Quality Scanning ----

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
        "can_approve": scan_result.get("status") not in ["error"]
    }


@app.get("/sonarqube-standards")
async def sonarqube_standards():
    from scanner import get_sonarqube_project_issues
    issues = get_sonarqube_project_issues()
    return {
        "status": issues.get("status"),
        "project": "devgordon",
        "recent_issues": issues.get("issues", []),
        "sonar_url": SONAR_URL
    }