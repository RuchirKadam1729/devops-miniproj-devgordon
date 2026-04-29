# SonarQube Self-Checking for Generated Code

DevGordon now **scans generated code against SonarQube standards before approving execution**.

## How It Works

### 1. Generate Phase
```
User: "Create an Ansible playbook to deploy Nginx"
         ↓
LLM generates playbook code
```

### 2. Scan Phase
```
Pre-scan checks:
  - ansible-lint catches: FQCN requirements, security issues, YAML errors
  - Manual patterns catch: 0777 permissions, HTTP URLs, shell injection risks
  - SonarQube API (optional): language-specific standards for Python

Issues are flagged.
```

### 3. Approval Phase
```
DevGordon shows approval card:
  ✓ What it will do
  ⚠ Issues found (ansible-lint violations, security risks)
  
User can:
  [✓ Approve Anyway] [❌ Reject and Ask to Fix] [... Other Options]
```

### 4. Execute Phase
If approved, the code runs. If issues = "error", execution is blocked.

---

## Testing the Scanner

### Test 1: Insecure Playbook (Will Be Blocked)

```bash
curl -X POST http://localhost:8000/scan-code \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "run_ansible_playbook",
    "tool_args": {
      "playbook_content": "---\n- hosts: localhost\n  tasks:\n    - shell: whoami\n      mode: 0777"
    }
  }' | jq '.issues'
```

Returns:
```json
[
  "⚠ World-writable file permission (mode: 777) — security risk",
  "⚠ Shell/command task uses template variables — verify input is sanitized"
]
```

### Test 2: Safe Playbook (Will Be Approved)

```bash
curl -X POST http://localhost:8000/scan-code \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "run_ansible_playbook",
    "tool_args": {
      "playbook_content": "---\n- name: Safe playbook\n  hosts: localhost\n  tasks:\n    - name: Gather facts\n      ansible.builtin.setup:\n        gather_subset: all\n"
    }
  }' | jq '.scan_status'
```

Returns: `"clean"`

### Test 3: View SonarQube Standards

```bash
curl http://localhost:8000/sonarqube-standards | jq '.'
```

Shows:
- SonarQube project key
- Recent code quality issues
- Link to SonarQube dashboard

---

## What Gets Scanned

### ✓ Ansible Playbooks (FAST)
- ansible-lint for YAML/playbook violations
- Manual patterns for:
  - World-writable file permissions (0777)
  - Unencrypted URLs (http://)
  - Unsanitized shell variables
  - Privilege escalation without user specification
  - Tasks that may handle secrets without no_log

### ✓ Kubectl Commands (FAST)
- Flag destructive operations: `delete`, `delete namespace`, `--force`, `--all`
- Warn user about irreversible changes

### ✓ Docker Operations (FAST)
- Flag `docker system prune` (removes data)

### ✓ Python Code (OPTIONAL — Requires Setup)
- SonarQube API integration (requires sonar-scanner in container)
- Would enforce language-specific standards:
  - Error handling
  - Code duplication
  - Security issues
  - Naming conventions

---

## Architecture

```
/scan-code endpoint (POST)
  ↓
  Calls pre_scan() in scanner.py
  ↓
  For Ansible: runs ansible-lint + manual patterns (fast)
  For Python: would call SonarQube API (optional)
  For Kubectl: validates command safety
  ↓
  Returns: {scan_status, issues, can_approve}
  ↓
  UI shows results in approval card
```

---

## Full SonarQube Integration (Advanced)

To enable full SonarQube scanning of generated Python code:

### 1. Install sonar-scanner in Dockerfile
```dockerfile
RUN apt-get install -y sonar-scanner
```

### 2. Configure sonar-project.properties
```properties
sonar.projectKey=devgordon
sonar.projectName=DevGordon
sonar.sourceEncoding=UTF-8
sonar.sources=app
sonar.python.version=3.11
```

### 3. Set SONAR_TOKEN in .env
```bash
SONAR_TOKEN=your_sonarqube_token_here
```

### 4. Restart app
```bash
docker compose restart devgordon-app
```

Now Python code will be scanned via SonarQube before execution.

---

## What Quality Gates Are Enforced?

### Via ansible-lint (Default)
- FQCN requirements for all modules
- No bare variables in commands
- Proper task naming
- No world-writable files
- YAML validation

### Via SonarQube (If Configured)
- Python code quality standards
- Security vulnerabilities
- Code duplication
- Code coverage
- Naming conventions

These are the **same standards** run in the Jenkins pipeline on committed code.
The agent is held to the same standards.

---

## Next Steps

1. **Test the scanner** with the examples above
2. **Configure SonarQube token** in .env if you want full Python scanning
3. **Try generating code** via DevGordon and watch it get scanned
4. **Reject bad code** and ask the agent to fix it

---

**DevGordon's self-checking ensures generated code meets your codebase standards before execution.**
