import os
import glob
import json
import sqlite3
import threading
import time
from sentence_transformers import SentenceTransformer

HAS_VSS = hasattr(sqlite3.Connection, "enable_load_extension")
if HAS_VSS:
    try:
        import sqlite_vss
    except ImportError:
        HAS_VSS = False

class VectorMemory:
    def __init__(self, db_path="/app/brain/memory.db", model_name="all-MiniLM-L6-v2"):
        self.db_path = db_path
        self._lock = threading.Lock()  # H6: Thread safety for shared SQLite connection
        _vss_enabled = os.getenv("VECTORS_LOAD_EXTENSION") == "1"  # M4: Explicit opt-in
        if not HAS_VSS:
            print("WARNING: Vector search disabled (missing sqlite-vss or macOS limits sqlite3).")
            self.model = None
        elif not _vss_enabled:
            print("WARNING: Vector search disabled (VECTORS_LOAD_EXTENSION != 1).")
            self.model = None
        else:
            # M3: Pin model revision for reproducible builds
            self.model = SentenceTransformer(model_name, revision="c5f93f70e82bc3c30e7a1a3ada002cd3c3543307")
            self.embedding_dim = self.model.get_embedding_dimension()
            self._init_db()

    def _init_db(self):
        if not HAS_VSS:
            return
        """Initializes the SQLite database with VSS extensions for vector search."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with self._lock:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            if os.getenv("VECTORS_LOAD_EXTENSION") == "1":  # M4: guarded
                self.conn.enable_load_extension(True)
                sqlite_vss.load(self.conn)
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
            
            # VSS virtual table for embeddings
            cursor.execute(f'''
                CREATE VIRTUAL TABLE IF NOT EXISTS vss_blocks USING vss0(
                    embedding({self.embedding_dim})
                )
            ''')
            self.conn.commit()

    def index_markdown_directory(self, watch_dir="/app/brain/daily_notes"):
        if not HAS_VSS:
            return
        """Reads markdown files, extracts blocks, generates embeddings, and saves them."""
        # Check if directory exists
        if not os.path.exists(watch_dir):
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
                        continue # Skip if we already indexed this exact thought
                    
                    # Insert text block
                    db_cursor.execute('INSERT INTO blocks (file_path, content) VALUES (?, ?)', (file_path, block))
                    block_id = db_cursor.lastrowid
                    
                    # Generate and insert embedding
                    embedding = self.model.encode(block).tolist()
                    db_cursor.execute('INSERT INTO vss_blocks(rowid, embedding) VALUES (?, ?)', 
                                      (block_id, json.dumps(embedding)))
            self.conn.commit()

    def start_background_indexer(self, watch_dir="/app/brain/daily_notes", interval_seconds=None):
        """Spawn a daemon thread that periodically re-indexes the markdown directory."""
        if not HAS_VSS:
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
        if not HAS_VSS:
            return [{"content": "Vector memory offline. Use local markdown context instead.", "distance": 0.0}]
        """Perform semantic search against the indexed markdown blocks."""
        query_embedding = self.model.encode(query).tolist()
        
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT b.content, v.distance 
                FROM (
                    SELECT rowid, distance 
                    FROM vss_blocks
                    WHERE vss_search(embedding, ?)
                    LIMIT ?
                ) v
                JOIN blocks b ON b.id = v.rowid
            ''', (json.dumps(query_embedding), limit))
            
            return [{"content": row["content"], "distance": row["distance"]} for row in cursor.fetchall()]

    def __del__(self):
        if hasattr(self, 'conn'):
            self.conn.close()
