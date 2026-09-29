# Minecraft Server AI Incident Response Agent

An AI agent that inspects real Linux and Minecraft server telemetry — CPU, memory, disk, TPS, MSPT, player counts, logs, plugins — and produces evidence-based diagnoses of server health problems, through a controlled, narrowly-scoped tool system (no arbitrary command execution, ever).

## Problem statement

Diagnosing "why is my Minecraft server lagging?" usually means an admin manually checking `top`, tailing logs, running `/tps` in console, and cross-referencing all of it by hand. This project automates that investigative process: an LLM-driven agent decides which specific, safe diagnostic tools to call, gathers real telemetry, and explains its reasoning — while being architecturally incapable of running arbitrary commands on the host.

## Architecture

```
                ┌──────────────────────┐
                │      Admin/User      │
                │      CLI / API       │
                └──────────┬───────────┘
                           │
                           ▼
                ┌──────────────────────┐
                │   FastAPI REST API   │   api/
                └──────────┬───────────┘
                           │
                           ▼
                ┌──────────────────────┐
                │   IncidentResponse   │   agent/
                │        Agent         │
                │ reason → tool call → │
                │ observe → diagnose   │
                └──────────┬───────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
    ┌───────────┐   ┌────────────┐   ┌───────────┐
    │  Linux    │   │ Minecraft  │   │PostgreSQL │
    │  Tools    │   │   Tools    │   │(incident  │
    │(psutil)   │   │(ping/RCON/ │   │ history)  │
    │           │   │ log files) │   │           │
    └───────────┘   └────────────┘   └───────────┘
```

**Layering, and why:**

- `monitoring/` has zero knowledge of the LLM, tools, or HTTP. It only knows how to read real state from the OS (`psutil`), the Minecraft server (Server List Ping via `mcstatus`, RCON, and direct file parsing), and return typed Pydantic models. This is deliberately the most boring, most heavily-tested layer.
- `agent/tools/` wraps each monitoring method as a single, narrow, LLM-callable `Tool` — this is the security boundary (see below).
- `agent/agent.py` is provider-agnostic reasoning: it depends on an `LLMClient` Protocol and a `ToolRegistry`, so it's fully testable with a scripted fake LLM and never needs a real API key to test.
- `api/` is a thin FastAPI layer. Route handlers contain no business logic — they call the agent/monitors via dependency injection and translate results to HTTP.
- `database/` persists investigations for historical comparison ("have we seen this before?").

## Features

- Real Linux telemetry: CPU, memory, disk, uptime (`psutil`)
- Real Minecraft telemetry:
  - Server up/down, version, player count (Server List Ping — no auth needed)
  - Online player names (when the server exposes a sample)
  - TPS and MSPT via Paper's `/tps` console command over RCON
  - Installed plugins via RCON
  - server.properties (with secrets like `rcon.password` filtered out)
  - Recent logs and substring search over the log file
- An LLM agent (Claude, via the Anthropic API) that autonomously selects and chains tool calls to investigate a question, then produces a structured, evidence-cited diagnosis
- A FastAPI backend exposing chat, server status/metrics/players/logs, and investigation history endpoints
- PostgreSQL-backed incident history with a "find similar past incidents" query
- 94 automated tests, all running offline (no live LLM, no live Minecraft server, no live Postgres required)

## Tech stack

Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 · PostgreSQL · pytest · Docker · `psutil` · `mcstatus` · Anthropic API (`claude-sonnet-4-6`)

## Setup instructions

### Option A: Docker Compose (Postgres + app)

```bash
cp .env.example .env
# edit .env: set ANTHROPIC_API_KEY, and MINECRAFT_* settings for your server
docker compose up --build
```

The app will be at `http://localhost:8000` (interactive docs at `/docs`). Point `MINECRAFT_SERVER_DIR` in `.env` at your real Paper server directory before starting, so it gets mounted read-only into the container.

### Option B: Local Python (SQLite, for quick local testing without Postgres)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# For quick local testing, set DATABASE_URL=sqlite:///./dev.db in .env
uvicorn api.main:app --reload
```

### Enabling Minecraft telemetry on a real Paper server

Server List Ping (status/players) works with no configuration. TPS/MSPT/plugins require RCON — add to `server.properties`:

```
enable-rcon=true
rcon.port=25575
rcon.password=<a strong password, matching MINECRAFT_RCON_PASSWORD in .env>
```

## Example investigation

```
POST /chat
{"message": "Why is my Minecraft server lagging?"}
```

Agent's tool-call sequence (as recorded in `tool_calls` on the response and in the `tool_calls` table):

| Tool | Result |
|---|---|
| `get_tps` | `tps_1m: 13.2` |
| `get_mspt` | `mspt_avg: 75.4` |
| `get_cpu_usage` | `percent: 96.1` |
| `get_memory_usage` | `percent: 93.4` |
| `search_logs("Can't keep up")` | 37 matching lines |

Final `answer` field (illustrative — actual wording is generated dynamically by the LLM from whatever the tools return, not templated):

> **Summary:** The server is experiencing significant tick lag, most likely from CPU saturation.
> **Evidence:** TPS 13.2 (ideal 20.0), MSPT 75.4ms (ideal <50ms), CPU 96.1%, RAM 93.4%, 37 recent "Can't keep up!" warnings in the last log window.
> **Interpretation:** CPU and tick time are both saturated at the same time as repeated overload warnings, which points to resource exhaustion rather than a single bad plugin event.
> **Uncertain:** I cannot determine which specific process or plugin is driving CPU usage from this telemetry alone — `get_plugins` shows what's installed, but not per-plugin CPU cost.

The response is persisted to PostgreSQL (`investigations`, `tool_calls`, `observations` tables) and is retrievable later via `GET /investigations/{id}`.

## API documentation

Interactive OpenAPI docs are auto-generated at `/docs` once running. Summary:

| Method & Path | Description |
|---|---|
| `POST /chat` | Ask the agent a question; runs a full investigation and persists it |
| `GET /server/status` | Minecraft up/down, version, player count |
| `GET /server/metrics` | CPU/memory/disk/uptime + TPS/MSPT (degrades gracefully if RCON unset) |
| `GET /server/players` | Online player names |
| `GET /server/logs?query=&max_lines=` | Recent logs, or substring search |
| `POST /investigations` | Same as `/chat` but lets you name the server explicitly |
| `GET /investigations/{id}` | Retrieve a past investigation, including its tool-call audit trail |
| `GET /health` | Liveness check |

## Security considerations

- **No arbitrary command execution, anywhere.** Every tool is a specific, named function (`get_cpu_usage`, `get_tps`, ...) with a fixed JSON Schema — there is no `execute_command(command)` tool, and `tests/test_tools.py::TestNoArbitraryExecution` asserts no tool's input schema even accepts a parameter named `command`/`cmd`/etc.
- **RCON is never exposed to the LLM directly.** `monitoring/rcon.py`'s `RconClient.command()` is called from exactly one place (`minecraft_monitor.py`), always with a hardcoded string (`"tps"`, `"plugins"`) — never a value from a tool argument, the LLM, or an HTTP request body.
- **Secrets are filtered even from files the agent can read.** `get_server_properties` explicitly strips `rcon.password` and `query.password` before the data ever reaches a tool result.
- **Every tool call is wrapped so failures become structured data, not crashes or leaked tracebacks** (`Tool.run()` in `agent/tools/base.py`).
- **Read-only by design.** Weeks 1–4 implement no write/mutating operations against the Minecraft server or host.

## Testing instructions

```bash
pip install -r requirements.txt
PYTHONPATH=. pytest -v
```

All 94 tests run fully offline:
- Linux/Minecraft monitoring tests run against real `psutil` calls and real temp files, but mock the two network dependencies (Server List Ping, RCON) for failure-mode tests.
- Agent loop tests use a `FakeLLMClient` returning scripted, SDK-shaped responses — no Anthropic API key needed.
- Database tests run against a real in-memory SQLite database using the exact same SQLAlchemy models as production Postgres.
- API tests use FastAPI's `TestClient` with every dependency (monitors, agent, DB session) overridden via `app.dependency_overrides`.

## Future roadmap

Explicitly out of scope for Weeks 1–4, and not implemented:
- RAG over historical incidents or Minecraft/Paper documentation
- Automatic remediation (restarting the server, killing processes, kicking players)
- Kubernetes / container orchestration
- Prometheus / Grafana metrics export
- AWS or other cloud deployment automation
- Alembic migrations (currently `Base.metadata.create_all` — fine for this project's scope, not for a schema that evolves over time in production)
- Multi-server fleet management (the schema supports multiple `servers` rows, but the API currently defaults to a single "default" server for `/chat`)
