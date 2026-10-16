"""Configuration centrale (lue depuis .env)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

try:  # python-dotenv est dans requirements.txt ; on tolère son absence
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
IMAGE_DIR = DATA_DIR / "images"
DB_PATH = DATA_DIR / "rag.sqlite3"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
TEXT_MODEL = os.getenv("TEXT_MODEL", "nvidia/nemotron-3.5-super-vl-120b-a12b")
VISION_MODEL = os.getenv("VISION_MODEL", "meta/llama-3.2-11b-vision-instruct")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nvidia/nemotron-3-embed-1b")

DEMO_MODE = os.getenv("DEMO_MODE", "0") == "1" or not NVIDIA_API_KEY

# Découpage du texte (même esprit que ton notebook : ~1200-1500 caractères)
CHUNK_MAX_CHARS = 1500
CHUNK_MIN_CHARS = 300
# Nombre de passages renvoyés au LLM
TOP_K = 5
# Images trop petites = logos / icônes, on les ignore
MIN_IMAGE_SIDE = 120
MAX_IMAGES_PER_DOC = 12

# 1 = comme ton notebook (on vectorise un résumé LLM de chaque bloc de texte, multi-vector)
# 0 = on vectorise directement le texte (moins d'appels API, pas de RateLimit 429)
SUMMARIZE_TEXT = os.getenv("SUMMARIZE_TEXT", "0") == "1"
