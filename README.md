# RAG PDF
[https://github.com/Zakaria-reb/Chat-With-PDFs/blob/39e505fc83c2b76d82a16bd7cc68a4823438d5cd/demo_vid.mp4]

Pipeline RAG pour interroger tes PDF (texte + images) avec les modèles NVIDIA de ton notebook `pipeline_rag_pdf_QA-v2`.

- **Glisser-déposer** de plusieurs PDF (n'importe où sur la page)
- **Ajouter / remplacer / supprimer** un document (bouton ↻ sur chaque carte ; un fichier de même nom déclenche une confirmation « Remplacer »)
- **Suivi de l'indexation** en direct (lecture → images → embeddings)
- **Chat** avec réponses citant leurs sources `[1] [2]` (clic sur une citation = source mise en évidence, lien « Ouvrir » à la bonne page du PDF)
- **Choix des documents** interrogés (case à cocher sur chaque carte)
- Tout est **persistant** : relance le serveur, tes documents et leur index sont toujours là

## Installation (Python 3.10 ou plus)

```bash
python -m venv .venv
# Windows : .venv\Scripts\activate      Linux/Mac : source .venv/bin/activate
pip install -r requirements.txt
```

Copie `.env.example` en `.env` et mets ta clé :

```
NVIDIA_API_KEY=nvapi-...
```

## Lancer

```bash
python -m uvicorn app.main:app --port 8000
```

(ou double-clic sur `run.bat` sous Windows / `./run.sh` sous Linux-Mac), puis ouvre http://localhost:8000

Sans clé NVIDIA, l'app démarre en **mode démo** (faux embeddings, pas de vrai LLM) : pratique pour tester l'interface.

## Architecture

```
PDF ──► pypdf ──► blocs de texte (~1500 car.) + fiche du document (titre, auteur, nb de pages)
         └──► images ──► modèle vision NVIDIA ──► description texte
                       │
   textes + descriptions ──► embeddings NVIDIA (nemotron-3-embed-1b) ──► SQLite (vecteurs float32)

Question ──► embedding (input_type=query) ──► similarité cosinus (numpy) ──► top 5 passages
         ──► LLM NVIDIA (texte + images retrouvées) ──► réponse + sources
```

| Fichier | Rôle |
|---|---|
| `app/main.py` | API FastAPI (documents, chat, fichiers) |
| `app/ingest.py` | Indexation d'un PDF (en tâche de fond, 2 PDF en parallèle max) |
| `app/rag.py` | Recherche vectorielle + génération de la réponse |
| `app/models.py` | Appels NVIDIA (embeddings, vision, LLM) avec reprise automatique sur erreur 429 |
| `app/store.py` | Base SQLite |
| `app/static/` | Interface (HTML / CSS / JS pur, sans build) |
| `tests/` | Tests du cœur et des appels NVIDIA (sans réseau) |

## Différences avec le notebook

- **Texte vectorisé directement** au lieu d'un résumé LLM par bloc : moins d'appels, plus de `RateLimitError 429`, et la recherche porte sur le vrai texte. Pour revenir au comportement « multi-vector » du notebook : `SUMMARIZE_TEXT=1` dans `.env`.
- **Chroma + InMemoryStore remplacés par SQLite + numpy** : persistant, aucune dépendance lourde, remplacer un document est atomique.
- **`pypdf` à la place de `unstructured`** : pas besoin de poppler/tesseract, installation en une commande. Limites : pas de détection de tableaux (ils restent du texte) et pas d'OCR sur les PDF scannés.
- **Fiche du document** indexée en plus (titre, auteur, pages, début du texte) pour répondre à « qui a écrit ce document ? ».

## Tests (optionnel)

```bash
pip install reportlab
python -m tests.test_core          # indexation, recherche, remplacement, suppression
python -m tests.test_nvidia_mock   # format des requêtes NVIDIA + reprise sur 429
```
