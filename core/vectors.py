import os
import glob
import struct
import sqlite3
import threading
import time
import sqlite_vec


def _serialize_embedding(vec):
    """Serialize a list/array of floats into a compact little-endian float32 blob."""
    return struct.pack(f"{len(vec)}f", *vec)


class VectorMemory:
    def __init__(self, llm_client, embedding_model: str, db_path="/app/brain/memory.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._enabled = False

        try:
            result = llm_client.get_embedding("hello", embedding_model)
            if not result:
                print("WARNING: Vector search disabled (embedding probe returned empty).")
                return
            self.llm_client = llm_client
            self.embedding_model = embedding_model
            self.embedding_dim = len(result)
            self._init_db()
            self._enabled = True
        except Exception as e:
            print(f"WARNING: Vector search disabled ({e}).")

    def _init_db(self):
        """Initializes the SQLite database with sqlite-vec for vector search."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        with self._lock:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
            self.conn.row_factory = sqlite3.Row

            cursor = self.conn.cursor()

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS file_metadata (
                    file_path TEXT PRIMARY KEY,
                    last_modified REAL NOT NULL
                )
            ''')

            # Migrate: old schema had UNIQUE on content — drop and recreate if so
            col_info = cursor.execute("PRAGMA table_info(blocks)").fetchall()
            if col_info:
                # Check for UNIQUE constraint on content column
                indexes = cursor.execute("PRAGMA index_list(blocks)").fetchall()
                has_unique = any(idx[2] == 'u' for idx in indexes)
                if has_unique:
                    cursor.execute("DROP TABLE blocks")
                    cursor.execute("DELETE FROM vec_blocks")
                    cursor.execute("DELETE FROM file_metadata")

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS blocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    content TEXT NOT NULL
                )
            ''')

            cursor.execute(f'''
                CREATE VIRTUAL TABLE IF NOT EXISTS vec_blocks USING vec0(
                    embedding float[{self.embedding_dim}]
                )
            ''')
            self.conn.commit()

    def _chunk_markdown(self, content: str, filename: str) -> list:
        chunks = []
        for chunk in content.split("\n\n"):
            chunk = chunk.strip()
            if not chunk or len(chunk) < 10:
                continue
            chunks.append(f"[{filename}] {chunk}")
        return chunks

    def index_markdown_directory(self, watch_dirs=None, watch_dir=None, extra_files=None):
        """Incrementally indexes markdown files, only re-indexing changed files.

        Accepts a list of directories (watch_dirs) or a single directory
        (watch_dir, for backwards compat). Searches recursively for .md files.
        """
        if not self._enabled:
            return

        if watch_dirs is None:
            watch_dirs = [watch_dir or "/app/brain/daily_notes"]

        with self._lock:
            cursor = self.conn.cursor()
            md_files = []
            for d in watch_dirs:
                md_files.extend(glob.glob(os.path.join(d, "**", "*.md"), recursive=True))
            if extra_files:
                md_files.extend(f for f in extra_files if os.path.isfile(f))

            now = time.time()

            for file_path in md_files:
                mtime = os.path.getmtime(file_path)

                row = cursor.execute(
                    "SELECT last_modified FROM file_metadata WHERE file_path = ?", (file_path,)
                ).fetchone()
                stored_mtime = row[0] if row else None

                if stored_mtime is not None and mtime <= stored_mtime:
                    continue

                # Debounce: skip files modified in the last 10 seconds
                if now - mtime <= 10:
                    continue

                # Garbage collect old chunks for this file
                ids = [r[0] for r in cursor.execute(
                    "SELECT id FROM blocks WHERE file_path = ?", (file_path,)
                ).fetchall()]
                if ids:
                    cursor.executemany("DELETE FROM vec_blocks WHERE rowid = ?", [(i,) for i in ids])
                    cursor.execute("DELETE FROM blocks WHERE file_path = ?", (file_path,))

                # Read and chunk
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                filename = os.path.basename(file_path)
                chunks = self._chunk_markdown(content, filename)

                for chunk in chunks:
                    embedding = self.llm_client.get_embedding(chunk, self.embedding_model)
                    if not embedding:
                        continue
                    cursor.execute(
                        "INSERT INTO blocks (file_path, content) VALUES (?, ?)", (file_path, chunk)
                    )
                    block_id = cursor.lastrowid
                    cursor.execute(
                        "INSERT INTO vec_blocks(rowid, embedding) VALUES (?, ?)",
                        (block_id, _serialize_embedding(embedding))
                    )

                cursor.execute(
                    "INSERT OR REPLACE INTO file_metadata (file_path, last_modified) VALUES (?, ?)",
                    (file_path, mtime)
                )

            # Prune deleted files
            all_tracked = [r[0] for r in cursor.execute("SELECT file_path FROM file_metadata").fetchall()]
            for path in all_tracked:
                if not os.path.exists(path):
                    ids = [r[0] for r in cursor.execute(
                        "SELECT id FROM blocks WHERE file_path = ?", (path,)
                    ).fetchall()]
                    if ids:
                        cursor.executemany("DELETE FROM vec_blocks WHERE rowid = ?", [(i,) for i in ids])
                        cursor.execute("DELETE FROM blocks WHERE file_path = ?", (path,))
                    cursor.execute("DELETE FROM file_metadata WHERE file_path = ?", (path,))

            self.conn.commit()

    def start_background_indexer(self, watch_dirs=None, watch_dir=None, extra_files=None, interval_seconds=None):
        """Spawn a daemon thread that periodically re-indexes markdown directories."""
        if not self._enabled:
            print("[indexer] vector search disabled, background indexer not started")
            return

        if watch_dirs is None:
            watch_dirs = [watch_dir or "/app/brain/daily_notes"]

        if interval_seconds is None:
            interval_seconds = int(os.getenv("GRUG_INDEX_INTERVAL", "30"))

        def _loop():
            while True:
                try:
                    self.index_markdown_directory(watch_dirs=watch_dirs, extra_files=extra_files)
                except Exception as e:
                    print(f"[indexer] error: {e}")
                time.sleep(interval_seconds)

        thread = threading.Thread(target=_loop, daemon=True, name="grug-indexer")
        thread.start()
        print(f"[indexer] background indexer started, interval={interval_seconds}s, watching {watch_dirs}")

    def query_memory(self, query: str, limit: int = 5):
        """Perform semantic search against the indexed markdown blocks.

        When called as a tool (via registry), returns a formatted string.
        Internal callers (RAG preflight) still get the list-of-dicts via
        query_memory_raw().
        """
        hits = self.query_memory_raw(query, limit=limit)
        if hits and hits[0].get("offline"):
            return "Vector memory is offline. Try the search tool instead."
        if not hits:
            return f"No memory matches found for \"{query}\"."
        lines = [f"• {h['content']}" for h in hits]
        return f"Found {len(hits)} memory match{'es' if len(hits) != 1 else ''} for \"{query}\":\n" + "\n".join(lines)

    def query_memory_raw(self, query: str, limit: int = 5):
        """Return raw list-of-dicts for internal callers (RAG, search fallback)."""
        if not self._enabled:
            return [{"content": "Vector memory offline.", "distance": 0.0, "offline": True}]

        query_embedding = self.llm_client.get_embedding(query, self.embedding_model)

        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT b.content, v.distance
                FROM (
                    SELECT rowid, distance
                    FROM vec_blocks
                    WHERE embedding MATCH ?
                      AND k = ?
                    ORDER BY distance
                ) v
                JOIN blocks b ON b.id = v.rowid
            ''', (_serialize_embedding(query_embedding), limit))

            return [{"content": row["content"], "distance": row["distance"]} for row in cursor.fetchall()]

    def stats(self):
        """Return vector memory stats for health reporting."""
        if not self._enabled:
            return {"enabled": False}
        with self._lock:
            count = self.conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
        return {
            "enabled": True,
            "block_count": count,
            "db_size": os.path.getsize(self.db_path),
        }

    def __del__(self):
        if hasattr(self, 'conn'):
            self.conn.close()
