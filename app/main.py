"""API FastAPI + service des fichiers statiques du dashboard."""
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, ingest, rag, store

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD_MB = 100

app = FastAPI(title="RAG PDF Dashboard")
store.init()


# ------------------------------------------------------------------ helpers
def _save_upload(upload: UploadFile, doc_id: str) -> int:
    dest = ingest.pdf_path(doc_id)
    tmp = dest.with_suffix(".tmp")
    size = 0
    with open(tmp, "wb") as fh:
        while chunk := upload.file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_MB * 1024 * 1024:
                fh.close()
                tmp.unlink(missing_ok=True)
                raise HTTPException(413, f"Fichier trop gros (max {MAX_UPLOAD_MB} Mo).")
            fh.write(chunk)
    with open(tmp, "rb") as fh:
        if fh.read(5) != b"%PDF-":
            tmp.unlink(missing_ok=True)
            raise HTTPException(400, f"« {upload.filename} » n'est pas un PDF valide.")
    tmp.replace(dest)
    return size


def _check_pdf_name(name: str | None):
    if not name or not name.lower().endswith(".pdf"):
        raise HTTPException(400, f"« {name} » : seuls les fichiers .pdf sont acceptés.")


# ------------------------------------------------------------------ API
@app.get("/api/status")
def status():
    return {
        "demo_mode": config.DEMO_MODE,
        "models": {"texte": config.TEXT_MODEL, "vision": config.VISION_MODEL, "embeddings": config.EMBED_MODEL},
        "stats": store.stats(),
    }


@app.get("/api/documents")
def documents():
    return {"documents": store.list_documents(), "stats": store.stats()}


@app.post("/api/documents")
def add_documents(files: list[UploadFile] = File(...), on_duplicate: str = Form("replace")):
    """Ajoute des PDF. Si un fichier du même nom existe : on_duplicate = replace | skip."""
    results = []
    for up in files:
        _check_pdf_name(up.filename)
        existing = store.find_by_name(up.filename)
        if existing:
            if on_duplicate == "skip":
                results.append({"name": up.filename, "action": "skipped"})
                continue
            if ingest.is_running(existing["id"]):
                raise HTTPException(409, f"« {up.filename} » est en cours d'indexation, réessaie dans un instant.")
            size = _save_upload(up, existing["id"])
            store.update_document(existing["id"], status="queued", stage="En attente", progress=0, error="", size=size)
            ingest.submit(existing["id"])
            results.append({"name": up.filename, "id": existing["id"], "action": "replaced"})
        else:
            doc_id = store.new_id()
            size = _save_upload(up, doc_id)
            store.create_document(up.filename, size, doc_id)
            ingest.submit(doc_id)
            results.append({"name": up.filename, "id": doc_id, "action": "added"})
    return {"results": results}


@app.post("/api/documents/{doc_id}/replace")
def replace_document(doc_id: str, file: UploadFile = File(...)):
    """Remplace le contenu d'un document existant par un autre PDF (le nom est mis à jour)."""
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document introuvable.")
    _check_pdf_name(file.filename)
    other = store.find_by_name(file.filename)
    if other and other["id"] != doc_id:
        raise HTTPException(409, f"Un autre document s'appelle déjà « {file.filename} ».")
    if ingest.is_running(doc_id):
        raise HTTPException(409, "Ce document est en cours d'indexation.")
    size = _save_upload(file, doc_id)
    store.update_document(doc_id, name=file.filename, status="queued", stage="En attente", progress=0, error="", size=size)
    ingest.submit(doc_id)
    return {"id": doc_id, "action": "replaced"}


@app.post("/api/documents/{doc_id}/reindex")
def reindex_document(doc_id: str):
    if not store.get_document(doc_id):
        raise HTTPException(404, "Document introuvable.")
    if ingest.is_running(doc_id):
        raise HTTPException(409, "Déjà en cours.")
    if not ingest.pdf_path(doc_id).exists():
        raise HTTPException(410, "Le fichier PDF n'existe plus : remplace-le.")
    store.update_document(doc_id, status="queued", stage="En attente", progress=0, error="")
    ingest.submit(doc_id)
    return {"id": doc_id}


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str):
    if not store.get_document(doc_id):
        raise HTTPException(404, "Document introuvable.")
    store.delete_document(doc_id)
    ingest.pdf_path(doc_id).unlink(missing_ok=True)
    shutil.rmtree(config.IMAGE_DIR / doc_id, ignore_errors=True)
    return {"deleted": doc_id}


@app.get("/api/documents/{doc_id}/file")
def document_file(doc_id: str):
    doc = store.get_document(doc_id)
    path = ingest.pdf_path(doc_id)
    if not doc or not path.exists():
        raise HTTPException(404, "Fichier introuvable.")
    return FileResponse(path, media_type="application/pdf", filename=doc["name"], content_disposition_type="inline")


@app.get("/api/images/{doc_id}/{run_id}/{name}")
def image(doc_id: str, run_id: str, name: str):
    base = (config.IMAGE_DIR / doc_id).resolve()
    path = (base / run_id / name).resolve()
    if base not in path.parents or not path.is_file():  # anti path-traversal
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


class ChatRequest(BaseModel):
    question: str
    doc_ids: list[str] | None = None
    history: list[dict] = []


@app.post("/api/chat")
def chat(req: ChatRequest):
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "Question vide.")
    ready = {d["id"] for d in store.list_documents() if d["status"] == "ready"}
    scope = [i for i in (req.doc_ids or ready) if i in ready]
    if not scope:
        return {"answer": "Aucun document prêt : ajoute un PDF (ou attends la fin de l'indexation).", "sources": []}
    try:
        return rag.answer(question, scope, req.history)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Erreur du modèle : {exc}") from exc


# ------------------------------------------------------------------ front
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
