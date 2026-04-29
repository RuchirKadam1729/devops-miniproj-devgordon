# DevGordon Changelog

## Current state (April 2026)

### Architecture
- **Ollama runs on host** (not in Docker) for Metal GPU access on Apple Silicon. Compose points to `host.docker.internal:11434`. Prior setup ran Ollama in Docker → CPU only → 60s timeouts.
- **Docker Desktop Kubernetes** replaces Minikube as the recommended K8s setup. Kubeconfig is copied to `/tmp/kube/config` at container startup and `127.0.0.1` is rewritten to `host.docker.internal` so kubectl works from inside the container without touching the read-only mounted file.
- **qwen3:8b** as default model. Full native tool calling support in Ollama.

### Backend fixes (app/main.py)
- `tool_args` was being passed through `json.loads()` even though Ollama returns arguments as a Python dict, not a JSON string — silently set args to `{}` on every tool call. Fixed with `isinstance(raw_args, str)` check.
- System prompt (`SYSTEM_PROMPT`) was defined but never injected into the messages list sent to Ollama. Fixed: prepended if `messages[0].role != "system"`.
- Tool result history used Anthropic's format (`role: "user"`, `type: "tool_result"`, `tool_use_id`). Ollama expects `role: "tool"` with plain string content. Fixed in both `/execute` and `/reject`.
- Added `"options": {"think": False}` to suppress qwen3's extended thinking mode on CPU (burns the full timeout before responding). Can be removed when running on GPU.

### Approval modes (new)
Three modes selectable from the header toggle:
- `always` — approval card before every tool call (default)
- `writes` — auto-execute read-only tools (`kubectl get`, `docker ps`, `jenkins_status`), card only for writes
- `never` — auto-execute everything

Read/write classification is based on the actual subcommand (`kubectl delete` is a write, `kubectl get` is a read). Backend: `GET/POST /approval-mode`, `_is_read_only()` helper, `auto_executed` response type.

### UI (app/static/index.html)
- Mode toggle in header (3 buttons, highlights red/yellow/green based on active mode)
- Execution results now show as a single message bubble instead of separate result block + interpretation message:
  - Bot interpretation text at top
  - `▶ Command run` collapsible (orange monospace) with ✓/✗ badge
  - `▶ Output / results` collapsible (grey monospace)
  - Works for both manually approved and auto-executed tool calls

### Files deleted from root
`DELIVERABLES.md`, `FINAL_VALIDATION.md`, `INDEX.md`, `PROJECT_SUMMARY.txt`, `SETUP_COMPLETE.md`, `SONARQUBE_INTEGRATION.md`, `TEST_REPORT.md`, `DEVGORDON_README.md` — consolidated into `README.md`, `CHANGELOG.md`, `QUICKREF.sh`.

---

## Known limitations

- Conversation history is in-memory — clears on container restart
- No authentication — local/lab use only
- Python code scanning via SonarQube requires sonar-scanner in the container (not installed by default); Ansible scanning via ansible-lint works out of the box
- `think: false` option logs a warning on older Ollama versions — harmless, remove the option if it bothers you
