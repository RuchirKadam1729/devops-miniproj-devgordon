# DevGordon — Presentation Script
# ══════════════════════════════════════════════════════════════════════════════
# Structure: ~15 min. Sections marked with [DEMO] mean stop and show the UI
# or run the showcase. Sections marked [TALK] are pure speaking.
# Square brackets = stage direction. Everything else = say it.
# ══════════════════════════════════════════════════════════════════════════════


────────────────────────────────────────────────────────────────────────────────
SECTION 1 — THE PROBLEM  (~2 min, no demo yet)
────────────────────────────────────────────────────────────────────────────────

[TALK]

"DevOps is fundamentally a communication problem.

You've got a cluster, a CI pipeline, a fleet of containers — and the way
you interact with all of it is through a wall of CLIs, config files, and
documentation tabs. kubectl has 50 subcommands. Ansible playbooks are YAML.
Jenkins has a GUI that looks like 2009. None of it talks to each other.

The problem gets worse when something goes wrong at 2am. You know *what*
you want to happen. You might not remember the exact kubectl incantation.
You definitely don't want to write a playbook from scratch.

That's the gap this project targets: the space between knowing what you want
done and knowing exactly how to tell the machine to do it."


────────────────────────────────────────────────────────────────────────────────
SECTION 2 — THE SOLUTION  (~1.5 min)
────────────────────────────────────────────────────────────────────────────────

[TALK]

"DevGordon is a local conversational DevOps agent. You describe what you want
in plain English. It figures out which tool to call, shows you the exact
command it's going to run, and waits for your approval before touching
anything.

Key word: local. No cloud API. No sending your kubeconfig or playbook content
to OpenAI. The LLM runs on your own machine via Ollama — in my case that's
qwen3:8b on Apple Silicon, hitting Metal GPU directly, so inference takes
2-5 seconds not 60.

The stack is FastAPI on the backend, a clean vanilla JS frontend, and a set
of tool executors for kubectl, Docker, Ansible, and Jenkins. It's all wired
together with Ollama's native function calling."

[show the architecture mentally — point at things if you have a diagram]

"Let me show you what this actually looks like."


────────────────────────────────────────────────────────────────────────────────
SECTION 3 — OPEN THE UI  (~30 sec)
────────────────────────────────────────────────────────────────────────────────

[DEMO] Open http://localhost:8000

[TALK]

"Right — no login, no setup wizard, no cloud account. One URL.
The green dot means Ollama is reachable and the model is loaded.
The three buttons in the header — Ask always, Writes only, Auto-run — are
the approval mode toggle. I'll explain those in a minute."


────────────────────────────────────────────────────────────────────────────────
SECTION 4 — KUBERNETES DEMO  (~3 min)
────────────────────────────────────────────────────────────────────────────────

[DEMO] Type in UI: "what pods are running?"

[TALK while it thinks]

"So this goes: user message → FastAPI → Ollama with the full tool schema →
Ollama decides to call kubectl_command → the backend runs a pre-execution
security scan → and we get an approval card."

[when card appears]

"This is the approval card. It shows me what Ollama decided to do, the exact
command it's going to run — 'kubectl get pods -n default' — and the scan
result. Green means the scanner found no issues.

I can approve, reject, or ask it to modify. I'm going to approve."

[click Approve]

"And there's the result. The raw kubectl output in the collapsible, and above
it Ollama's interpretation in plain English — it parsed the table and
described what's running.

Notice: I never typed kubectl. I never specified a namespace. The model
inferred that from context and made a sensible choice."

---

[DEMO] Type: "describe the nginx pod in detail"

[TALK while it runs — this one is faster if mode is writes]

"This is a read-only operation so if I had the mode set to Writes Only it
would have auto-executed without asking. Let me show you that."

[DEMO] Switch to "Writes only" mode in header
[DEMO] Type: "how many nodes does the cluster have?"

[TALK]

"Auto-executed — no card, no click, just the result. That's the Writes Only
mode: reads run immediately, writes still ask. So you get speed on the
routine stuff but safety on anything destructive."


────────────────────────────────────────────────────────────────────────────────
SECTION 5 — SECURITY SCANNING  (~2 min)
────────────────────────────────────────────────────────────────────────────────

[DEMO] Switch back to "Ask always"
[DEMO] Type: "write and run an ansible playbook to check disk space on localhost"

[TALK while it generates]

"Ansible is the interesting one because Ollama is generating code here, not
just constructing a CLI command. It writes the playbook, then before the
approval card even appears, scanner.py runs ansible-lint on it plus a set
of manual pattern checks.

Things it flags: world-writable permissions like 0777, unencrypted http://
URLs in package sources, shell module calls that look like injection risks,
secrets passed without no_log."

[when card appears — point at scan result]

"Clean scan, green card. If I'd asked it to do something sketchy the card
would go red and list the specific issues — you can still approve, but
you've been warned and it's on you.

This is genuinely useful in a lab or team environment where you want a
human in the loop on any generated infrastructure code."


────────────────────────────────────────────────────────────────────────────────
SECTION 6 — DOCKER  (~1 min)
────────────────────────────────────────────────────────────────────────────────

[DEMO] Type: "list all running containers and show me docker disk usage"

[TALK]

"Docker operations work the same way. The model dispatches to the
docker_operation tool, runs docker ps and docker system df, and interprets
the output. Same approval flow, same scan."


────────────────────────────────────────────────────────────────────────────────
SECTION 7 — PLAIN CONVERSATION  (~1 min)
────────────────────────────────────────────────────────────────────────────────

[DEMO] Type: "what's the difference between a Deployment and a StatefulSet?"

[TALK]

"It doesn't always call a tool. When no action is needed it just answers.
The model knows its own system prompt — it knows its role is a DevOps
assistant, not a general chatbot — so it stays on topic and doesn't waffle.

This is useful for the 'I know what I want but I can't remember the exact
syntax' case. You ask, it answers, and if you decide you want it done you
just follow up and it'll generate the command."


────────────────────────────────────────────────────────────────────────────────
SECTION 8 — MCP / EXTERNAL AGENT ACCESS  (~1 min)
────────────────────────────────────────────────────────────────────────────────

[DEMO] In terminal: curl http://localhost:8000/mcp/tools | jq '[.tools[].name]'

[TALK]

"This is a detail that probably gets more marks than it sounds like.
DevGordon exposes a Model Context Protocol endpoint. That means an external
agent — say, Claude in another tool, or any MCP-compatible client — can
discover and call DevGordon's tools programmatically. It becomes a node
in a larger agentic system, not just a standalone UI.

So you could, in theory, have an AI coding assistant that recognises
'this code needs to be deployed' and routes that action through DevGordon
rather than trying to shell out itself."


────────────────────────────────────────────────────────────────────────────────
SECTION 9 — ENGINEERING DECISIONS WORTH FLAGGING  (~2 min)
────────────────────────────────────────────────────────────────────────────────

[TALK — no demo, just speak]

"A few decisions in here that I want to call out because they reflect real
engineering thinking rather than just getting it to work:

ONE — Ollama runs on the host, not in Docker. On Apple Silicon, running it
inside a container means CPU-only inference — 60-second response times and
timeouts. On the host it hits Metal GPU directly. The container reaches it
via host.docker.internal. This is a one-line config change but the reasoning
matters.

TWO — The kubeconfig problem. kubectl inside a container can't connect to
127.0.0.1 because that's the container's own loopback, not the host. The
standard fix is just mount the config and change the address, but you also
have to remove the certificate authority data when you add
insecure-skip-tls-verify, or kubectl refuses to connect. There's a Ruby
script at container startup that handles all of this — parses the YAML
properly, only rewrites localhost entries so remote clusters are untouched,
and writes the result to the correct home directory for whatever user the
container runs as.

THREE — Tool result format. Ollama and Anthropic's API use different formats
for feeding tool results back into conversation history. Ollama expects
role='tool' with plain string content. Getting this wrong silently breaks
the agentic loop — arguments come back as empty dicts and the model loses
context. That took debugging to find and fix.

FOUR — The approval mode system. Three modes — always ask, ask on writes only,
never ask — with a read/write classifier that looks at the actual subcommand,
not just the tool name. 'kubectl get' is a read. 'kubectl delete' is a write.
That distinction matters for usability without sacrificing safety."


────────────────────────────────────────────────────────────────────────────────
SECTION 10 — CLOSE  (~30 sec)
────────────────────────────────────────────────────────────────────────────────

[TALK]

"So to summarise what this actually delivers:

A local, private, zero-cloud DevOps agent that spans four tools —
Kubernetes, Docker, Ansible, Jenkins — with pre-execution security scanning,
a configurable approval loop, markdown-rendered output, an MCP endpoint for
external agents, and a CI pipeline in Jenkins with SonarQube quality gates.

It's not a toy demo that calls one API. It's a working system with real
engineering problems solved underneath it.

Happy to take questions."


────────────────────────────────────────────────────────────────────────────────
BACKUP — if something breaks live
────────────────────────────────────────────────────────────────────────────────

If Ollama is slow:
  → "This is running on CPU right now — normally on GPU this takes 2-5s"
  → Switch to a plain-text question while it catches up

If kubectl fails:
  → "The cluster connection goes through a startup script that rewrites
     the kubeconfig — if the container just restarted it may need a moment"
  → docker compose restart app && try again

If Jenkins returns 401:
  → "Jenkins token isn't configured in this environment — I'll show the
     kubectl and Docker tools instead which don't need external tokens"
  → Move straight to Section 6

If the model calls the wrong tool:
  → "This is actually a useful demo point — the model made a judgment call.
     I can reject this and rephrase." [reject, rephrase, continue]
     This shows the approval loop working exactly as designed.