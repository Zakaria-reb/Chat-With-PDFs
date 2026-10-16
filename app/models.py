"""Accès aux modèles : embeddings, vision, LLM (API NVIDIA) + mode démo hors-ligne."""
import base64
import random
import re
import time
import zlib

import numpy as np
import requests

from . import config


class ModelError(RuntimeError):
    pass


# --------------------------------------------------------------------------- HTTP
def _post(path: str, payload: dict, timeout: int = 120, retries: int = 6) -> dict:
    """POST vers l'API NVIDIA avec reprise automatique sur 429 / 5xx."""
    headers = {
        "Authorization": f"Bearer {config.NVIDIA_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    url = f"{config.NVIDIA_BASE_URL}{path}"
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            if attempt == retries - 1:
                raise ModelError(f"Connexion NVIDIA impossible : {exc}") from exc
            time.sleep(min(2 ** attempt, 20))
            continue
        if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
            wait = float(r.headers.get("Retry-After", 0) or 0)
            time.sleep(max(wait, min(2 ** attempt + random.random(), 30)))
            continue
        if not r.ok:
            raise ModelError(f"NVIDIA HTTP {r.status_code} : {r.text[:400]}")
        return r.json()
    raise ModelError("NVIDIA : trop de tentatives")


# --------------------------------------------------------------------------- Embeddings
_DEMO_DIM = 512


def _demo_embed(text: str) -> list[float]:
    """Faux embedding : sac de mots haché (suffisant pour tester l'interface)."""
    vec = np.zeros(_DEMO_DIM, dtype=np.float32)
    for tok in re.findall(r"\w+", text.lower()):
        if len(tok) > 2:
            vec[zlib.crc32(tok.encode()) % _DEMO_DIM] += 1.0
    norm = np.linalg.norm(vec)
    return (vec / norm if norm else vec).tolist()


def embed(texts: list[str], input_type: str = "passage") -> list[list[float]]:
    """input_type : 'passage' pour les documents, 'query' pour les questions."""
    texts = [str(t).strip() or "." for t in texts]
    if config.DEMO_MODE:
        return [_demo_embed(t) for t in texts]
    out: list[list[float]] = []
    for start in range(0, len(texts), 16):  # lots de 16, comme dans le notebook
        data = _post(
            "/embeddings",
            {
                "input": texts[start:start + 16],
                "model": config.EMBED_MODEL,
                "input_type": input_type,
                "encoding_format": "float",
                "truncate": "END",
            },
        )["data"]
        data.sort(key=lambda item: item["index"])
        out.extend(item["embedding"] for item in data)
    return out


# --------------------------------------------------------------------------- LLM
def _chat(model: str, messages: list[dict], max_tokens: int = 1024) -> str:
    data = _post(
        "/chat/completions",
        {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens},
        timeout=180,
    )
    return (data["choices"][0]["message"].get("content") or "").strip()


def summarize_text(text: str) -> str:
    if config.DEMO_MODE:
        return text[:300]
    prompt = (
        "You are an assistant tasked with summarizing a passage of a document.\n"
        "Give a concise summary preserving important facts, technical terms, names, "
        "numbers and relationships. Respond in the same language as the passage. "
        f"Respond only with the summary.\n\nPassage:\n{text}"
    )
    return _chat(config.TEXT_MODEL, [{"role": "user", "content": prompt}])


def describe_image(image_b64_jpeg: str, page: int) -> str:
    if config.DEMO_MODE:
        return f"Image (mode démo) trouvée page {page}."
    prompt = (
        "Describe the image in detail. It may be part of a document or scientific paper. "
        "Identify its main subject, labels, relationships, important visual elements and "
        "readable values. Do not invent information that cannot be seen."
    )
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64_jpeg}"}},
    ]
    return _chat(config.VISION_MODEL, [{"role": "user", "content": content}])


SYSTEM_PROMPT = (
    "You answer questions about the user's PDF documents using ONLY the numbered context "
    "passages provided. Answer in the same language as the question. Be precise and concise. "
    "Cite the passages you used with their number in square brackets, like [1] or [2][3]. "
    "If the answer is not in the context, say clearly that you could not find it in the "
    "documents. Never invent facts."
)


def generate_answer(question: str, passages: list[dict], images_b64: list[str], history: list[dict]) -> str:
    """passages : [{'n', 'doc', 'page', 'text'}]"""
    if config.DEMO_MODE:
        if not passages:
            return "Mode démo : aucun passage trouvé. Ajoute un PDF puis repose ta question."
        top = passages[0]
        snippet = re.sub(r"\s+", " ", top["text"]).strip()[:450]
        return (
            "**Mode démo** (pas de clé NVIDIA : pas de vrai LLM). "
            f"Voici le passage le plus proche de ta question [1] :\n\n> {snippet}…"
        )
    context = "\n\n---\n\n".join(
        f"[{p['n']}] (document : {p['doc']}, page {p['page']})\n{p['text']}" for p in passages
    )
    user_text = f"Context:\n{context}\n\nQuestion:\n{question}"
    content: list[dict] = [{"type": "text", "text": user_text}]
    for img in images_b64[:2]:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in history[-6:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": str(turn["content"])[:2000]})
    messages.append({"role": "user", "content": content})
    return _chat(config.TEXT_MODEL, messages, max_tokens=1200)


def b64_of_file(path) -> str:
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode()
