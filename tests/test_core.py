"""Test de bout en bout du cœur (sans serveur, sans clé API) : python -m tests.test_core
Nécessite reportlab uniquement pour fabriquer un PDF de test : pip install reportlab"""
import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["DEMO_MODE"] = "1"

from io import BytesIO  # noqa: E402

from PIL import Image  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app import config, ingest, rag, store  # noqa: E402


def make_pdf(path, title, paragraphs, with_image=False):
    c = canvas.Canvas(str(path))
    c.setTitle(title)
    c.setAuthor("Zakaria Test")
    y = 800
    for p in paragraphs:
        for line in [p[i:i + 90] for i in range(0, len(p), 90)]:
            c.drawString(40, y, line)
            y -= 14
        y -= 10
    if with_image:
        img = Image.new("RGB", (300, 200), (200, 40, 40))
        c.drawImage(ImageReader(img), 40, 200, width=300, height=200)
    c.save()


def run():
    store.init()
    pdf = config.DATA_DIR / "a.pdf"
    make_pdf(pdf, "Intro ML", [
        "Overfitting occurs when a model learns the noise of the training data and fails to generalize.",
        "Supervised learning uses labelled examples to learn a mapping from inputs to outputs.",
        "Clustering groups similar observations without labels, for example k-means.",
    ], with_image=True)
    doc_id = store.create_document("a.pdf", pdf.stat().st_size)
    os.replace(pdf, ingest.pdf_path(doc_id))
    ingest._index(doc_id)
    doc = store.get_document(doc_id)
    print("doc:", {k: doc[k] for k in ("status", "stage", "pages", "n_text", "n_images", "error")})
    assert doc["status"] == "ready" and doc["n_images"] == 1 and doc["n_text"] >= 2

    res = rag.answer("What is overfitting?", [doc_id])
    print("top source:", res["sources"][0]["kind"], res["sources"][0]["score"], res["sources"][0]["snippet"][:70])
    assert "overfitting" in res["sources"][0]["snippet"].lower()

    # remplacement : même id, autre contenu -> l'ancien contenu disparaît
    pdf2 = config.DATA_DIR / "b.pdf"
    make_pdf(pdf2, "Autre", ["Hadoop HDFS stores large files across a cluster of machines."])
    os.replace(pdf2, ingest.pdf_path(doc_id))
    ingest._index(doc_id)
    res = rag.answer("hadoop cluster", [doc_id])
    assert "hdfs" in res["sources"][0]["snippet"].lower()
    assert not any("overfitting" in s["snippet"].lower() for s in res["sources"])
    assert store.get_document(doc_id)["n_images"] == 0
    assert not list((config.IMAGE_DIR / doc_id).glob("*/*.jpg")), "anciennes images non nettoyées"
    print("remplacement OK ->", store.stats())

    store.delete_document(doc_id)
    assert store.stats()["passages"] == 0
    print("ALL OK")


if __name__ == "__main__":
    run()
