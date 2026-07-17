# GenXAI Workflow Studio

A no-code, drag-and-drop workflow builder (in the spirit of n8n/Zapier) built on
the GenXAI framework. Drag components onto a canvas, wire them into a pipeline,
configure each node in the inspector, and run the workflow with live per-node
status streamed onto the canvas.

![Architecture](../../docs/diagrams/workflow_composition.svg)

## Components

| Node | What it does |
|---|---|
| **Input** | Entry point; receives the run's input JSON |
| **Output** | Exit point; captures the final workflow state |
| **Agent** | An LLM agent (role, goal, model, temperature, tools) |
| **Tool** | Runs one tool with fixed parameters (calculator, HTTP client, JSON/CSV processors, …) |
| **Decision** | Routes flow based on a condition key in workflow state |
| **Loop** | Repeats a body until a condition or iteration limit |
| **Subworkflow** | Runs another saved workflow as a single step (nested workflows) |
| **Chat Model** | Capability node: attach to an agent's *Model* port to choose its LLM |
| **Memory** | Capability node: attach to an agent's *Memory* port for cross-run recall |

Edges support **conditions** (routing) and **parallel** execution — both are
handled by the GenXAI graph engine, including proper join semantics (a fan-in
node waits for all incoming branches).

## Passing data between nodes

Tool parameters and agent tasks support `{{ }}` expressions that resolve
against workflow state at execution time — each node's result is stored under
its node id:

```json
{ "expression": "{{ calc1.data.result }} + 6" }
```

```
Task: Summarize this content: {{ fetch.data.content }}
```

A string that is exactly one expression keeps the value's type (numbers,
objects); embedded expressions interpolate as text. The run input is
available as `{{ input.<key> }}`. Unresolvable paths fail the node with a
clear error. After a run, **click any node** to inspect its output in the
inspector — that's where you find the paths to reference.

Try the seeded "Example: Chained calculators (data passing)" workflow to see
it in action (no API keys needed).

## Quick start

From the repository root:

```bash
# 1. Backend (FastAPI, port 8000)
./restart_workflow_studio_backend.sh

# 2. Frontend (Vite dev server, port 5173)
./restart_workflow_studio_frontend.sh
```

Open http://localhost:5173. A seeded example ("Example: Calculator pipeline")
is available in the *Open workflow…* dropdown — it runs **without any API
keys**. To use agent nodes, put keys in `backend/.env`
(see `backend/.env.example`):

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

## How it works

- The canvas (React Flow) edits a *workflow document*: nodes with positions +
  config, edges with condition/parallel flags.
- The backend translates that document directly into the node/edge dicts that
  `genxai.core.graph.executor.WorkflowExecutor` consumes and runs it with
  `execute_workflow_async`.
- Per-node lifecycle events from the graph engine (`running`, `completed`,
  `failed`, `skipped`) stream to the browser over **SSE**; the canvas recolors
  nodes live.
- Workflows persist as JSON files under `backend/data/workflows/`; run history
  is tracked with the framework's `ExecutionStore`.

## API

`http://localhost:8000/docs` for the interactive OpenAPI UI. Highlights:

| Route | Purpose |
|---|---|
| `GET  /api/v1/palette` | Node types, available tools (+schemas), model options |
| `GET/POST /api/v1/workflows`, `GET/PUT/DELETE /api/v1/workflows/{id}` | Workflow CRUD |
| `POST /api/v1/workflows/validate` | Structural validation without running |
| `POST /api/v1/workflows/{id}/run/stream` | Run a saved workflow (SSE) |
| `POST /api/v1/run/stream` | Run the current canvas without saving (SSE) |
| `GET  /api/v1/runs`, `GET /api/v1/runs/{run_id}` | Run history |

## Tests

```bash
cd applications/workflow_studio/backend
PYTHONPATH="../../..:." python -m pytest tests/ -q
```

Covers CRUD, palette shape, validation, document translation, and full SSE
runs (a key-free tool pipeline and a mocked-LLM agent pipeline).

## Automation (webhooks & schedules)

Open a saved workflow and expand **Automation** in the right rail:

- **Webhook** — generates a fire-by-URL endpoint. POST JSON to it and the
  body becomes the workflow input:

  ```bash
  curl -X POST http://localhost:8000/api/v1/hooks/<token> \
    -H 'Content-Type: application/json' -d '{"task": "run it"}'
  ```

- **Schedule** — runs the workflow automatically on a fixed interval or a
  cron expression (with timezone support). Enabled schedules resume when the
  backend restarts.

**Run history** (right rail) lists every run — manual, webhook, or scheduled —
with status and trigger source. History persists across restarts.

## Integrations (Connector nodes)

Drag a **Connector** node onto the canvas to call Email, Slack, WhatsApp,
HubSpot, S3, PostgreSQL, GitHub, Jira, Notion, or Google Workspace as a
pipeline step (send a message, create an issue, upsert a CRM contact, query a
database, append sheet rows, ...). Action parameters support the same
`{{ }}` expressions as tool nodes.

Connections are managed in the **Credentials** panel (right rail): add a
named credential per integration (e.g. a Slack bot token). Secrets are stored
server-side — the API never returns them — and encrypted at rest when
`GENXAI_CONNECTOR_CONFIG_KEY` holds a Fernet key.

## Event-driven triggers (GitHub)

Set the webhook **Provider** to *GitHub* in the Automation panel to turn a
workflow into an event handler: paste the hook URL and the shared secret into
your repo's webhook settings, and optionally filter to one event (e.g.
`issues.opened`). Deliveries are verified against `X-Hub-Signature-256`;
non-matching events are ignored. The workflow input becomes
`{"event": "issues.opened", "payload": {...}}`.

## Error handling per node

Every agent/tool/connector node has an **Advanced: retries & errors**
section: retry count (exponential backoff), per-attempt timeout, and
**continue on fail** — the node's failure is recorded as
`{"success": false, "error": ...}` and the pipeline keeps going, so a
downstream node (e.g. a Slack alert) can react to it.

## Securing the backend

Set `STUDIO_API_TOKEN` in `backend/.env` to require the `X-Studio-Token`
header on all API calls (webhook endpoints stay public — they have their own
tokens/signatures). Recommended if the backend is reachable beyond localhost.

## MCP Tool nodes

Register any Model Context Protocol server in the **MCP servers** panel
(right rail) — a local stdio command (like Claude Desktop configs) or a
remote HTTP/SSE endpoint. The **MCP Tool** node then offers that server's
tools live: pick server → tool (discovered on the fly) → params, with the
tool's own schema shown as hints. Params support `{{ }}` expressions.

One MCP server can expose dozens of tools, so this scales the integration
surface far faster than hand-written connectors.

**Agents can use MCP tools too**: every registered server's tools also appear
in the agent node's *tools* list as `mcp__{server}__{tool}` — the agent
decides mid-reasoning whether to call them, exactly like built-in tools.

> stdio servers execute their command on the backend host — only register
> commands you trust.

## Agent capability ports (n8n-style sub-nodes)

Agent nodes have three diamond ports on their bottom edge — **Model**,
**Memory**, and **Tools** — connected with dashed edges, like n8n's AI Agent:

- **Model**: attach a *Chat Model* node to choose the agent's LLM and
  temperature visually (one per agent).
- **Memory**: attach a *Memory* node and the agent remembers previous runs —
  conversation memory persists on disk under a stable identity
  (workflow + agent + session key). Turn *persistent* off for
  within-run-only memory. Different session keys keep separate histories.
- **Tools**: attach any **Tool** or **MCP Tool** node. Attached nodes leave
  the flow — instead of always running, the agent decides mid-reasoning
  whether to call them.

The inspector's config fields remain as the non-visual alternative; both
write the same workflow document.

## Shared agent memory

Toggle **Shared agent memory** in the Automation panel to give every agent
in the run a common `SharedMemoryBus` (a framework primitive) — useful for
multi-agent pipelines where one agent should see facts another one wrote.
Save the workflow after toggling for it to take effect on the next run.

## Subworkflows (nested workflows)

Drag a **Subworkflow** node onto the canvas and pick any other saved
workflow — it runs as a single step, using the graph engine's native
`subgraph` node type (the Studio just resolves which workflow document to
hand it, no custom nested-execution logic). Useful for factoring out a
common sequence (e.g. "send Slack alert") and reusing it across pipelines.
A subworkflow can't reference the workflow it's placed in.

## Import from YAML

**Import YAML…** in the toolbar accepts a genxai workflow YAML file — the
same format the `genxai workflow run` CLI reads (see `examples/nocode/*.yaml`
for real examples: routing pipelines, shared memory, human-in-the-loop). The
importer maps the CLI's node types onto the Studio's (`condition` →
Decision, `subgraph` → Subworkflow) and inlines each `agent:`-referenced
agent definition directly into its node, so the result is a normal,
editable canvas workflow. `agents_ref` (loading agents from a second file)
isn't supported for pasted/uploaded YAML — inline the agents first.

## Metrics

`GET /metrics` exposes the framework's own Prometheus collector
(`genxai.observability.metrics`) — the graph engine records
workflow/node/agent/tool execution counts and durations as a side effect of
running, so this needs no Studio-side instrumentation. Install the optional
`prometheus_client` package for real values (`pip install prometheus_client`);
without it the endpoint returns a placeholder line.

## Durable execution

All runs (manual, webhook, scheduled) go through a queue with worker tasks:

- **Closing the browser doesn't kill a run** — the SSE stream is a
  subscriber, not the owner. Reattach later via `GET /api/v1/runs/{id}/stream`.
- **Run details persist**: per-node outputs and events are stored on the run
  record (size-capped) — click any run in Run history to inspect them.
- **Cancel** queued or running runs (✕ in Run history, or
  `POST /api/v1/runs/{id}/cancel`).
- **Re-run** any past run with its stored workflow snapshot + input (↻, or
  `POST /api/v1/runs/{id}/rerun`).
- **Restart recovery**: runs left in flight by a dead process are marked
  `interrupted` on startup (never auto-resubmitted — that could double-charge
  LLM calls); re-run them explicitly if wanted.
