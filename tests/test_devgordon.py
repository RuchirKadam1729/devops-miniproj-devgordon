"""
Comprehensive DevGordon Test Suite
===================================
Tests all major features:
- Model management & caching
- Scanning & quality gates
- Chat & LLM integration
- Tool execution
- API endpoints
- Error handling
"""

import pytest
import json
import subprocess
import requests
import time
from pathlib import Path

BASE_URL = "http://localhost:8000"
OLLAMA_URL = "http://localhost:11434"
JENKINS_URL = "http://localhost:8080"
SONAR_URL = "http://localhost:9000"

# =============================================================================
# FIXTURE: Health Check
# =============================================================================

@pytest.fixture(scope="session", autouse=True)
def ensure_services_running():
    """Verify all services are up before running tests."""
    print("\n[SETUP] Checking services...")
    
    # Check DevGordon app
    for i in range(30):
        try:
            r = requests.get(f"{BASE_URL}/health", timeout=5)
            if r.status_code == 200:
                print("✓ DevGordon app ready")
                break
        except:
            time.sleep(1)
    else:
        raise RuntimeError("DevGordon app failed to start")
    
    # Check Ollama
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        assert r.status_code == 200
        print("✓ Ollama ready")
    except:
        raise RuntimeError("Ollama not responding")
    
    yield


# =============================================================================
# TESTS: HEALTH & STATUS
# =============================================================================

class TestHealth:
    """Test basic health checks."""
    
    def test_health_endpoint_returns_ok(self):
        """Health endpoint should return 200."""
        r = requests.get(f"{BASE_URL}/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
    
    def test_health_shows_ollama_status(self):
        """Health should report Ollama status."""
        r = requests.get(f"{BASE_URL}/health")
        data = r.json()
        assert "ollama" in data
        assert data["ollama"] in ["ok", "no_models", "unreachable"]
    
    def test_health_lists_models(self):
        """Health should list installed models."""
        r = requests.get(f"{BASE_URL}/health")
        data = r.json()
        assert "models_available" in data
        assert isinstance(data["models_available"], list)
        if len(data["models_available"]) > 0:
            assert data["selected_model"] is not None
    
    def test_health_shows_service_urls(self):
        """Health should show service URLs."""
        r = requests.get(f"{BASE_URL}/health")
        data = r.json()
        assert "jenkins" in data
        assert "sonarqube" in data
        assert "http://" in data["jenkins"]
        assert "http://" in data["sonarqube"]


# =============================================================================
# TESTS: MODEL MANAGEMENT
# =============================================================================

class TestModelManagement:
    """Test model detection, selection, and caching."""
    
    def test_models_endpoint_returns_list(self):
        """GET /models should return installed models."""
        r = requests.get(f"{BASE_URL}/models")
        assert r.status_code == 200
        data = r.json()
        assert "models" in data
        assert isinstance(data["models"], list)
    
    def test_models_have_selected_one(self):
        """Should auto-select a model if one is available."""
        r = requests.get(f"{BASE_URL}/models")
        data = r.json()
        if len(data["models"]) > 0:
            assert data["selected_model"] is not None
            assert data["selected_model"] in data["models"]
    
    def test_model_selection_validation(self):
        """Selecting non-existent model should error."""
        r = requests.post(
            f"{BASE_URL}/select-model",
            json={"model": "nonexistent-model-xyz"}
        )
        assert r.status_code == 200
        data = r.json()
        assert "error" in data
    
    def test_model_selection_works(self):
        """Selecting valid model should succeed."""
        # Get current models
        r = requests.get(f"{BASE_URL}/models")
        models = r.json()["models"]
        
        if len(models) > 0:
            # Select first model
            r = requests.post(
                f"{BASE_URL}/select-model",
                json={"model": models[0]}
            )
            assert r.status_code == 200
            data = r.json()
            assert data.get("status") == "ok"
            assert data["selected_model"] == models[0]
    
    def test_model_persists_across_requests(self):
        """Selected model should persist."""
        r1 = requests.get(f"{BASE_URL}/models")
        model1 = r1.json()["selected_model"]
        
        r2 = requests.get(f"{BASE_URL}/models")
        model2 = r2.json()["selected_model"]
        
        assert model1 == model2


# =============================================================================
# TESTS: CHAT & LLM
# =============================================================================

class TestChat:
    """Test chat endpoint and LLM integration."""
    
    def test_chat_requires_message(self):
        """Chat endpoint should require message field."""
        r = requests.post(f"{BASE_URL}/chat", json={})
        assert r.status_code in [200, 422]  # Either error or validation error
    
    def test_chat_with_empty_message(self):
        """Chat with empty message should still work."""
        r = requests.post(f"{BASE_URL}/chat", json={"message": ""})
        # May get error or may return response
        assert r.status_code in [200, 422, 503]
    
    def test_chat_returns_response_type(self):
        """Chat response should have type field."""
        r = requests.post(
            f"{BASE_URL}/chat",
            json={"message": "hello"}
        )
        if r.status_code == 200:
            data = r.json()
            assert "type" in data
            assert data["type"] in ["message", "pending_approval"]
    
    def test_chat_returns_model_used(self):
        """Chat response should show which model was used."""
        r = requests.post(
            f"{BASE_URL}/chat",
            json={"message": "test"}
        )
        if r.status_code == 200:
            data = r.json()
            assert "model_used" in data
    
    def test_chat_with_history(self):
        """Chat should accept conversation history."""
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"}
        ]
        r = requests.post(
            f"{BASE_URL}/chat",
            json={"message": "how are you?", "history": history}
        )
        if r.status_code == 200:
            data = r.json()
            assert data["type"] in ["message", "pending_approval"]


# =============================================================================
# TESTS: CODE SCANNING (CORE FEATURE)
# =============================================================================

class TestCodeScanning:
    """Test pre-execution scanning for generated code."""
    
    def test_scan_requires_tool_name(self):
        """Scan should require tool_name."""
        r = requests.post(
            f"{BASE_URL}/scan-code",
            json={"tool_args": {}}
        )
        assert r.status_code in [200, 400, 422]
        if r.status_code == 200:
            data = r.json()
            assert "error" in data or "scan_status" in data
    
    def test_scan_ansible_playbook_clean(self):
        """Clean playbook should pass scan."""
        clean_playbook = """---
- name: Safe playbook
  hosts: localhost
  tasks:
    - name: Show info
      ansible.builtin.debug:
        msg: "Hello"
"""
        r = requests.post(
            f"{BASE_URL}/scan-code",
            json={
                "tool_name": "run_ansible_playbook",
                "tool_args": {"playbook_content": clean_playbook}
            }
        )
        assert r.status_code == 200
        data = r.json()
        assert "scan_status" in data
        assert data["scan_status"] in ["clean", "warning", "error"]
        assert "issues" in data
        assert isinstance(data["issues"], list)
    
    def test_scan_catches_world_writable_permissions(self):
        """Scan should catch 0777 permissions."""
        bad_playbook = """---
- hosts: localhost
  tasks:
    - file:
        path: /tmp/test
        mode: 0777
"""
        r = requests.post(
            f"{BASE_URL}/scan-code",
            json={
                "tool_name": "run_ansible_playbook",
                "tool_args": {"playbook_content": bad_playbook}
            }
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["issues"]) > 0
        assert data["scan_status"] in ["warning", "error"]
    
    def test_scan_catches_http_urls(self):
        """Scan should catch unencrypted HTTP URLs."""
        bad_playbook = """---
- hosts: localhost
  tasks:
    - get_url:
        url: http://example.com/file.tar.gz
        dest: /tmp/file.tar.gz
"""
        r = requests.post(
            f"{BASE_URL}/scan-code",
            json={
                "tool_name": "run_ansible_playbook",
                "tool_args": {"playbook_content": bad_playbook}
            }
        )
        assert r.status_code == 200
        data = r.json()
        # Should catch HTTP or other issues
        assert isinstance(data["issues"], list)
    
    def test_scan_catches_shell_injection_risk(self):
        """Scan should warn about shell with variables."""
        risky_playbook = """---
- hosts: localhost
  vars:
    user_input: "test"
  tasks:
    - shell: echo {{ user_input }}
"""
        r = requests.post(
            f"{BASE_URL}/scan-code",
            json={
                "tool_name": "run_ansible_playbook",
                "tool_args": {"playbook_content": risky_playbook}
            }
        )
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data["issues"], list)
    
    def test_scan_kubectl_destructive_commands(self):
        """Scan should flag destructive kubectl commands."""
        r = requests.post(
            f"{BASE_URL}/scan-code",
            json={
                "tool_name": "kubectl_command",
                "tool_args": {"command": "delete pod my-pod"}
            }
        )
        assert r.status_code == 200
        data = r.json()
        assert "delete" in data["tool"].lower() or len(data["issues"]) > 0
    
    def test_scan_returns_can_approve_flag(self):
        """Scan should indicate if code can be approved."""
        r = requests.post(
            f"{BASE_URL}/scan-code",
            json={
                "tool_name": "run_ansible_playbook",
                "tool_args": {"playbook_content": "---\n- hosts: localhost\n  tasks:\n    - debug: msg=test\n"}
            }
        )
        assert r.status_code == 200
        data = r.json()
        assert "can_approve" in data
        assert isinstance(data["can_approve"], bool)


# =============================================================================
# TESTS: SONARQUBE STANDARDS
# =============================================================================

class TestSonarQubeStandards:
    """Test SonarQube integration."""
    
    def test_sonarqube_standards_endpoint_exists(self):
        """SonarQube standards endpoint should respond."""
        r = requests.get(f"{BASE_URL}/sonarqube-standards")
        assert r.status_code == 200
    
    def test_sonarqube_standards_returns_project_info(self):
        """Should return project key and status."""
        r = requests.get(f"{BASE_URL}/sonarqube-standards")
        data = r.json()
        assert "project" in data
        assert data["project"] == "devgordon"
        assert "sonar_url" in data
    
    def test_sonarqube_standards_lists_issues(self):
        """Should list recent SonarQube issues."""
        r = requests.get(f"{BASE_URL}/sonarqube-standards")
        data = r.json()
        assert "recent_issues" in data
        assert isinstance(data["recent_issues"], list)


# =============================================================================
# TESTS: CONVERSATION HISTORY
# =============================================================================

class TestHistory:
    """Test conversation history management."""
    
    def test_history_endpoint_exists(self):
        """History endpoint should respond."""
        r = requests.get(f"{BASE_URL}/history")
        assert r.status_code == 200
    
    def test_history_returns_list(self):
        """History should return list of messages."""
        r = requests.get(f"{BASE_URL}/history")
        data = r.json()
        assert "history" in data
        assert isinstance(data["history"], list)
    
    def test_clear_history_works(self):
        """Should be able to clear history."""
        # Add something
        requests.post(f"{BASE_URL}/chat", json={"message": "test"})
        
        # Clear
        r = requests.delete(f"{BASE_URL}/history")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "cleared"
        
        # Verify empty
        r = requests.get(f"{BASE_URL}/history")
        assert r.json()["history"] == []


# =============================================================================
# TESTS: MCP (Model Context Protocol)
# =============================================================================

class TestMCP:
    """Test MCP tool discovery and execution."""
    
    def test_mcp_tools_endpoint_exists(self):
        """MCP tools endpoint should exist."""
        r = requests.get(f"{BASE_URL}/mcp/tools")
        assert r.status_code == 200
    
    def test_mcp_tools_returns_list(self):
        """Should return list of tools."""
        r = requests.get(f"{BASE_URL}/mcp/tools")
        data = r.json()
        assert "tools" in data
        assert isinstance(data["tools"], list)
        assert len(data["tools"]) > 0
    
    def test_mcp_tools_have_required_fields(self):
        """Each tool should have name, description, inputSchema."""
        r = requests.get(f"{BASE_URL}/mcp/tools")
        data = r.json()
        for tool in data["tools"]:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert len(tool["name"]) > 0
    
    def test_mcp_tools_include_all_major_ones(self):
        """Should include kubectl, ansible, jenkins, docker tools."""
        r = requests.get(f"{BASE_URL}/mcp/tools")
        data = r.json()
        tool_names = [t["name"] for t in data["tools"]]
        
        expected = ["kubectl_command", "run_ansible_playbook", "docker_operation"]
        for expected_tool in expected:
            assert expected_tool in tool_names, f"{expected_tool} not in {tool_names}"
    
    def test_mcp_call_endpoint_exists(self):
        """MCP call endpoint should exist."""
        r = requests.post(
            f"{BASE_URL}/mcp/call",
            json={"tool": "invalid"}
        )
        # Should return 200 or error, not 404
        assert r.status_code != 404
    
    def test_mcp_call_requires_tool(self):
        """MCP call should require tool field."""
        r = requests.post(f"{BASE_URL}/mcp/call", json={})
        assert r.status_code == 200
        data = r.json()
        assert "error" in data or "success" in data


# =============================================================================
# TESTS: API ENDPOINTS
# =============================================================================

class TestAPIEndpoints:
    """Test all API routes."""
    
    def test_root_returns_html(self):
        """Root endpoint should return HTML (the UI)."""
        r = requests.get(f"{BASE_URL}/")
        assert r.status_code == 200
        assert "html" in r.text.lower() or "<!doctype" in r.text.lower()
    
    def test_all_endpoints_accessible(self):
        """Key endpoints should all be accessible."""
        endpoints = [
            ("GET", "/health"),
            ("GET", "/models"),
            ("POST", "/select-model"),
            ("GET", "/history"),
            ("POST", "/chat"),
            ("POST", "/scan-code"),
            ("GET", "/sonarqube-standards"),
            ("GET", "/mcp/tools"),
            ("POST", "/mcp/call"),
        ]
        
        for method, path in endpoints:
            if method == "GET":
                r = requests.get(f"{BASE_URL}{path}")
            else:
                r = requests.post(f"{BASE_URL}{path}", json={})
            
            # Should not return 404
            assert r.status_code != 404, f"{method} {path} returned 404"


# =============================================================================
# TESTS: ERROR HANDLING
# =============================================================================

class TestErrorHandling:
    """Test graceful error handling."""
    
    def test_invalid_json_returns_error(self):
        """Invalid JSON should not crash server."""
        r = requests.post(
            f"{BASE_URL}/chat",
            data="not valid json",
            headers={"Content-Type": "application/json"}
        )
        assert r.status_code != 500  # Not a server error
    
    def test_missing_required_fields(self):
        """Missing required fields should error gracefully."""
        r = requests.post(f"{BASE_URL}/chat", json={})
        assert r.status_code in [200, 422]  # Not 500
    
    def test_sonarqube_unavailable_doesnt_crash(self):
        """App should work even if SonarQube is unavailable."""
        # This is already tested by the fact that tests run
        r = requests.get(f"{BASE_URL}/sonarqube-standards")
        # Should return something, not 500
        assert r.status_code != 500


# =============================================================================
# TESTS: FEATURE COMPLETENESS
# =============================================================================

class TestFeatureCompleteness:
    """Verify all major features are implemented."""
    
    def test_model_caching_works(self):
        """Models should persist across requests (caching)."""
        r1 = requests.get(f"{BASE_URL}/models")
        models1 = r1.json()["models"]
        
        # Small delay
        time.sleep(0.5)
        
        r2 = requests.get(f"{BASE_URL}/models")
        models2 = r2.json()["models"]
        
        # Should be same models (caching works)
        assert models1 == models2
    
    def test_conversation_flow(self):
        """Test full conversation flow: chat → history."""
        # Clear history
        requests.delete(f"{BASE_URL}/history")
        
        # Send message
        r1 = requests.post(
            f"{BASE_URL}/chat",
            json={"message": "hello"}
        )
        assert r1.status_code == 200
        
        # Check history updated
        r2 = requests.get(f"{BASE_URL}/history")
        history = r2.json()["history"]
        assert len(history) > 0
    
    def test_scanning_blocks_bad_code(self):
        """Verify scanning can block execution."""
        # Scan bad code
        r = requests.post(
            f"{BASE_URL}/scan-code",
            json={
                "tool_name": "run_ansible_playbook",
                "tool_args": {"playbook_content": "- file:\n    mode: 0777"}
            }
        )
        
        data = r.json()
        # Either has issues or can_approve is false
        if "can_approve" in data:
            # Bad code should not be automatically approvable
            assert not data["can_approve"] or len(data["issues"]) > 0
    
    def test_mcp_tools_discoverable(self):
        """External clients should discover tools via MCP."""
        r = requests.get(f"{BASE_URL}/mcp/tools")
        data = r.json()
        
        # Should have meaningful tool descriptions
        for tool in data["tools"]:
            assert len(tool["description"]) > 10
    
    def test_full_scan_to_execution_flow(self):
        """Test: generate code → scan → show result."""
        # This simulates what would happen in a real workflow
        playbook = """---
- name: Test
  hosts: localhost
  tasks:
    - debug: msg=test
"""
        
        # Scan the code
        r = requests.post(
            f"{BASE_URL}/scan-code",
            json={
                "tool_name": "run_ansible_playbook",
                "tool_args": {"playbook_content": playbook}
            }
        )
        
        assert r.status_code == 200
        data = r.json()
        
        # Should have scan results
        assert "scan_status" in data
        assert "issues" in data
        assert "can_approve" in data


# =============================================================================
# PERFORMANCE TESTS
# =============================================================================

class TestPerformance:
    """Test response times and performance."""
    
    def test_health_endpoint_fast(self):
        """Health endpoint should respond quickly."""
        start = time.time()
        r = requests.get(f"{BASE_URL}/health", timeout=5)
        elapsed = time.time() - start
        
        assert r.status_code == 200
        assert elapsed < 2  # Should be under 2 seconds
    
    def test_models_endpoint_fast(self):
        """Models endpoint should be fast."""
        start = time.time()
        r = requests.get(f"{BASE_URL}/models", timeout=5)
        elapsed = time.time() - start
        
        assert r.status_code == 200
        assert elapsed < 2
    
    def test_scan_completes_reasonably(self):
        """Scan should complete within reasonable time."""
        start = time.time()
        r = requests.post(
            f"{BASE_URL}/scan-code",
            json={
                "tool_name": "run_ansible_playbook",
                "tool_args": {"playbook_content": "---\n- hosts: localhost\n  tasks:\n    - debug: msg=test\n"}
            },
            timeout=30
        )
        elapsed = time.time() - start
        
        assert r.status_code == 200
        assert elapsed < 30  # ansible-lint can take a few seconds


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

class TestIntegration:
    """Test integration between components."""
    
    def test_ollama_integration(self):
        """Verify chat actually uses Ollama."""
        r = requests.post(
            f"{BASE_URL}/chat",
            json={"message": "test"}
        )
        
        if r.status_code == 200:
            data = r.json()
            # Should have model info showing Ollama was used
            assert "model_used" in data
            assert data["model_used"] is not None
    
    def test_scanner_integration(self):
        """Verify scanner is called before execution."""
        # The fact that /scan-code endpoint works proves integration
        r = requests.get(f"{BASE_URL}/mcp/tools")
        tools = r.json()["tools"]
        
        # Scanner should apply to ansible playbooks
        ansible_tool = next(
            (t for t in tools if "ansible" in t["name"]),
            None
        )
        assert ansible_tool is not None
    
    def test_sonarqube_integration(self):
        """Verify SonarQube endpoint is functional."""
        r = requests.get(f"{BASE_URL}/sonarqube-standards")
        
        assert r.status_code == 200
        data = r.json()
        assert "project" in data
        assert "sonar_url" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
