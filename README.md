# 🪨 Grug: The Caveman Context Router

Grug is a lightweight, edge-first "OpenClaw" clone designed for speed, portability, and zero-bullshit token compression. 

Unlike heavy cloud memory systems that hide your thoughts in opaque databases, Grug uses **Markdown as Truth** and **SQLite as Cache**. You write plain text, and Grug seamlessly vectors it for semantic search.

## 🔥 Quick Start

Just run the wizard. It will ask for your keys, carve out your local memory caves, and start Docker.
```bash
chmod +x setup.sh
./setup.sh
```

## 🧠 Philosophy

1. **Lightweight & Portable**: Everything runs entirely in a `docker-compose` sandbox. Moving machines? Just zip the `/brain` folder and `docker-compose up` on your new host.
2. **"Caveman" Token Compression**: Edge models like Gemma e4b run fast but have strict context lengths. By default, Grug compresses system prompts using maximum brevity ("Inline obj prop -> new ref -> useMemo") to save precious tokens.
3. **No Arbitrary Bash (Fixing OpenClaw)**: The AI is banned from arbitrary execution. Instead, Grug safely maps the LLM's JSON into strict Python arguments and whitelisted CLI binaries (`core/orchestrator.py`), pausing for Human-in-the-Loop (HITL) approval on anything destructive.
4. **Graceful Frontier Degradation**: Grug tries to route complex tasks to Claude. If you are offline or have no API key, Grug intercepts the failure and forces Gemma to provide a best-effort local answer instead of crashing.

## 📁 Storage
All of your memories are saved to `/brain/daily_notes/`. 
If something ever gets corrupted or you run into a sync error, **forget the database**. Open up the markdown file, edit the text directly, and Grug's background `VectorMemory` daemon will automatically detect the changes and re-index the cache.

## Host volume permissions

The container runs as UID 1000 (non-root). Before the first `docker-compose up`, make sure the `./brain` host directory is writable by UID 1000:

```bash
sudo chown -R 1000:1000 ./brain
```

If you need to match a different host UID, pass `--build-arg UID=<your-uid> --build-arg GID=<your-gid>` to `docker build` and update the `user:` field in `docker-compose.yml` accordingly.

## 🛠️ The Anatomy of a "Grug-Friendly" CLI

Because the orchestrator routes LLM JSON payloads directly into subprocess binaries within your Docker container, not all CLIs are created equal. To know if a CLI can be effortlessly added to the `ToolRegistry`, it must pass these four criteria:

1. **Stateless/Background Auth**: The CLI must authenticate seamlessly via Environment Variables (passed down through `docker-compose.yml`) or via mounted config credentials (e.g., a read-only `~/.aws/credentials` volume mount). If the CLI frequently demands interactive browser-based OAuth (`"Press Enter to open your browser..."`), the subprocess will hang forever.
2. **Deterministic Outputs (JSON/YAML)**: Human-readable colorful ASCII tables are the enemy of edge-first LLMs. Your CLI **must** support a structured output flag like `--output json` or `--format json`. Passing raw JSON back to Gemma ensures it accurately parses the response without hallucinating data columns.
3. **Strictly Non-Interactive**: The CLI must accept all parameters via flags (e.g., `--title "Meeting" --time "Tomorrow"`). Any CLI that routinely pauses to ask `"Are you sure you want to proceed? [y/N]"` will block the python execution thread endlessly. *(Pro tip: Always bake a `--yes` or `--quiet` flag into the `ToolRegistry` base-command config).*
4. **Predictable Exit Codes**: A well-behaved CLI returns an exit status of `0` on success, and `> 0` on failure. This ensures the `subprocess` hook correctly catches the exception and gracefully routes the failure tracebacks back to the LLM for self-correction.
