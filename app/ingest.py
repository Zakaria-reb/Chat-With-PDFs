"""Indexation d'un PDF : texte -> blocs, images -> descriptions, puis embeddings."""
import base64
import re
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from . import config, models, store

# 2 PDF indexés en parallèle au maximum (évite de saturer l'API et le RateLimit)
_pool = ThreadPoolExecutor(max_workers=2)
_running: set[str] = set()
_lock = threading.Lock()


def pdf_path(doc_id: str) -> Path:
    return config.UPLOAD_DIR / f"{doc_id}.pdf"


def is_running(doc_id: str) -> bool:
    with _lock:
        return doc_id in _running


def submit(doc_id: str):
    with _lock:
        _running.add(doc_id)
    _pool.submit(_run, doc_id)


# ------------------------------------------------------------------ découpage
def split_text(text: str, max_chars: int, min_chars: int) -> list[str]:
    """Découpe en blocs de ~max_chars en respectant paragraphes puis phrases."""
    text = re.sub(r"[ \t]+", " ", text.replace("\r", ""))
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    units: list[str] = []
    for para in paragraphs:
        para = re.sub(r"\s*\n\s*", " ", para)
        if len(para) <= max_chars:
            units.append(para)
            continue
        for sent in re.split(r"(?<=[.!?])\s+", para):
            while len(sent) > max_chars:  # phrase géante : coupe dure
                units.append(sent[:max_chars])
                sent = sent[max_chars:]
            if sent:
                units.append(sent)
    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + len(unit) + 1 > max_chars:
            chunks.append(current)
            current = unit
        else:
            current = f"{current} {unit}".strip()
    if current:
        if chunks and len(current) < min_chars and len(chunks[-1]) + len(current) + 1 <= max_chars:
            chunks[-1] = f"{chunks[-1]} {current}"
        else:
            chunks.append(current)
    return chunks


def _jpeg_b64(img: Image.Image) -> str | None:
    if min(img.size) < config.MIN_IMAGE_SIDE:
        return None
    img = img.convert("RGB")
    img.thumbnail((1600, 1600))  # limite la taille envoyée au modèle de vision
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


# ------------------------------------------------------------------ pipeline
def _run(doc_id: str):
    try:
        _index(doc_id)
    except Exception as exc:  # noqa: BLE001 - on remonte l'erreur à l'interface
        if store.get_document(doc_id):
            store.update_document(doc_id, status="error", stage="Échec", error=str(exc)[:500])
    finally:
        with _lock:
            _running.discard(doc_id)


def _index(doc_id: str):
    doc = store.get_document(doc_id)
    if not doc:
        return
    store.update_document(doc_id, status="processing", stage="Lecture du PDF", progress=3, error="")

    reader = PdfReader(str(pdf_path(doc_id)))
    if reader.is_encrypted and not reader.decrypt(""):
        raise RuntimeError("PDF protégé par mot de passe.")
    n_pages = len(reader.pages)

    # 1) Texte, page par page ---------------------------------------------
    items: list[dict] = []
    first_page_text = ""
    for pno, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if pno == 1:
            first_page_text = text
        for chunk in split_text(text, config.CHUNK_MAX_CHARS, config.CHUNK_MIN_CHARS):
            items.append({"kind": "text", "page": pno, "content": chunk})
        store.update_document(doc_id, progress=3 + int(25 * pno / max(n_pages, 1)))

    # Fiche d'identité du document : répond aux questions "qui a écrit / de quoi parle ce
    # document" (titre, auteur, nb de pages) même quand le texte ne le dit pas clairement.
    meta = reader.metadata or {}
    card = (
        f"Fiche du document : {doc['name']}\n"
        f"Titre : {meta.get('/Title') or 'non renseigné'}\n"
        f"Auteur (métadonnées PDF) : {meta.get('/Author') or 'non renseigné'}\n"
        f"Nombre de pages : {n_pages}\n\n"
        f"Début du document :\n{first_page_text[:900]}"
    )
    items.insert(0, {"kind": "text", "page": 1, "content": card})
    n_text = len(items)

    # 2) Images -------------------------------------------------------------
    store.update_document(doc_id, stage="Extraction des images", progress=30)
    run_id = uuid.uuid4().hex[:8]
    img_dir = config.IMAGE_DIR / doc_id / run_id
    images: list[tuple[int, str]] = []  # (page, base64)
    for pno, page in enumerate(reader.pages, start=1):
        if len(images) >= config.MAX_IMAGES_PER_DOC:
            break
        try:
            page_images = list(page.images)
        except Exception:  # noqa: BLE001 - certaines images exotiques font planter pypdf
            continue
        for pimg in page_images:
            if len(images) >= config.MAX_IMAGES_PER_DOC:
                break
            try:
                b64 = _jpeg_b64(pimg.image)
            except Exception:  # noqa: BLE001
                continue
            if b64:
                images.append((pno, b64))

    # 3) Description des images (modèle de vision) ---------------------------
    img_dir.mkdir(parents=True, exist_ok=True)
    for k, (pno, b64) in enumerate(images):
        store.update_document(
            doc_id,
            stage=f"Description des images ({k + 1}/{len(images)})",
            progress=35 + int(25 * k / max(len(images), 1)),
        )
        try:
            desc = models.describe_image(b64, pno)
        except models.ModelError as exc:
            desc = ""
            print(f"[ingest] image {k + 1} ignorée : {exc}")
        if not desc.strip():
            continue
        fname = f"{run_id}/{k}.jpg"
        (config.IMAGE_DIR / doc_id / fname).write_bytes(base64.b64decode(b64))
        items.append({"kind": "image", "page": pno, "content": desc, "image_file": fname})

    # 4) (optionnel) résumés des textes, comme dans le notebook ----------------
    texts_idx = [i for i, it in enumerate(items) if it["kind"] == "text"]
    if config.SUMMARIZE_TEXT:
        for k, i in enumerate(texts_idx):
            store.update_document(
                doc_id,
                stage=f"Résumé des blocs ({k + 1}/{len(texts_idx)})",
                progress=60 + int(15 * k / max(len(texts_idx), 1)),
            )
            items[i]["summary"] = models.summarize_text(items[i]["content"])

    # 5) Embeddings ------------------------------------------------------------
    store.update_document(doc_id, stage="Calcul des embeddings", progress=78)
    to_embed = [(it.get("summary") or it["content"]) for it in items]
    vectors = models.embed(to_embed, "passage")
    for it, vec in zip(items, vectors):
        it["embedding"] = vec

    # 6) Écriture atomique : l'ancien contenu (cas "remplacer") disparaît d'un coup ----
    if not store.get_document(doc_id):  # supprimé pendant l'indexation
        shutil.rmtree(config.IMAGE_DIR / doc_id, ignore_errors=True)
        return
    store.replace_items(doc_id, items)
    for old in (config.IMAGE_DIR / doc_id).glob("*"):
        if old.name != run_id:
            shutil.rmtree(old, ignore_errors=True)
    store.update_document(
        doc_id,
        status="ready",
        stage="Prêt",
        progress=100,
        pages=n_pages,
        n_text=n_text,
        n_images=sum(1 for it in items if it["kind"] == "image"),
    )
