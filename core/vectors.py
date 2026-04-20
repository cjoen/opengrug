import os
import glob
import json
import struct
import sqlite3
import threading
import time
import sqlite_vec


def _serialize_embedding(vec):
    """Serialize a list/array of floats into a compact little-endian float32 blob."""
    return struct.pack(f"{len(vec)}f", *vec)


class VectorMemory:
    def __init__(self, db_path="/app/brain/memory.db", model_name="all-MiniLM-L6-v2"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._enabled = False

        try:
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(model_name)
            self.embedding_dim = self.model.get_embedding_dimension()
            self._init_db()
            self._enabled = True
        except Exception as e:
            print(f"WARNING: Vector search disabled ({e}).")
            self.model = None

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

            # Table to hold the raw text blocks and metadata
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS blocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    content TEXT NOT NULL UNIQUE
                )
            ''')

            # sqlite-vec virtual table for embeddings
            cursor.execute(f'''
                CREATE VIRTUAL TABLE IF NOT EXISTS vec_blocks USING vec0(
                    embedding float[{self.embedding_dim}]
                )
            ''')
            self.conn.commit()

    def index_markdown_directory(self, watch_dir="/app/brain/daily_notes"):
        """Reads markdown files, extracts blocks, generates embeddings, and saves them."""
        if not self._enabled:
            return

        with self._lock:
            db_cursor = self.conn.cursor()
            md_files = glob.glob(os.path.join(watch_dir, "*.md"))

            for file_path in md_files:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                # Grug-style parsing: Extract every bullet point as a separate memory block
                blocks = [line.strip() for line in content.split('\n') if line.strip().startswith("- ")]

                for block in blocks:
                    # Check if block is already indexed
                    db_cursor.execute('SELECT id FROM blocks WHERE content = ?', (block,))
                    if db_cursor.fetchone():
                        continue  # Skip if we already indexed this exact thought

                    # Insert text block
                    db_cursor.execute('INSERT INTO blocks (file_path, content) VALUES (?, ?)', (file_path, block))
                    block_id = db_cursor.lastrowid

                    # Generate and insert embedding
                    embedding = self.model.encode(block).tolist()
                    db_cursor.execute('INSERT INTO vec_blocks(rowid, embedding) VALUES (?, ?)',
                                      (block_id, _serialize_embedding(embedding)))
            self.conn.commit()

    def start_background_indexer(self, watch_dir="/app/brain/daily_notes", interval_seconds=None):
        """Spawn a daemon thread that periodically re-indexes the markdown directory."""
        if not self._enabled:
            print("[indexer] vector search disabled, background indexer not started")
            return

        if interval_seconds is None:
            interval_seconds = int(os.getenv("GRUG_INDEX_INTERVAL", "30"))

        def _loop():
            while True:
                try:
                    self.index_markdown_directory(watch_dir)
                except Exception as e:
                    print(f"[indexer] error: {e}")
                time.sleep(interval_seconds)

        thread = threading.Thread(target=_loop, daemon=True, name="grug-indexer")
        thread.start()
        print(f"[indexer] background indexer started, interval={interval_seconds}s, watching {watch_dir}")

    def query_memory(self, query: str, limit: int = 5):
        """Perform semantic search against the indexed markdown blocks."""
        if not self._enabled:
            return [{"content": "Vector memory offline. Use local markdown context instead.", "distance": 0.0, "offline": True}]

        query_embedding = self.model.encode(query).tolist()

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
