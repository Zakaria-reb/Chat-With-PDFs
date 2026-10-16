"""Stockage SQLite : documents + passages (texte / image) + embeddings (float32)."""
import sqlite3
import time
import uuid
from contextlib import contextmanager

import numpy as np

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,          -- queued | processing | ready | error
    stage TEXT DEFAULT '',
    progress INTEGER DEFAULT 0,
    error TEXT DEFAULT '',
    pages INTEGER DEFAULT 0,
    n_text INTEGER DEFAULT 0,
    n_images INTEGER DEFAULT 0,
    size INTEGER DEFAULT 0,
    created_at REAL,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    kind TEXT NOT NULL,            -- text | image
    page INTEGER DEFAULT 1,
    content TEXT NOT NULL,         -- texte original, ou description de l'image
    summary TEXT DEFAULT '',
    image_file TEXT DEFAULT '',
    embedding BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_doc ON items(doc_id);
"""


@contextmanager
def db():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with db() as c:
        c.executescript(SCHEMA)
        # Au redémarrage, un document resté "en cours" n'a jamais fini : on le signale.
        c.execute(
            "UPDATE documents SET status='error', error='Indexation interrompue (redémarrage). "
            "Clique sur Réindexer.' WHERE status IN ('queued','processing')"
        )


# ------------------------------------------------------------------ documents
def new_id() -> str:
    return uuid.uuid4().hex[:12]


def create_document(name: str, size: int, doc_id: str | None = None) -> str:
    doc_id = doc_id or new_id()
    now = time.time()
    with db() as c:
        c.execute(
            "INSERT INTO documents (id,name,status,stage,progress,size,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (doc_id, name, "queued", "En attente", 0, size, now, now),
        )
    return doc_id


def update_document(doc_id: str, **fields):
    if not fields:
        return
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    with db() as c:
        c.execute(f"UPDATE documents SET {cols} WHERE id=?", (*fields.values(), doc_id))


def get_document(doc_id: str):
    with db() as c:
        row = c.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    return dict(row) if row else None


def find_by_name(name: str):
    with db() as c:
        row = c.execute(
            "SELECT * FROM documents WHERE lower(name)=lower(?)", (name,)
        ).fetchone()
    return dict(row) if row else None


def list_documents() -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def delete_document(doc_id: str):
    with db() as c:
        c.execute("DELETE FROM items WHERE doc_id=?", (doc_id,))
        c.execute("DELETE FROM documents WHERE id=?", (doc_id,))


# ------------------------------------------------------------------ items
def replace_items(doc_id: str, items: list[dict]):
    """Remplace d'un coup tous les passages d'un document (l'ancien contenu reste
    consultable jusqu'à cet instant)."""
    with db() as c:
        c.execute("DELETE FROM items WHERE doc_id=?", (doc_id,))
        c.executemany(
            "INSERT INTO items (doc_id,kind,page,content,summary,image_file,embedding) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                (
                    doc_id,
                    it["kind"],
                    it["page"],
                    it["content"],
                    it.get("summary", ""),
                    it.get("image_file", ""),
                    np.asarray(it["embedding"], dtype=np.float32).tobytes(),
                )
                for it in items
            ],
        )


def load_vectors(doc_ids: list[str] | None):
    """Retourne (lignes, matrice normalisée) pour les documents prêts demandés."""
    q = (
        "SELECT i.id,i.doc_id,i.kind,i.page,i.content,i.image_file,i.embedding,d.name "
        "FROM items i JOIN documents d ON d.id=i.doc_id"
    )
    params: list = []
    if doc_ids:
        q += f" WHERE i.doc_id IN ({','.join('?' * len(doc_ids))})"
        params = list(doc_ids)
    with db() as c:
        rows = c.execute(q, params).fetchall()
    if not rows:
        return [], np.zeros((0, 1), dtype=np.float32)
    mat = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return [dict(r) for r in rows], mat / norms


def stats() -> dict:
    with db() as c:
        d = c.execute(
            "SELECT COUNT(*) n, SUM(status='ready') ready, SUM(status IN ('queued','processing')) busy "
            "FROM documents"
        ).fetchone()
        t = c.execute(
            "SELECT SUM(kind='text') t, SUM(kind='image') i FROM items"
        ).fetchone()
    return {
        "documents": d["n"] or 0,
        "ready": d["ready"] or 0,
        "busy": d["busy"] or 0,
        "passages": t["t"] or 0,
        "images": t["i"] or 0,
    }
