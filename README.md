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
