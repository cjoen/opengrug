import sqlite3
import sqlite_vss
import os
import glob
import json
from sentence_transformers import SentenceTransformer

class VectorMemory:
    def __init__(self, db_path="/app/brain/memory.db", model_name="all-MiniLM-L6-v2"):
        self.db_path = db_path
        # SentenceTransformers downloads and caches the model locally on first run
        self.model = SentenceTransformer(model_name)
        self.embedding_dim = self.model.get_sentence_embedding_dimension()
        self._init_db()

    def _init_db(self):
        """Initializes the SQLite database with VSS extensions for vector search."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        self.conn = sqlite3.connect(self.db_path)
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
        """Reads markdown files, extracts blocks, generates embeddings, and saves them."""
        # Check if directory exists
        if not os.path.exists(watch_dir):
            return

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

    def query_memory(self, query: str, limit: int = 5):
        """Perform semantic search against the indexed markdown blocks."""
        query_embedding = self.model.encode(query).tolist()
        
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT b.content, v.distance 
            FROM vss_blocks v
            JOIN blocks b ON b.id = v.rowid
            WHERE vss_search(v.embedding, ?)
            LIMIT ?
        ''', (json.dumps(query_embedding), limit))
        
        return [{"content": row["content"], "distance": row["distance"]} for row in cursor.fetchall()]

    def __del__(self):
        if hasattr(self, 'conn'):
            self.conn.close()
