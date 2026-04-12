# Future Improvements

> **ARCHIVED 2026-04-11** — superseded by [../followups.md](../followups.md), which merges every item below with the April 2026 steel-man review findings and deduplicates overlapping entries. This file is retained for historical context only.
>
> Cross-references (item here → ID in followups):
> - §1 category tags → L1
> - §2 hot-reload prompts → M6
> - §3 routing trace log → M5
> - §4 offline prompt test harness → M7
> - §5 `friendly_names` in registration → M8
> - §6 `PENDING` in-memory → H3 (merged with review H3)
> - §6 `add_note` tag enum → M10 (merged with review M finding)
> - §6 compression mode hardcoded → M11
> - §6 `_backlog_browser_proc` port collision → M12
> - §6 `memory.md` barely used → M9 (merged with review M finding)
> - §6 backlog dashboard LAN reachability → H8 (merged with review H8)
> - §6 backlog CLI error surfacing → H10

Follow-up work identified while debugging prompt/routing issues. Grouped by theme, ordered roughly by leverage. Nothing here is urgent — the harness works — but each item removes a paper cut or unblocks growth.

---

## 1. Scaling the tool registry (before we hit ~10 tools)

Gemma4 E4B routes well with a small, focused tool list. As the registry grows, the single flat `get_all_schemas()` dump into the prompt will start to hurt accuracy. Two options, simplest first:

### Option A — Category tags in descriptions (zero infra)
Prefix each tool's `description` with a bracketed tag (`[BOARD]`, `[NOTES]`, `[CHAT]`, `[META]`) so Gemma groups them mentally. Reinforce in `schema_examples.md`. No code changes beyond registration sites.

### Option B — Category field + keyword pre-filter (recommended once > ~10 tools)
- Add a `category` kwarg to `register_python_tool` / `register_cli_tool` in [core/orchestrator.py:36](core/orchestrator.py#L36).
- In `route_message` ([core/orchestrator.py:324](core/orchestrator.py#L324)), run a cheap keyword match on `user_message` to pick 1–2 relevant categories, then inject only those tools plus the always-on `meta` group (`reply_to_user`, `ask_for_clarification`, `escalate_to_frontier`, `list_capabilities`).
- Keeps a single LLM call. Avoids the latency cost of a two-stage router, which is tempting but doubles Gemma calls on what is already the bottleneck.

**Do NOT** build a two-stage "category → tool" router unless Option B measurably fails. Small-model latency is the constraint.

---

## 2. Hot-reload prompts

**Why:** The recent "formal English" bug cost real debugging time — the running Python process had the old prompts cached because [app.py:197](app.py#L197) only calls `load_prompt_files("prompts")` once at import. Every prompt edit requires a full process restart, and it is easy to forget.

**Options:**
- **Slack command:** `/grug-reload` action handler that re-runs `load_prompt_files` and swaps `base_prompt`. Simplest.
- **mtime check:** In `route_message`, stat the prompt files and reload if any mtime changed. Zero user action required. ~5 lines.

Recommend mtime check — it is invisible and prevents the footgun entirely.

---

## 3. Routing trace log

**Why:** When Grug picks the wrong tool, there is currently no record of what Gemma saw or what it emitted. The "ambiguous board" bug would have been diagnosed in seconds with a log.

**Shape:** Append one JSON line per routing decision to `./brain/routing_trace.jsonl`:
```json
{"ts": "...", "user_msg": "...", "tool": "...", "args": {...}, "confidence": 10, "compression": "FULL"}
```

Add in `route_message` right after the JSON parse succeeds. No PII concerns beyond what is already in the SQLite store.

---

## 4. Offline prompt test harness

**Why:** Every prompt change currently requires: restart process → open Slack → type test phrases → eyeball output. Slow and non-repeatable.

**Shape:** A small CLI (`scripts/test_prompts.py`) that:
- Loads a fixture file of `(user_message, expected_tool)` pairs (YAML or JSON).
- Calls `router.route_message` directly with a stub context.
- Prints a pass/fail table.

Fixtures live in `tests/prompt_fixtures.yaml` and should cover: the greeting cases, the board summary variants, factual trivia, destructive task creation, and the "missing info" clarification path. This becomes the regression safety net for all future prompt edits.

---

## 5. Move `friendly_names` into tool registration

**Why:** [core/orchestrator.py:228-238](core/orchestrator.py#L228-L238) duplicates knowledge that belongs with the tool definition. Every new tool requires editing two places, and the dict has already gone stale once.

**Shape:** Add a `friendly_name` kwarg to `register_python_tool` / `register_cli_tool`. Store alongside schema. `execute_list_capabilities` reads it from the registry. Delete the dict.

---

## 6. Smaller incomplete items (paper cuts)

These are narrower fixes worth noting but do not need their own section.

- **[app.py:14](app.py#L14) — `PENDING` is in-memory only.** HITL approvals are lost on restart. Fine for dev, but if multiple people use Grug or restarts happen mid-day, approvals silently disappear. Fix: persist to SQLite with the existing `GrugStorage`, or at least surface "approval expired due to restart" instead of "No pending call found."

- **[app.py:109](app.py#L109) — `add_note` does not enforce the tag enum.** [prompts/rules.md](prompts/rules.md) says tags must be drawn from `[dev, personal, infra, meeting, urgent, draft, misc]`, but the schema accepts any string. Gemma can (and eventually will) hallucinate tags. Fix: add `"enum": [...]` to the `tags.items` schema.

- **[app.py:232](app.py#L232) — `compression_mode` is hardcoded to `"FULL"`.** The system prompt supports `LITE` / `FULL` / `ULTRA`, but there is no way for the user to change it. Fix: Slack slash command (`/grug-mode ultra`) or per-user preference in SQLite.

- **[app.py:81](app.py#L81) — `_backlog_browser_proc` is a module global.** On process restart the new process has no idea the old dashboard is still running on port 6420 and will try to spawn another. Fix: on startup, probe the port; if it is answering, assume it is ours and skip spawn. Or write a pidfile.

- **[core/orchestrator.py:13](core/orchestrator.py#L13) — `memory.md` is loaded but barely used.** Currently a near-empty user-filled template that adds tokens to every prompt without earning them. Decision: either populate it with durable project facts (team names, aliases, typical workflows) or drop it from `load_prompt_files` entirely.

- **No tests anywhere.** The prompt harness in §4 is the obvious starting point. After that, unit tests for `ToolRegistry.execute` (schema validation, destructive gating) are the next highest leverage.

- **Backlog dashboard is not reachable from other machines on the LAN.** The docker-compose port mapping at [docker-compose.yml:10](docker-compose.yml#L10) already publishes `6420:6420`, so the host interface is exposed — the problem is almost certainly that `backlog browser` binds to `127.0.0.1` inside the container by default, which means Docker's port forward lands on a loopback nothing outside the container can reach. Fix in two steps:
    1. Check `backlog browser --help` inside the container for a host/bind flag (likely `--host` or `--hostname`). Add it to the Popen args in [app.py:89](app.py#L89) so the command becomes `backlog browser --port <port> --host 0.0.0.0 --no-open`. Make the bind address configurable via a `BACKLOG_DASHBOARD_HOST` env var that defaults to `0.0.0.0` under Docker and `127.0.0.1` otherwise — you do not want the local-dev path listening on all interfaces by accident.
    2. If the CLI has no such flag, fall back to running it behind a tiny reverse proxy (caddy or nginx) in the same container, or a second service in compose that proxies `0.0.0.0:6420` to `grug_core:6420`. Heavier, but unblocks LAN access.
    Also: once the bind is correct, the dashboard will be reachable at `http://<host-LAN-IP>:6420` — update the string returned by `backlog_start_browser` in [app.py:87](app.py#L87) and [app.py:95](app.py#L95) to show the LAN-usable URL (or at least note that localhost is host-only), otherwise users on other machines will keep copy-pasting `http://localhost:6420` and wondering why it fails. Security note: exposing the dashboard to the LAN means anyone on the network can edit tasks — fine for a home/office network, but worth flagging before turning it on in less-trusted environments.

- **Backlog list/summary commands fail with raw CalledProcessError.** Observed in two shapes: `Command '['backlog', 'task', 'list', '--plain', '-s', 'Todo']' returned non-zero exit status 1` and (no filter at all) `Grug cannot see board: Command '['backlog', 'task', 'list', '--plain']' returned non-zero exit status 1`. The unfiltered case proves this is not just a bad status value — something more fundamental is broken about how the CLI is invoked, and the stderr that would tell us what is being swallowed. Three stacked problems, fix in this order:
    1. **(Prerequisite) Useless error surfacing.** [core/orchestrator.py:76](core/orchestrator.py#L76) catches `Exception` in the python-tool branch and returns `str(e)`, which for `CalledProcessError` drops the captured `e.output` — exactly the stderr we need to diagnose the CLI failure. Fix: in the python-tool branch of `execute`, detect `CalledProcessError` specifically and include `e.output` in the returned message (something like `f"{e}\n---stderr---\n{e.output}"`). Until this is done, every diagnosis of the CLI failure is guesswork. This blocks step 2.
    2. **Root cause of the CLI failure (diagnose once step 1 is done).** Likely candidates:
        - **Working directory mismatch.** [app.py:25](app.py#L25) sets `BACKLOG_CWD=/app` under Docker, but the mounted data is at `/app/backlog` per [docker-compose.yml:15](docker-compose.yml#L15). The `backlog init` at [app.py:30](app.py#L30) runs without an explicit `cwd=` and may initialize into the wrong place (or silently fail), leaving the subsequent `task list` pointing at a project directory that does not exist. Worth passing `cwd=_BACKLOG_CWD` explicitly and capturing the init output instead of throwing it away.
        - **Init never ran successfully.** The `subprocess.run` at [app.py:30](app.py#L30) uses `capture_output=True` but never checks `returncode` or logs stderr. If init failed (permissions, mount race, CLI version mismatch), every subsequent command fails and nobody knows. Fix: log init stdout/stderr and fail loudly on non-zero exit.
        - **Permissions on the mounted volume.** The container runs as `user: "1000:1000"` ([docker-compose.yml:7](docker-compose.yml#L7)). If `./backlog` on the host is owned by a different UID, the CLI cannot write state files. `ls -lan backlog/` on the host will tell you.
    3. **Wrong status token (separate issue, now lower priority).** Backlog.md's default columns use `"To Do"` (with a space), not `"Todo"`. Even once step 2 is fixed, the filtered list will still fail for this reason. Normalize all status filter examples in [prompts/schema_examples.md](prompts/schema_examples.md) to the exact column names, pin the allowed values in [prompts/rules.md](prompts/rules.md) (`"To Do"`, `"In Progress"`, `"Done"`), and consider a small normalizer in [app.py:47](app.py#L47) `backlog_list_tasks` that maps common variants (`todo` → `To Do`) so Gemma's near-misses still work.

---

## Ordering suggestion

If tackled in order, each step compounds:

1. **Routing trace log** (§3) — gives you data to debug everything else.
2. **Hot-reload prompts** (§2) — removes the biggest iteration-time footgun.
3. **Offline prompt test harness** (§4) — makes prompt edits safe to make quickly.
4. **`friendly_names` into registration** (§5) — small cleanup, prevents future staleness.
5. **Category tags** (§1, Option A) — defer Option B until you actually feel the pain.
6. **Paper cuts** (§6) — as they bite.
