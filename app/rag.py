"""Question -> recherche vectorielle -> réponse du LLM avec sources."""
import numpy as np

from . import config, models, store


def search(question: str, doc_ids: list[str] | None, k: int = config.TOP_K) -> list[dict]:
    rows, mat = store.load_vectors(doc_ids)
    if not rows:
        return []
    qvec = np.asarray(models.embed([question], "query")[0], dtype=np.float32)
    qvec /= (np.linalg.norm(qvec) or 1)
    scores = mat @ qvec
    best = np.argsort(-scores)[:k]
    return [{**rows[i], "score": float(scores[i])} for i in best]


def answer(question: str, doc_ids: list[str] | None, history: list[dict] | None = None) -> dict:
    hits = search(question, doc_ids)
    passages = [
        {"n": n, "doc": h["name"], "page": h["page"], "text": h["content"]}
        for n, h in enumerate(hits, start=1)
    ]
    images_b64 = [
        models.b64_of_file(config.IMAGE_DIR / h["doc_id"] / h["image_file"])
        for h in hits
        if h["kind"] == "image" and h["image_file"]
    ]
    if not hits:
        text = "Je n'ai aucun document prêt à interroger. Ajoute un PDF à gauche."
    else:
        text = models.generate_answer(question, passages, images_b64, history or [])
    sources = [
        {
            "n": n,
            "doc_id": h["doc_id"],
            "doc": h["name"],
            "page": h["page"],
            "kind": h["kind"],
            "score": round(h["score"], 3),
            "snippet": h["content"][:600],
            "image_url": f"/api/images/{h['doc_id']}/{h['image_file']}" if h["image_file"] else None,
        }
        for n, h in enumerate(hits, start=1)
    ]
    return {"answer": text, "sources": sources}
