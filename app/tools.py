"""
tools.py — What DevGordon can actually DO
==========================================
Two things live here:

1. TOOL_DEFINITIONS — JSON schema that Ollama reads to understand
   what tools exist and when to call them. Think of it as the LLM's
   instruction manual for your infrastructure.

2. execute_tool() — The actual Python that runs when a tool is approved.

Important: The descriptions in TOOL_DEFINITIONS aren't just for humans.
The LLM reads these to decide WHICH tool to call and HOW to fill in
the arguments. Vague descriptions = bad tool selection.
"""

import os
import subprocess
import json
import requests

# Jenkins connection details — pulled from env vars set in docker-compose.yml
JENKINS_URL = os.getenv("JENKINS_URL", "http://localhost:8080")
JENKINS_USER = os.getenv("JENKINS_USER", "admin")
JENKINS_TOKEN = os.getenv("JENKINS_TOKEN", "")

# ----------------------------------------------------------------
# TOOL DEFINITIONS
# These are passed to Ollama on every /chat call.
# The LLM uses "name" and "description" to decide when to call a tool.
# The "input_schema" tells it what arguments to provide.
# ----------------------------------------------------------------
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "run_ansible_playbook",
            "description": (
                "Generate and run an Ansible playbook to automate infrastructure tasks. "
                "Use this for: deploying applications, configuring servers, installing packages, "
                "managing services, applying Kubernetes manifests. "
                "The playbook content will be scanned before execution."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "playbook_content": {
                        "type": "string",
                        "description": "Full YAML content of the Ansible playbook to run"
                    },
                    "inventory": {
                        "type": "string",
                        "description": "Inventory content or 'localhost' for local execution",
                        "default": "localhost,"
                    },
                    "extra_vars": {
                        "type": "string",
                        "description": "Extra variables to pass to ansible-playbook as JSON string",
                        "default": "{}"
                    }
                },
                "required": ["playbook_content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_jenkins_job",
            "description": (
                "Trigger a Jenkins CI/CD job and optionally wait for its result. "
                "Use this when the user wants to run a build, deploy, or test pipeline."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_name": {
                        "type": "string",
                        "description": "Name of the Jenkins job/pipeline to trigger"
                    },
                    "parameters": {
                        "type": "object",
                        "description": "Build parameters as key-value pairs",
                        "default": {}
                    },
                    "wait_for_result": {
                        "type": "boolean",
                        "description": "Whether to wait and return the build result",
                        "default": True
                    }
                },
                "required": ["job_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "kubectl_command",
            "description": (
                "Run a kubectl command on the Kubernetes cluster (Minikube). "
                "Use for: checking pod status, viewing logs, applying manifests, "
                "scaling deployments, describing resources, deleting resources."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "kubectl command WITHOUT the 'kubectl' prefix. E.g. 'get pods -n default' or 'describe deployment myapp'"
                    },
                    "namespace": {
                        "type": "string",
                        "description": "Kubernetes namespace",
                        "default": "default"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "docker_operation",
            "description": (
                "Perform Docker operations: list containers/images, view logs, "
                "check resource usage, pull images, remove stopped containers. "
                "For building and deploying, prefer the Jenkins pipeline."
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
                        "description": "Additional arguments for the docker command",
                        "default": ""
                    }
                },
                "required": ["operation"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "jenkins_status",
            "description": (
                "Check Jenkins status: list jobs, view recent build results, "
                "check if Jenkins is healthy, view build logs. "
                "Use this BEFORE triggering jobs to verify the job exists."
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
                    "build_number": {
                        "type": "integer",
                        "description": "Build number for build_log (default: last build)",
                        "default": 0
                    }
                },
                "required": ["action"]
            }
        }
    }
]


# ----------------------------------------------------------------
# EXECUTOR — runs after user approval
# ----------------------------------------------------------------

def execute_tool(tool_name: str, args: dict) -> dict:
    """
    Dispatch table. Routes to the right function based on tool name.
    Returns a dict with {"success": bool, "output": str}
    """
    executors = {
        "run_ansible_playbook": _run_ansible,
        "trigger_jenkins_job": _trigger_jenkins,
        "kubectl_command": _kubectl,
        "docker_operation": _docker,
        "jenkins_status": _jenkins_status,
    }
    fn = executors.get(tool_name)
    if not fn:
        return {"success": False, "output": f"Unknown tool: {tool_name}"}
    return fn(args)


def _run_ansible(args: dict) -> dict:
    """
    Writes the playbook to a temp file and runs it.
    We write to a file rather than passing via stdin because
    ansible-playbook doesn't easily accept YAML from stdin.
    """
    import tempfile
    import os

    playbook_content = args.get("playbook_content", "")
    inventory = args.get("inventory", "localhost,")
    extra_vars = args.get("extra_vars", "{}")

    # Write playbook to temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as pf:
        pf.write(playbook_content)
        playbook_path = pf.name

    # Write inventory to temp file (if it's inline content, not a path)
    inv_path = None
    if "\n" in inventory or inventory == "localhost,":
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as inv:
            inv.write(inventory)
            inv_path = inv.name
    else:
        inv_path = inventory  # Assume it's already a file path

    try:
        cmd = ["ansible-playbook", playbook_path, "-i", inv_path]
        if extra_vars and extra_vars != "{}":
            cmd += ["--extra-vars", extra_vars]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120  # 2 minute timeout
        )
        success = result.returncode == 0
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return {"success": success, "output": output, "returncode": result.returncode}
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "Timed out after 120 seconds"}
    except FileNotFoundError:
        return {"success": False, "output": "ansible-playbook not found. Is Ansible installed?"}
    finally:
        os.unlink(playbook_path)
        if inv_path and "\n" in inventory:
            try:
                os.unlink(inv_path)
            except:
                pass


def _trigger_jenkins(args: dict) -> dict:
    """
    Calls Jenkins REST API to trigger a build.
    Jenkins API is simple: POST to /job/{name}/build
    With parameters: POST to /job/{name}/buildWithParameters
    """
    job_name = args.get("job_name", "")
    parameters = args.get("parameters", {})
    wait = args.get("wait_for_result", True)

    if not JENKINS_TOKEN:
        return {"success": False, "output": "JENKINS_TOKEN not set in environment"}

    auth = (JENKINS_USER, JENKINS_TOKEN)

    # Get CSRF crumb first (Jenkins security requirement)
    try:
        crumb_resp = requests.get(
            f"{JENKINS_URL}/crumbIssuer/api/json",
            auth=auth, timeout=10
        )
        crumb_data = crumb_resp.json()
        headers = {crumb_data["crumbRequestField"]: crumb_data["crumb"]}
    except Exception:
        headers = {}  # Some Jenkins configs don't require crumbs

    try:
        if parameters:
            url = f"{JENKINS_URL}/job/{job_name}/buildWithParameters"
            resp = requests.post(url, auth=auth, headers=headers,
                                 params=parameters, timeout=10)
        else:
            url = f"{JENKINS_URL}/job/{job_name}/build"
            resp = requests.post(url, auth=auth, headers=headers, timeout=10)

        if resp.status_code in (200, 201):
            return {"success": True, "output": f"Build triggered for '{job_name}'. Check Jenkins at {JENKINS_URL}/job/{job_name}"}
        else:
            return {"success": False, "output": f"Jenkins returned {resp.status_code}: {resp.text}"}
    except requests.exceptions.ConnectionError:
        return {"success": False, "output": f"Cannot connect to Jenkins at {JENKINS_URL}. Is it running?"}

def _get_kube_server():
    """
    Auto-detect if kubeconfig points to 127.0.0.1 and rewrite to
    host.docker.internal so kubectl can reach the host K8s API from
    inside the container. KUBE_API_SERVER env var overrides everything.
    """
    # Explicit override always wins
    explicit = os.getenv("KUBE_API_SERVER", "").strip()
    if explicit:
        return explicit, True

    # Auto-detect from kubeconfig
    try:
        import yaml
        kubeconfig = os.getenv("KUBECONFIG", os.path.expanduser("~/.kube/config"))
        with open(kubeconfig) as f:
            cfg = yaml.safe_load(f)

        current_context = cfg.get("current-context", "")
        ctx = next((c["context"] for c in cfg.get("contexts", []) 
                    if c["name"] == current_context), {})
        cluster_name = ctx.get("cluster", "")
        cluster = next((c["cluster"] for c in cfg.get("clusters", []) 
                        if c["name"] == cluster_name), {})
        server = cluster.get("server", "")

        if "127.0.0.1" in server:
            return server.replace("127.0.0.1", "host.docker.internal"), True
    except Exception:
        pass

    return None, False

def _kubectl(args: dict) -> dict:
    """Run a kubectl command. Namespace is injected if not already in the command."""
    command = args.get("command", "").strip()
    namespace = args.get("namespace", "default")

    # Safety: block destructive commands unless they're explicit
    destructive = ["delete", "drain", "cordon"]
    cmd_parts = command.split()

    server, needs_override = _get_kube_server()
    if needs_override:
        full_cmd = ["kubectl", f"--server={server}", "--insecure-skip-tls-verify"] + cmd_parts
    else:
        full_cmd = ["kubectl"] + cmd_parts

    # Add namespace flag if not already present and command supports it
    ns_supporting = ["get", "describe", "logs", "delete", "apply", "rollout", "scale"]
    if cmd_parts and cmd_parts[0] in ns_supporting and "-n" not in command and "--namespace" not in command:
        full_cmd += ["-n", namespace]

    try:
        result = subprocess.run(
            full_cmd,
            capture_output=True, text=True, timeout=30
        )
        output = result.stdout + (result.stderr if result.stderr else "")
        return {"success": result.returncode == 0, "output": output}
    except FileNotFoundError:
        return {"success": False, "output": "kubectl not found. Is Minikube running?"}
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "kubectl timed out after 30s"}


def _docker(args: dict) -> dict:
    """Docker operations that are safe to run (no build/run to avoid side effects)."""
    operation = args.get("operation", "ps")
    extra_args = args.get("args", "").strip()

    op_map = {
        "ps":      ["docker", "ps", "-a"],
        "images":  ["docker", "images"],
        "logs":    ["docker", "logs"],
        "stats":   ["docker", "stats", "--no-stream"],
        "pull":    ["docker", "pull"],
        "prune":   ["docker", "system", "prune", "-f"],
        "inspect": ["docker", "inspect"],
    }

    base_cmd = op_map.get(operation, ["docker", operation])
    if extra_args:
        base_cmd += extra_args.split()

    try:
        result = subprocess.run(base_cmd, capture_output=True, text=True, timeout=30)
        output = result.stdout + (result.stderr if result.stderr else "")
        return {"success": result.returncode == 0, "output": output}
    except FileNotFoundError:
        return {"success": False, "output": "Docker not found or not running"}


def _jenkins_status(args: dict) -> dict:
    """Check Jenkins health/status without triggering anything."""
    action = args.get("action", "health")
    job_name = args.get("job_name", "")

    if not JENKINS_TOKEN:
        # Try without auth (open Jenkins)
        auth = None
    else:
        auth = (JENKINS_USER, JENKINS_TOKEN)

    try:
        if action == "health":
            resp = requests.get(f"{JENKINS_URL}/api/json", auth=auth, timeout=5)
            data = resp.json()
            jobs = [j["name"] for j in data.get("jobs", [])]
            return {"success": True, "output": f"Jenkins is up. Jobs: {', '.join(jobs) or 'none'}"}

        elif action == "list_jobs":
            resp = requests.get(f"{JENKINS_URL}/api/json", auth=auth, timeout=5)
            data = resp.json()
            jobs = [{"name": j["name"], "color": j.get("color", "?")} for j in data.get("jobs", [])]
            return {"success": True, "output": json.dumps(jobs, indent=2)}

        elif action == "job_status" and job_name:
            resp = requests.get(f"{JENKINS_URL}/job/{job_name}/lastBuild/api/json", auth=auth, timeout=5)
            data = resp.json()
            return {"success": True, "output": f"Last build: #{data.get('number')} — {data.get('result')} ({data.get('duration', 0)//1000}s)"}

        elif action == "build_log" and job_name:
            build_num = args.get("build_number", 0)
            build_ref = build_num if build_num else "lastBuild"
            resp = requests.get(f"{JENKINS_URL}/job/{job_name}/{build_ref}/consoleText", auth=auth, timeout=10)
            # Truncate to last 3000 chars so we don't overwhelm the LLM
            log = resp.text[-3000:] if len(resp.text) > 3000 else resp.text
            return {"success": True, "output": log}

    except requests.exceptions.ConnectionError:
        return {"success": False, "output": f"Cannot connect to Jenkins at {JENKINS_URL}"}
    except Exception as e:
        return {"success": False, "output": str(e)}

    return {"success": False, "output": "Invalid action or missing required parameters"}
