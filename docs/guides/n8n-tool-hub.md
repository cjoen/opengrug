# Connecting an n8n Instance to Grug

Grug can turn your n8n workflows into first-class tools. Tag a workflow
`grug-tool`, describe it with a small JSON block, and Grug registers it as a
tool the model can call — no Python, no redeploy. You can add, change, or
remove tools entirely from the n8n UI and reload them from Slack.

This guide walks through connecting an instance and building your first tool.

---

## Prerequisites

- A running n8n instance Grug can reach over HTTP.
- An n8n API key (n8n **Settings → n8n API → Create an API key**).
- Access to edit Grug's `grug_config.json`.

---

## 1. Point Grug at your n8n instance

Add an `n8n` section to `grug_config.json`. The integration is **opt-in** —
if this section is absent, nothing loads and startup is unaffected.

```json
"n8n": {
  "base_url": "http://localhost:5678",
  "api_key": "your-n8n-api-key",
  "tag": "grug-tool",
  "timeout_seconds": 10
}
```

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| `base_url` | yes | — | Root of your n8n instance. A trailing slash is fine (it's stripped). |
| `api_key` | yes | — | Used **only** for workflow discovery — never sent to the webhooks. |
| `tag` | no | `grug-tool` | The tag Grug looks for when discovering workflows. |
| `timeout_seconds` | no | `10` | Timeout for both discovery and webhook calls. |

Restart Grug once after adding the section so the loader initializes. On
startup Grug calls `GET {base_url}/api/v1/workflows?active=true&tags={tag}`
and registers one tool per valid workflow.

> If the API key is wrong or empty, discovery fails gracefully: Grug logs a
> warning, registers zero n8n tools, and starts normally. It never crashes on
> an unreachable or misconfigured n8n.

---

## 2. Build a workflow as a tool

For a workflow to become a Grug tool it must meet four conditions:

1. **Start with a Webhook trigger node.** This is the execution entry point.
2. **Be active** in n8n.
3. **Carry the discovery tag** (`grug-tool` by default).
4. **Describe itself** with a `grug` JSON block in the workflow's
   **Description** field (Workflow **Settings → Description**).

### The `grug` description block

```json
{
  "grug": {
    "name": "send_email",
    "description": "Send an email to a recipient. Use when the user wants to email someone.",
    "webhook_path": "send-email",
    "read_only": false,
    "category": "COMMS",
    "parameters": {
      "to":      { "type": "string", "description": "Recipient email address" },
      "subject": { "type": "string", "description": "Email subject line" },
      "body":    { "type": "string", "description": "Email body text" }
    },
    "required": ["to", "subject", "body"]
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Tool name — must be unique. A collision with a Grug builtin is **skipped with a warning**; the builtin wins. |
| `description` | yes | Shown to the model. Describe **what it does and when to use it** — this drives routing accuracy. |
| `webhook_path` | yes | Path suffix. Grug calls `POST {base_url}/webhook/{webhook_path}` and must match your Webhook node's path. |
| `read_only` | no (default `true`) | `true` → runs immediately. `false` → **requires human approval** (HITL gate) before executing. |
| `category` | no (default `N8N`) | Grouping shown in routing prompts. |
| `parameters` | no | JSON-Schema properties for the tool's arguments. |
| `required` | no | Which of those parameters are mandatory. |
| `response_rules` | no (default none) | Guidance for how Grug phrases its reply after the tool runs (e.g. `"Summarize in one sentence; don't echo raw JSON."`). Injected into the system prompt for the reply step only — never saved to history. Without it, Grug replies from the raw webhook output. |

A workflow is **skipped** (with a warning, or silently for empty
descriptions) if its description is missing, not valid JSON, has no `grug`
key, or is missing any of `name` / `webhook_path` / `description`. A skip
never affects other workflows or startup.

### Returning a result

Grug uses the webhook's HTTP response body as the tool's output. To return
data to the model (e.g. for a lookup tool), end your workflow with a
**Respond to Webhook** node that returns the payload. Set the Webhook node's
response mode accordingly. Without it, the tool returns whatever n8n's default
webhook response is.

---

## 3. Choose `read_only` deliberately

`read_only` is your safety switch:

- **`read_only: true`** — the tool runs the moment the model calls it. Use for
  lookups and other side-effect-free reads (`get_weather`, `search_notes`).
- **`read_only: false`** — the call is held at the **human-in-the-loop gate**
  and must be approved before it executes. Use for anything that changes state
  (`send_email`, `create_task`, `create_calendar_event`).

Internally, `read_only: false` maps to a destructive tool, so it flows through
the exact same approval path as Grug's built-in destructive tools.

---

## 4. Reload without restarting

Once Grug is running, you can add, change, or remove tagged workflows in n8n
and pick up the changes live. In Slack, invoke **`reload_n8n_tools`**
(for example: *"reload n8n tools"*).

Reload re-discovers the tagged workflows, swaps them into the registry
(builtins are never touched), and rebuilds the agent so the refreshed tool set
is available on the next message — no restart required.

---

## 5. Security notes

- **Webhooks are unauthenticated by default in n8n.** The API key is used only
  for discovery, never for webhook execution. If your webhooks need protection,
  secure them at n8n or the network layer.
- **Discovery metadata is operator-controlled.** `base_url` comes from your
  config and `webhook_path` from your own workflow description — not from end
  users — so keep those trustworthy. Argument *values* are sent as a JSON body,
  not interpolated into the URL.
- Grug never returns your API key or request headers in tool output; error
  strings include only status codes and response bodies.

---

## 6. Test routing offline (optional)

You can validate that user phrasings route to the right tool **without a live
n8n**, using the eval harness:

```bash
# Dry-run the n8n routing cases against the default model
python3 evals/run_evals.py --category N8N
```

The harness loads `evals/n8n_workflows_fixture.json` (a set of mock workflows)
instead of calling n8n, so you can compare models and catch routing
regressions before wiring real workflows. Add your own cases to
`evals/golden_dataset.jsonl` under `"category": "N8N"`.

---

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Tool never appears | Workflow not active, missing the tag, or the `grug` block is invalid JSON. Check Grug's startup logs for skip warnings. |
| Tool appears but errors on call | `webhook_path` doesn't match the Webhook node's path, or the workflow isn't returning a response. |
| Changes not picked up | Run `reload_n8n_tools` from Slack — startup discovery is a one-time snapshot. |
| Zero n8n tools at startup | Bad/empty `api_key`, wrong `base_url`, or n8n unreachable. Grug logs a warning and starts normally. |
| n8n tool name silently ignored | It collides with a Grug builtin; the builtin wins. Rename the workflow's `name`. |
