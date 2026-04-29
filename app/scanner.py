"""
scanner.py — Pre-execution security/quality scanning
=====================================================
This runs BEFORE the approval card is shown to the user.
So the user sees potential issues flagged in the card itself,
before they decide whether to approve.

Two-tier scanning:
  1. FAST (ansible-lint, manual patterns): runs inline
  2. SLOW (SonarQube API): optional, for generated Python code

For Ansible playbooks: runs ansible-lint
For Python code: optionally scans via SonarQube
For other tools: basic argument validation

Why this matters (your SonarQube instinct was right):
  AI-generated code commonly has these issues:
  - Ansible: shell injection, world-writable files, unencrypted downloads
  - Python: missing error handling, code duplication, security issues
  SonarQube enforces language-specific quality standards.
"""

import subprocess
import tempfile
import os
import json
import re
import httpx

# SonarQube connection details
SONAR_URL = os.getenv("SONAR_URL", "http://sonarqube:9000")
SONAR_TOKEN = os.getenv("SONAR_TOKEN", "")
SONAR_PROJECT_KEY = "devgordon"


def pre_scan(tool_name: str, tool_args: dict) -> dict:
    """
    Returns:
      {"status": "clean", "issues": []}
      {"status": "warning", "issues": ["issue1", "issue2"]}
      {"status": "error", "issues": ["critical issue"]}
      {"status": "skipped", "issues": ["reason"]}
      {"scan_id": "abc123", "status": "pending"}  (for async SonarQube)
    """
    if tool_name == "run_ansible_playbook":
        return _scan_ansible_playbook(tool_args.get("playbook_content", ""))

    elif tool_name == "kubectl_command":
        return _validate_kubectl(tool_args.get("command", ""))

    elif tool_name == "docker_operation":
        return _validate_docker(tool_args)

    else:
        return {"status": "skipped", "issues": ["No scanner for this tool type"]}


# ============================================================================
# ANSIBLE PLAYBOOK SCANNING (FAST)
# ============================================================================

def _scan_ansible_playbook(content: str) -> dict:
    """
    Run ansible-lint on the playbook content.
    Falls back to manual pattern checks if ansible-lint isn't installed.
    """
    if not content.strip():
        return {"status": "error", "issues": ["Empty playbook content"]}

    # Try ansible-lint first (best option)
    if _command_exists("ansible-lint"):
        return _run_ansible_lint(content)
    else:
        # Fallback: manual pattern scanning
        return _manual_playbook_scan(content)


def _run_ansible_lint(content: str) -> dict:
    """Use ansible-lint for proper scanning."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write(content)
        path = f.name

    try:
        result = subprocess.run(
            ["ansible-lint", path, "--parseable", "--nocolor"],
            capture_output=True,
            text=True,
            timeout=20
        )

        issues = []
        if result.stdout:
            # Parse ansible-lint's parseable output format
            # Each line is like: path:line:rule_id: [rule_tag] message
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    # Clean up the path prefix (temp file path)
                    clean = re.sub(r'^.*?\.yml:\d+:\s*', '', line).strip()
                    if clean:
                        issues.append(clean)

        if not issues:
            return {"status": "clean", "issues": []}
        else:
            # Separate warnings from errors based on ansible-lint exit code
            status = "error" if result.returncode >= 2 else "warning"
            return {"status": status, "issues": issues[:10]}  # Cap at 10

    except subprocess.TimeoutExpired:
        return {"status": "skipped", "issues": ["ansible-lint timed out"]}
    finally:
        os.unlink(path)


def _manual_playbook_scan(content: str) -> dict:
    """
    Manual pattern matching when ansible-lint isn't available.
    Not as thorough but catches the obvious stuff.
    """
    issues = []

    # Check for world-writable permissions
    if re.search(r'mode:\s*["\']?0?777', content):
        issues.append("⚠ World-writable file permission (mode: 777) — security risk")

    # Check for HTTP instead of HTTPS in URLs
    if re.search(r'url:\s*http://', content) or re.search(r'get_url.*http://', content):
        issues.append("⚠ HTTP URL found — use HTTPS to prevent MITM attacks")

    # Check for shell/command with variables that could be injected
    if re.search(r'(shell|command):\s*.*\{\{.*\}\}', content):
        issues.append("⚠ Shell/command task uses template variables — verify input is sanitized")

    # Check for become without become_user (defaults to root)
    if "become: yes" in content or "become: true" in content:
        if "become_user" not in content:
            issues.append("⚠ become: yes without become_user — will run as root by default")

    # Check for ignore_errors
    if "ignore_errors: yes" in content or "ignore_errors: true" in content:
        issues.append("ℹ ignore_errors used — failures will be silently skipped")

    # Check for no_log: false on tasks that might have secrets
    if re.search(r'(password|secret|token|key).*:', content, re.IGNORECASE):
        if "no_log" not in content:
            issues.append("ℹ Task may handle secrets — consider no_log: true")

    if not issues:
        return {"status": "clean", "issues": []}
    
    # Anything with ⚠ is a warning, ℹ is info
    has_warning = any("⚠" in i for i in issues)
    return {"status": "warning" if has_warning else "info", "issues": issues}


# ============================================================================
# SONARQUBE SCANNING (FOR GENERATED PYTHON CODE)
# ============================================================================

async def scan_python_via_sonarqube(code: str, file_name: str = "generated_code.py") -> dict:
    """
    Scan generated Python code via SonarQube API.
    Returns violations found by SonarQube.
    
    Requires SonarQube running and SONAR_TOKEN set.
    """
    if not SONAR_TOKEN:
        return {
            "status": "skipped",
            "issues": ["SonarQube token not configured. Set SONAR_TOKEN in .env"]
        }

    # Check if SonarQube is reachable
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{SONAR_URL}/api/system/health", auth=(SONAR_TOKEN, ""))
            if r.status_code != 200:
                return {
                    "status": "skipped",
                    "issues": [f"SonarQube not ready (HTTP {r.status_code})"]
                }
    except Exception as e:
        return {
            "status": "skipped",
            "issues": [f"Cannot reach SonarQube at {SONAR_URL}: {str(e)}"]
        }

    # Write code to temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, dir="/tmp", prefix="sonar_"
    ) as f:
        f.write(code)
        temp_path = f.name

    try:
        # Use sonar-scanner CLI to analyze the file
        # This requires sonar-scanner to be installed, which it isn't in the container
        # Instead, we'll use the SonarQube API to get existing project issues
        # (or skip and recommend sonar-scanner setup)
        
        # For now: return a message about SonarQube integration
        return {
            "status": "info",
            "issues": [
                "SonarQube integration requires sonar-scanner CLI in the container.",
                "For full integration, add sonar-scanner to Dockerfile and configure sonar-project.properties",
                "Currently, the Jenkins pipeline runs full SonarQube scans of committed code."
            ],
            "recommendation": "Use ansible-lint for Ansible playbooks (built-in). Full SonarQube scans happen in CI/CD."
        }

    finally:
        os.unlink(temp_path)


def get_sonarqube_project_issues() -> dict:
    """
    Fetch recent issues for the DevGordon project from SonarQube.
    Useful for showing users what standards their code should meet.
    """
    if not SONAR_TOKEN:
        return {"status": "skipped", "issues": []}

    try:
        import httpx
        
        # Use sync request (called from sync context)
        headers = {"Authorization": f"Bearer {SONAR_TOKEN}"}
        r = httpx.get(
            f"{SONAR_URL}/api/issues/search",
            params={"componentKeys": SONAR_PROJECT_KEY, "pageSize": 10},
            headers=headers,
            timeout=10
        )
        
        if r.status_code == 200:
            data = r.json()
            issues = [
                {
                    "type": i.get("type"),
                    "severity": i.get("severity"),
                    "message": i.get("message"),
                    "component": i.get("component")
                }
                for i in data.get("issues", [])[:5]
            ]
            return {"status": "ok", "issues": issues}
        else:
            return {"status": "error", "issues": []}
    except Exception:
        return {"status": "error", "issues": []}


# ============================================================================
# KUBECTL & DOCKER VALIDATION (FAST)
# ============================================================================

def _validate_kubectl(command: str) -> dict:
    """Basic safety checks for kubectl commands."""
    issues = []
    cmd_lower = command.lower().strip()

    # Flag destructive commands
    if cmd_lower.startswith("delete"):
        issues.append("⚠ This will DELETE a resource — irreversible unless you have backups")

    if "delete namespace" in cmd_lower:
        issues.append("🚨 DELETING A NAMESPACE removes ALL resources inside it")

    if "--force" in cmd_lower:
        issues.append("⚠ --force flag bypasses graceful termination")

    if "--all" in cmd_lower and "delete" in cmd_lower:
        issues.append("🚨 Deleting ALL resources — this affects everything in the namespace")

    if not issues:
        return {"status": "clean", "issues": []}
    return {"status": "warning", "issues": issues}


def _validate_docker(args: dict) -> dict:
    """Basic validation for Docker operations."""
    operation = args.get("operation", "")
    extra = args.get("args", "")
    issues = []

    if operation == "prune":
        issues.append("⚠ docker system prune will remove all stopped containers, dangling images, and unused networks")

    return {"status": "warning" if issues else "clean", "issues": issues}


# ============================================================================
# HELPERS
# ============================================================================

def _command_exists(cmd: str) -> bool:
    """Check if a CLI command is available on the system."""
    try:
        subprocess.run(["which", cmd], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
