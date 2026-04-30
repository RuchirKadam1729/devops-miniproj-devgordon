#!/usr/bin/env python3
"""
devgordon_showcase.py
─────────────────────
Fires real prompts at DevGordon, auto-executes every tool call (mode=never),
pretty-prints results, and scores each scenario so the whole capability
surface is on full display in one terminal run.

Usage:
    python devgordon_showcase.py
    python devgordon_showcase.py --url http://localhost:8000
    python devgordon_showcase.py --pause   # wait for Enter between scenarios
"""

import argparse
import json
import sys
import time
import textwrap
import httpx

# ── Terminal colours ──────────────────────────────────────────────────────────
R  = "\033[0;31m"
G  = "\033[0;32m"
Y  = "\033[0;33m"
B  = "\033[0;34m"
P  = "\033[0;35m"
C  = "\033[0;36m"
W  = "\033[1;37m"
DIM = "\033[2m"
RST = "\033[0m"

def hdr(title): print(f"\n{W}{'─'*70}{RST}\n{W}{title}{RST}\n{'─'*70}")
def ok(s):      print(f"  {G}✔{RST} {s}")
def warn(s):    print(f"  {Y}⚠{RST}  {s}")
def fail(s):    print(f"  {R}✗{RST} {s}")
def info(s):    print(f"  {DIM}{s}{RST}")
def label(k,v): print(f"  {C}{k:18}{RST} {v}")

def wrap(text, width=66, indent="    "):
    if not text:
        return indent + "(empty)"
    lines = []
    for para in str(text).splitlines():
        if para.strip():
            lines.extend(textwrap.wrap(para, width, initial_indent=indent, subsequent_indent=indent))
        else:
            lines.append("")
    return "\n".join(lines)

# ── Scenario definitions ──────────────────────────────────────────────────────
#
# Each scenario has:
#   prompt      – what the user types
#   expect_tool – tool name we expect DevGordon to call (None = plain text ok)
#   desc        – human description shown in the header
#   eval_fn     – optional callable(response_dict) → (passed: bool, note: str)
#
def _used_tool(data, name):
    """True if the response shows the named tool was called."""
    return data.get("tool_name") == name or (
        data.get("type") == "auto_executed" and data.get("tool_name") == name
    )

def _has_output(data):
    result = data.get("result", {})
    return bool(result.get("output", "").strip())

def _success(data):
    result = data.get("result", {})
    return result.get("success", False)


SCENARIOS = [
    # ── Kubernetes ────────────────────────────────────────────────────────────
    {
        "desc":        "K8s — list running pods",
        "prompt":      "What pods are running right now?",
        "expect_tool": "kubectl_command",
        "eval_fn":     lambda d: (
            _used_tool(d, "kubectl_command") and _has_output(d),
            "kubectl was called and returned output"
        ),
    },
    {
        "desc":        "K8s — list all namespaces",
        "prompt":      "Show me all Kubernetes namespaces",
        "expect_tool": "kubectl_command",
        "eval_fn":     lambda d: (
            _used_tool(d, "kubectl_command") and _has_output(d),
            "namespaces listed via kubectl"
        ),
    },
    {
        "desc":        "K8s — describe a pod",
        "prompt":      "Describe the nginx pod in detail",
        "expect_tool": "kubectl_command",
        "eval_fn":     lambda d: (
            _used_tool(d, "kubectl_command") and _has_output(d),
            "kubectl describe returned pod detail"
        ),
    },
    {
        "desc":        "K8s — cluster nodes",
        "prompt":      "How many nodes does the cluster have and what's their status?",
        "expect_tool": "kubectl_command",
        "eval_fn":     lambda d: (
            _used_tool(d, "kubectl_command") and _has_output(d),
            "kubectl get nodes returned"
        ),
    },
    {
        "desc":        "K8s — resource usage",
        "prompt":      "Show CPU and memory usage across all pods",
        "expect_tool": "kubectl_command",
        "eval_fn":     lambda d: (
            _used_tool(d, "kubectl_command"),
            "kubectl top or equivalent called"
        ),
    },

    # ── Docker ────────────────────────────────────────────────────────────────
    {
        "desc":        "Docker — list containers",
        "prompt":      "List all running Docker containers",
        "expect_tool": "docker_operation",
        "eval_fn":     lambda d: (
            _used_tool(d, "docker_operation") and _has_output(d),
            "docker ps returned container list"
        ),
    },
    {
        "desc":        "Docker — disk usage",
        "prompt":      "How much disk space is Docker using?",
        "expect_tool": "docker_operation",
        "eval_fn":     lambda d: (
            _used_tool(d, "docker_operation") and _has_output(d),
            "docker system df returned"
        ),
    },
    {
        "desc":        "Docker — list images",
        "prompt":      "What Docker images do I have locally?",
        "expect_tool": "docker_operation",
        "eval_fn":     lambda d: (
            _used_tool(d, "docker_operation") and _has_output(d),
            "docker images returned"
        ),
    },

    # ── Jenkins ───────────────────────────────────────────────────────────────
    {
        "desc":        "Jenkins — overall status",
        "prompt":      "What's the current status of Jenkins?",
        "expect_tool": "jenkins_status",
        "eval_fn":     lambda d: (
            _used_tool(d, "jenkins_status"),
            "jenkins_status tool was called"
        ),
    },
    {
        "desc":        "Jenkins — list jobs",
        "prompt":      "Show me all Jenkins jobs",
        "expect_tool": "jenkins_status",
        "eval_fn":     lambda d: (
            _used_tool(d, "jenkins_status"),
            "jenkins_status called to list jobs"
        ),
    },

    # ── Ansible ───────────────────────────────────────────────────────────────
    {
        "desc":        "Ansible — disk space check playbook",
        "prompt":      "Write and run an Ansible playbook to check disk space on localhost",
        "expect_tool": "run_ansible_playbook",
        "eval_fn":     lambda d: (
            _used_tool(d, "run_ansible_playbook"),
            "ansible playbook was generated and run"
        ),
    },
    {
        "desc":        "Ansible — uptime check playbook",
        "prompt":      "Run an Ansible playbook that prints system uptime on localhost",
        "expect_tool": "run_ansible_playbook",
        "eval_fn":     lambda d: (
            _used_tool(d, "run_ansible_playbook"),
            "ansible uptime playbook executed"
        ),
    },

    # ── Plain text (no tool expected) ─────────────────────────────────────────
    {
        "desc":        "Chat — explain Kubernetes concept",
        "prompt":      "What's the difference between a Deployment and a StatefulSet?",
        "expect_tool": None,
        "eval_fn":     lambda d: (
            d.get("type") == "message" and bool(d.get("content", "").strip()),
            "plain text answer returned, no tool called"
        ),
    },
    {
        "desc":        "Chat — explain CI/CD",
        "prompt":      "Explain what a Jenkins pipeline does in plain English",
        "expect_tool": None,
        "eval_fn":     lambda d: (
            d.get("type") == "message" and bool(d.get("content", "").strip()),
            "plain text answer, no tool invoked"
        ),
    },
]

# ── Runner ────────────────────────────────────────────────────────────────────

def run_scenario(client: httpx.Client, s: dict, idx: int, total: int, pause: bool) -> bool:
    hdr(f"[{idx}/{total}] {s['desc']}")
    print(f"  {Y}Prompt:{RST} {s['prompt']}\n")

    # Fresh conversation for each scenario so history never bleeds
    client.post("/reset")

    t0 = time.time()
    resp = client.post("/chat", json={"message": s["prompt"]}, timeout=120)
    elapsed = time.time() - t0

    if resp.status_code != 200:
        fail(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return False

    data = resp.json()
    rtype = data.get("type", "unknown")

    label("Response type:", rtype)
    label("Model:", data.get("model_used", "?"))
    label("Time:", f"{elapsed:.1f}s")

    # ── If auto_executed, show tool details ───────────────────────────────────
    if rtype == "auto_executed":
        tool_name = data.get("tool_name", "?")
        tool_args = data.get("tool_args", {})
        result    = data.get("result", {})
        interp    = data.get("interpretation", "")

        label("Tool called:", f"{P}{tool_name}{RST}")
        label("Args:", json.dumps(tool_args, separators=(',',':')))
        label("Exit success:", str(result.get("success", "?")))

        output = result.get("output", "").strip()
        if output:
            print(f"\n  {C}── Raw output ──{RST}")
            print(wrap(output[:800]))

        if interp:
            print(f"\n  {G}── Interpretation ──{RST}")
            print(wrap(interp[:600]))

    # ── Plain text ────────────────────────────────────────────────────────────
    elif rtype == "message":
        content = data.get("content", "")
        print(f"\n  {G}── Response ──{RST}")
        print(wrap(content[:800]))

    # ── Unexpected pending card (mode should be never) ────────────────────────
    elif rtype == "pending_approval":
        warn("Got a pending_approval card — mode=never wasn't applied")
        label("Tool:", data.get("tool_name", "?"))

    # ── Evaluate ──────────────────────────────────────────────────────────────
    passed = False
    note = ""
    if s.get("eval_fn"):
        try:
            passed, note = s["eval_fn"](data)
        except Exception as e:
            note = f"eval error: {e}"

    print()
    if passed:
        ok(f"PASS — {note}")
    else:
        expected = s.get("expect_tool")
        actual   = data.get("tool_name", "none")
        if expected and actual != expected:
            fail(f"FAIL — expected tool '{expected}', got '{actual}'. {note}")
        else:
            fail(f"FAIL — {note or 'evaluation did not pass'}")

    if pause:
        input(f"\n  {DIM}Press Enter for next scenario...{RST}")

    return passed


def main():
    parser = argparse.ArgumentParser(description="DevGordon capability showcase")
    parser.add_argument("--url", default="http://localhost:8000", help="DevGordon base URL")
    parser.add_argument("--pause", action="store_true", help="Pause between scenarios")
    parser.add_argument("--scenario", type=int, default=None, help="Run only scenario N (1-indexed)")
    args = parser.parse_args()

    client = httpx.Client(base_url=args.url)

    # ── Pre-flight ────────────────────────────────────────────────────────────
    print(f"\n{W}DevGordon Capability Showcase{RST}")
    print(f"Target: {B}{args.url}{RST}\n")

    try:
        health = client.get("/health", timeout=5).json()
        ollama = health.get("ollama", "?")
        model  = health.get("model_selected", "?")
        status = health.get("status", "?")
        if ollama == "unreachable":
            print(f"{R}✗ Ollama is unreachable — is it running on the host?{RST}")
            sys.exit(1)
        elif ollama == "model_not_found":
            print(f"{Y}⚠  Model '{model}' not found in Ollama — pull it first{RST}")
            sys.exit(1)
        else:
            print(f"{G}✔ Backend healthy{RST}  status={status}  model={model}")
    except Exception as e:
        print(f"{R}✗ Cannot reach {args.url}: {e}{RST}")
        sys.exit(1)

    # ── Set approval mode to never ────────────────────────────────────────────
    client.post("/approval-mode", json={"mode": "never"})
    print(f"{G}✔ Approval mode set to 'never' (all tools auto-execute){RST}")

    # ── Run scenarios ─────────────────────────────────────────────────────────
    scenarios = SCENARIOS
    if args.scenario is not None:
        scenarios = [SCENARIOS[args.scenario - 1]]

    results = []
    for i, s in enumerate(scenarios, 1):
        passed = run_scenario(client, s, i, len(scenarios), args.pause)
        results.append((s["desc"], passed))

    # ── Summary ───────────────────────────────────────────────────────────────
    hdr("Summary")
    passed_n = sum(1 for _, p in results if p)
    total_n  = len(results)
    for desc, p in results:
        sym = f"{G}✔{RST}" if p else f"{R}✗{RST}"
        print(f"  {sym} {desc}")

    pct = int(passed_n / total_n * 100) if total_n else 0
    colour = G if pct == 100 else Y if pct >= 70 else R
    print(f"\n  {colour}{passed_n}/{total_n} passed ({pct}%){RST}\n")

    # ── Restore approval mode ─────────────────────────────────────────────────
    client.post("/approval-mode", json={"mode": "always"})
    print(f"{DIM}Approval mode restored to 'always'{RST}\n")

    sys.exit(0 if passed_n == total_n else 1)


if __name__ == "__main__":
    main()
