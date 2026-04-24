# Build Plan: Obsidian Vault RAG Sync

**Status:** Ready to implement
**Priority:** High (Prerequisite for handling a real Obsidian vault without crashing)
**Goal:** Replace the naive bullet-point vector indexer with an incremental, garbage-collected Markdown chunker backed by Ollama embeddings.

---

## 1. Database Schema Updates (Incremental Tracking)
Currently, `sqlite-vec` memory relies on a fragile `UNIQUE` text constraint and doesn't know when files change.

**Schema Changes:**
*   Create a `file_metadata` table to track file modification times.
    ```sql
    CREATE TABLE IF NOT EXISTS file_metadata (
        file_path TEXT PRIMARY KEY,
        last_modified REAL NOT NULL
    )
    ```
*   Update the `blocks` table to safely allow duplicate text across different files, and tie blocks to their parent file so they can be garbage collected.

## 2. The Incremental Sync Algorithm
The background indexer must stop brute-forcing the entire disk every 30 seconds.

**The Sync Loop:**
1.  **Scan Disk:** Get all `.md` files in the vault.
2.  **Check Modifications & Debounce:** For each file, check `os.path.getmtime(file)`. Compare this against `last_modified` in `file_metadata`. Additionally, verify that `CurrentTime - mtime > 10` seconds to debounce actively editing files (this prevents the indexer from chunking malformed Markdown while you are actively typing a note).
3.  **Process Changed/New Files:**
    *   If newer (or new):
        *   `DELETE FROM blocks WHERE file_path = ?` (This instantly deletes orphaned data from previous versions of the note).
        *   Chunk the new text.
        *   Generate embeddings and insert new blocks.
        *   `UPSERT` the new `last_modified` timestamp.
4.  **Prune Deleted Files:** Get all `file_path`s from `file_metadata`. If the file no longer exists on disk, `DELETE` from `blocks` and `file_metadata`.

## 3. Intelligent Markdown Chunking
Grug currently only indexes lines starting with `- `. Obsidian notes have paragraphs, headers, and code blocks.

**The Fix:**
Implement a `_chunk_markdown(content, filename)` function.
*   **Strategy:** Split the text by double-newlines (`\n\n`) to capture whole paragraphs and header blocks.
*   **Context Injection:** Prepend the chunk with the filename. For example, a chunk from `recipes.md` should look like: `[recipes.md] Add 2 cups of sugar...`. This gives the LLM crucial context about *where* the memory came from when it gets retrieved via RAG.

## 4. Migrate to Ollama Embeddings
Remove the heavy `sentence_transformers` library from RAM.

**The Fix:**
*   Add a `get_embedding(text)` method to `core/llm.py` (`OllamaClient`).
*   Hit the `http://localhost:11434/api/embeddings` endpoint.
*   *Note:* Different Ollama embedding models have different dimensions (e.g., `nomic-embed-text` is 768d, `mxbai-embed-large` is 1024d). `core/vectors.py` must dynamically set the `sqlite-vec` float dimension during `_init_db` based on the configured model.

---

## Rollout Steps
1.  **Update `OllamaClient`:** Add the `/api/embeddings` network call.
2.  **Refactor `core/vectors.py` schema:** Add the `file_metadata` table and drop the `SentenceTransformer` import.
3.  **Implement Chunking & Sync:** Write the incremental `getmtime` loop and the paragraph chunker.
4.  **Wipe and Rebuild:** Because the schema and embedding model dimensions will change, the migration should simply delete the old `memory.db` file on boot and let the new indexer rebuild the vault from scratch in seconds.
