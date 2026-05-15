"""
scanner.py — Pre-execution security/quality scanning
=====================================================
Runs BEFORE the approval card is shown. The user sees issues flagged
in the card itself, before deciding whether to approve.

Scanning tiers:
  1. FAST — ansible-lint, manual pattern checks (always runs, <2s)
  2. REAL-TIME SONAR — sonar-scanner CLI on generated code, results
     pulled from SonarQube API (~15-30s, runs if SONAR_TOKEN is set)

Why this matters:
  AI-generated code is known to be prone to security issues and poor
  design decisions. Running established, battle-tested static analysis
  tools (ansible-lint, SonarQube) on that output before a human approves
  it is the core safety proposition of this project.

  Most tools run SonarQube on committed code. This runs it on
  AI-generated code before it ever touches the filesystem permanently.
"""

import subprocess
import tempfile
import os
import re
import time
import shutil
import httpx

SONAR_URL = os.getenv("SONAR_URL", "http://sonarqube:9000")
SONAR_TOKEN = os.getenv("SONAR_TOKEN", "")
WORKSPACE_ROOT = os.getenv("WORKSPACE", "/workspace")
SONAR_PRESCAN_KEY = "devgordon-prescan"
SONAR_PRESCAN_DIR = os.path.join(WORKSPACE_ROOT, "tmp", "generated")


def pre_scan(tool_name: str, tool_args: dict) -> dict:
    if tool_name == "run_ansible_playbook":
        return _scan_ansible(tool_args.get("playbook_content", ""))
    elif tool_name == "write_workspace_file":
        return _scan_workspace_write(
            tool_args.get("path", ""), tool_args.get("content", "")
        )
    elif tool_name == "kubectl_command":
        return _validate_kubectl(tool_args.get("command", ""))
    elif tool_name == "docker_operation":
        return _validate_docker(tool_args)
    else:
        return {
            "status": "skipped",
            "issues": ["No scanner for this tool type"],
            "sources": [],
        }


# ── Ansible ───────────────────────────────────────────────────────────────────


def _scan_ansible(content: str) -> dict:
    if not content.strip():
        return {
            "status": "error",
            "issues": ["Empty playbook content"],
            "sources": ["validation"],
        }
    issues = []
    sources = []
    r = _manual_playbook_scan(content)
    issues.extend(r.get("issues", []))
    sources.append("pattern-scan")
    if _command_exists("ansible-lint"):
        r = _run_ansible_lint(content)
        issues.extend(r.get("issues", []))
        sources.append("ansible-lint")
    if SONAR_TOKEN:
        r = _sonarqube_scan_content(content, "playbook.yml", language="yaml")
        issues.extend(r.get("issues", []))
        if r.get("status") != "skipped":
            sources.append("sonarqube")
    return _aggregate(issues, sources)


def _scan_workspace_write(path: str, content: str) -> dict:
    if not content.strip():
        return {"status": "skipped", "issues": [], "sources": []}
    ext = os.path.splitext(path)[1].lower()
    issues = []
    sources = []
    if ext in (".yml", ".yaml"):
        r = _manual_playbook_scan(content)
        issues.extend(r.get("issues", []))
        sources.append("pattern-scan")
        if SONAR_TOKEN:
            r = _sonarqube_scan_content(
                content, os.path.basename(path), language="yaml"
            )
            issues.extend(r.get("issues", []))
            if r.get("status") != "skipped":
                sources.append("sonarqube")
    elif ext == ".py":
        if SONAR_TOKEN:
            r = _sonarqube_scan_content(content, os.path.basename(path), language="py")
            issues.extend(r.get("issues", []))
            if r.get("status") != "skipped":
                sources.append("sonarqube")
    elif ext in (".sh", ".bash"):
        issues.extend(_scan_shell(content))
        sources.append("pattern-scan")
    return _aggregate(issues, sources)


# ── SonarQube real-time pre-scan ──────────────────────────────────────────────


def _sonarqube_scan_content(content: str, filename: str, language: str = "py") -> dict:
    """
    Write generated content to /workspace/tmp/generated/, run sonar-scanner,
    poll until complete, return issues. This is what makes the claim real:
    SonarQube runs on AI-generated code before it is approved.
    """
    if not SONAR_TOKEN:
        return {"status": "skipped", "issues": ["SONAR_TOKEN not set"]}
    if not _command_exists("sonar-scanner"):
        return {"status": "skipped", "issues": ["sonar-scanner not installed"]}

    try:
        r = httpx.get(f"{SONAR_URL}/api/system/status", timeout=3)
        if r.json().get("status") != "UP":
            return {"status": "skipped", "issues": ["SonarQube not ready"]}
    except Exception:
        return {
            "status": "skipped",
            "issues": [f"SonarQube unreachable at {SONAR_URL}"],
        }

    _ensure_sonar_project(SONAR_PRESCAN_KEY, "DevGordon Pre-scan")

    os.makedirs(SONAR_PRESCAN_DIR, exist_ok=True)
    scan_file = os.path.join(SONAR_PRESCAN_DIR, filename)
    props_path = os.path.join(SONAR_PRESCAN_DIR, "sonar-project.properties")

    with open(scan_file, "w") as f:
        f.write(content)

    lang_extra = "sonar.python.version=3.11" if language == "py" else ""
    with open(props_path, "w") as f:
        f.write(f"""sonar.projectKey={SONAR_PRESCAN_KEY}
sonar.projectName=DevGordon Pre-scan
sonar.sources=.
sonar.language={language}
sonar.host.url={SONAR_URL}
sonar.token={SONAR_TOKEN}
sonar.scm.disabled=true
sonar.sourceEncoding=UTF-8
{lang_extra}
""")

    try:
        result = subprocess.run(
            ["sonar-scanner", f"-Dproject.settings={props_path}"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=SONAR_PRESCAN_DIR,
        )
        if result.returncode != 0:
            snippet = (result.stderr or result.stdout)[-400:]
            return {"status": "skipped", "issues": [f"sonar-scanner error: {snippet}"]}

        task_id = None
        for line in result.stdout.splitlines():
            if "ceTaskId=" in line:
                task_id = line.split("ceTaskId=")[-1].strip()
                break

        if task_id:
            return _poll_and_fetch(task_id, SONAR_PRESCAN_KEY)
        return _fetch_sonar_issues(SONAR_PRESCAN_KEY)

    except subprocess.TimeoutExpired:
        return {"status": "skipped", "issues": ["sonar-scanner timed out"]}
    finally:
        for p in (scan_file, props_path):
            try:
                os.remove(p)
            except Exception:
                pass


def _ensure_sonar_project(key: str, name: str):
    try:
        httpx.post(
            f"{SONAR_URL}/api/projects/create",
            params={"projectKey": key, "name": name},
            auth=(SONAR_TOKEN, ""),
            timeout=5,
        )
    except Exception:
        pass


def _poll_and_fetch(task_id: str, project_key: str, max_wait: int = 30) -> dict:
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            r = httpx.get(
                f"{SONAR_URL}/api/ce/task",
                params={"id": task_id},
                auth=(SONAR_TOKEN, ""),
                timeout=5,
            )
            status = r.json().get("task", {}).get("status")
            if status == "SUCCESS":
                return _fetch_sonar_issues(project_key)
            elif status in ("FAILED", "CANCELLED"):
                return {
                    "status": "skipped",
                    "issues": [f"SonarQube task {status.lower()}"],
                }
            time.sleep(3)
        except Exception:
            break
    return {"status": "skipped", "issues": ["SonarQube did not complete in time"]}


def _fetch_sonar_issues(project_key: str) -> dict:
    try:
        r = httpx.get(
            f"{SONAR_URL}/api/issues/search",
            params={
                "componentKeys": project_key,
                "resolved": "false",
                "ps": 15,
                "severities": "BLOCKER,CRITICAL,MAJOR,MINOR",
            },
            auth=(SONAR_TOKEN, ""),
            timeout=10,
        )
        issues = []
        for i in r.json().get("issues", []):
            sev = i.get("severity", "INFO")
            icon = {"BLOCKER": "🚨", "CRITICAL": "🚨", "MAJOR": "⚠", "MINOR": "ℹ"}.get(
                sev, "ℹ"
            )
            issues.append(
                f"{icon} [{sev}] line {i.get('line', '?')}: {i.get('message', '')} ({i.get('rule', '')})"
            )
        return {"status": "sonar_complete", "issues": issues}
    except Exception as e:
        return {"status": "skipped", "issues": [f"Could not fetch issues: {e}"]}


def get_sonarqube_project_issues() -> dict:
    if not SONAR_TOKEN:
        return {"status": "skipped", "issues": []}
    try:
        r = httpx.get(
            f"{SONAR_URL}/api/issues/search",
            params={"componentKeys": "devgordon", "pageSize": 10},
            auth=(SONAR_TOKEN, ""),
            timeout=10,
        )
        if r.status_code == 200:
            return {
                "status": "ok",
                "issues": [
                    {
                        "type": i.get("type"),
                        "severity": i.get("severity"),
                        "message": i.get("message"),
                        "component": i.get("component"),
                    }
                    for i in r.json().get("issues", [])[:5]
                ],
            }
    except Exception:
        pass
    return {"status": "error", "issues": []}


# ── ansible-lint ─────────────────────────────────────────────────────────────


def _run_ansible_lint(content: str) -> dict:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write(content)
        path = f.name
    try:
        result = subprocess.run(
            ["ansible-lint", path, "--parseable", "--nocolor"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        issues = []
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                clean = re.sub(r"^.*?\.yml:\d+:\s*", "", line).strip()
                if clean:
                    issues.append(clean)
        status = (
            "error" if result.returncode >= 2 else ("warning" if issues else "clean")
        )
        return {"status": status, "issues": issues[:10]}
    except subprocess.TimeoutExpired:
        return {"status": "skipped", "issues": ["ansible-lint timed out"]}
    finally:
        os.unlink(path)


def _manual_playbook_scan(content: str) -> dict:
    issues = []
    if re.search(r'mode:\s*["\']?0?777', content):
        issues.append("⚠ World-writable file permission (mode: 777) — security risk")
    if re.search(r"url:\s*http://", content) or re.search(r"get_url.*http://", content):
        issues.append("⚠ HTTP URL found — use HTTPS to prevent MITM attacks")
    if re.search(r"(shell|command):\s*.*\{\{.*\}\}", content):
        issues.append(
            "⚠ Shell/command task uses template variables — verify input is sanitised"
        )
    if (
        "become: yes" in content or "become: true" in content
    ) and "become_user" not in content:
        issues.append("⚠ become: yes without become_user — will run as root")
    if "ignore_errors: yes" in content or "ignore_errors: true" in content:
        issues.append("ℹ ignore_errors used — failures will be silently skipped")
    if (
        re.search(r"(password|secret|token|key).*:", content, re.IGNORECASE)
        and "no_log" not in content
    ):
        issues.append("ℹ Task may handle secrets — consider no_log: true")
    if not issues:
        return {"status": "clean", "issues": []}
    return {
        "status": "warning" if any("⚠" in i for i in issues) else "info",
        "issues": issues,
    }


# ── Shell scan ────────────────────────────────────────────────────────────────


def _scan_shell(content: str) -> list:
    issues = []
    if "rm -rf /" in content:
        issues.append("🚨 rm -rf / — would delete the entire filesystem")
    if re.search(r"curl.*\|\s*(bash|sh)", content):
        issues.append("⚠ Piping curl output to shell — verify the URL is trusted")
    if "chmod 777" in content:
        issues.append("⚠ chmod 777 — world-writable permissions")
    if re.search(r'password\s*=\s*["\'][^"\']+["\']', content, re.IGNORECASE):
        issues.append("⚠ Hardcoded password detected")
    return issues


# ── kubectl & docker validation ───────────────────────────────────────────────


def _validate_kubectl(command: str) -> dict:
    issues = []
    cmd_lower = command.lower().strip()
    if cmd_lower.startswith("delete"):
        issues.append("⚠ This will DELETE a resource — irreversible without backups")
    if "delete namespace" in cmd_lower:
        issues.append("🚨 DELETING A NAMESPACE removes ALL resources inside it")
    if "--force" in cmd_lower:
        issues.append("⚠ --force bypasses graceful termination")
    if "--all" in cmd_lower and "delete" in cmd_lower:
        issues.append("🚨 Deleting ALL resources in the namespace")
    return {
        "status": "warning" if issues else "clean",
        "issues": issues,
        "sources": ["validation"],
    }


def _validate_docker(args: dict) -> dict:
    issues = []
    if args.get("operation") == "prune":
        issues.append(
            "⚠ docker system prune removes all stopped containers, dangling images, and unused networks"
        )
    return {
        "status": "warning" if issues else "clean",
        "issues": issues,
        "sources": ["validation"],
    }


# ── Helpers ───────────────────────────────────────────────────────────────────


def _aggregate(issues: list, sources: list) -> dict:
    if not issues:
        status = "clean"
    elif any("🚨" in i for i in issues):
        status = "error"
    elif any("⚠" in i for i in issues):
        status = "warning"
    else:
        status = "info"
    return {
        "status": status,
        "issues": issues,
        "sources": sources,
        "can_approve": status != "error",
    }


def _command_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None
