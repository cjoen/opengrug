import os
import time
import pytest
import tempfile
from unittest.mock import MagicMock

sqlite_vec = pytest.importorskip("sqlite_vec")


def _make_mock_client(dim=4):
    client = MagicMock()
    client.get_embedding.return_value = [0.1] * dim
    return client


# ---------------------------------------------------------------------------
# _chunk_markdown
# ---------------------------------------------------------------------------

def test_chunk_markdown(tmp_path):
    client = _make_mock_client()
    db_path = str(tmp_path / "memory.db")
    vm = _make_enabled_vm(client, db_path)

    content = "Hello world paragraph one.\n\nSecond paragraph here.\n\nhi"
    chunks = vm._chunk_markdown(content, "notes.md")

    assert chunks == [
        "[notes.md] Hello world paragraph one.",
        "[notes.md] Second paragraph here.",
    ]
    # "hi" is < 10 chars and must be skipped
    assert not any("hi" in c for c in chunks)


# ---------------------------------------------------------------------------
# Init behaviour
# ---------------------------------------------------------------------------

def test_init_disabled_on_empty_embedding(tmp_path):
    client = MagicMock()
    client.get_embedding.return_value = []
    db_path = str(tmp_path / "memory.db")

    vm = _make_vm_raw(client, db_path)

    assert vm._enabled is False


def test_init_success(tmp_path):
    client = _make_mock_client(dim=8)
    db_path = str(tmp_path / "memory.db")

    vm = _make_vm_raw(client, db_path)

    assert vm._enabled is True
    assert vm.embedding_dim == 8


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

def test_index_new_file(tmp_path):
    client = _make_mock_client()
    db_path = str(tmp_path / "memory.db")
    vm = _make_enabled_vm(client, db_path)

    md = tmp_path / "note.md"
    md.write_text("First paragraph here.\n\nSecond paragraph here.")
    _set_old_mtime(str(md))

    vm.index_markdown_directory(watch_dir=str(tmp_path))

    count = vm.conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
    assert count == 2


def test_incremental_skip_unchanged(tmp_path):
    client = _make_mock_client()
    db_path = str(tmp_path / "memory.db")
    vm = _make_enabled_vm(client, db_path)

    md = tmp_path / "note.md"
    md.write_text("A long enough paragraph for the test.")
    _set_old_mtime(str(md))

    vm.index_markdown_directory(watch_dir=str(tmp_path))
    call_count_after_first = client.get_embedding.call_count

    vm.index_markdown_directory(watch_dir=str(tmp_path))
    call_count_after_second = client.get_embedding.call_count

    # Probe call happens once at init; indexing calls should not increase on second pass
    assert call_count_after_second == call_count_after_first


def test_incremental_reindex_on_change(tmp_path):
    client = _make_mock_client()
    db_path = str(tmp_path / "memory.db")
    vm = _make_enabled_vm(client, db_path)

    md = tmp_path / "note.md"
    md.write_text("Original paragraph content.")
    _set_old_mtime(str(md))

    vm.index_markdown_directory(watch_dir=str(tmp_path))
    count_after_first = vm.conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]

    # Modify file with two paragraphs and push mtime into the past
    md.write_text("Updated paragraph one.\n\nUpdated paragraph two.")
    _set_old_mtime(str(md), offset=20)

    vm.index_markdown_directory(watch_dir=str(tmp_path))
    count_after_second = vm.conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]

    assert count_after_first == 1
    assert count_after_second == 2


def test_prune_deleted_file(tmp_path):
    client = _make_mock_client()
    db_path = str(tmp_path / "memory.db")
    vm = _make_enabled_vm(client, db_path)

    md = tmp_path / "note.md"
    md.write_text("A long enough paragraph for pruning.")
    _set_old_mtime(str(md))

    vm.index_markdown_directory(watch_dir=str(tmp_path))
    assert vm.conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0] == 1
    assert vm.conn.execute("SELECT COUNT(*) FROM file_metadata").fetchone()[0] == 1

    md.unlink()
    vm.index_markdown_directory(watch_dir=str(tmp_path))

    assert vm.conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0] == 0
    assert vm.conn.execute("SELECT COUNT(*) FROM file_metadata").fetchone()[0] == 0


def test_debounce_skips_recent(tmp_path):
    client = _make_mock_client()
    db_path = str(tmp_path / "memory.db")
    vm = _make_enabled_vm(client, db_path)

    md = tmp_path / "note.md"
    md.write_text("A long enough paragraph to be indexed.")
    # Leave mtime as-is (just written, i.e. NOW — within the 10-second debounce window)

    vm.index_markdown_directory(watch_dir=str(tmp_path))

    count = vm.conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
    assert count == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vm_raw(client, db_path):
    """Instantiate VectorMemory directly without patching os.makedirs."""
    from core.vectors import VectorMemory
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return VectorMemory(client, "nomic-embed-text", db_path=db_path)


def _make_enabled_vm(client, db_path):
    vm = _make_vm_raw(client, db_path)
    assert vm._enabled, "VectorMemory failed to initialise — check mock client"
    return vm


def _set_old_mtime(path, offset=30):
    """Set file mtime to `offset` seconds in the past so debounce passes."""
    old_time = time.time() - offset
    os.utime(path, (old_time, old_time))
