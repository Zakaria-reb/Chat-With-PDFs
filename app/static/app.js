/* Dashboard RAG PDF — JavaScript pur, aucune dépendance. */
const $ = (id) => document.getElementById(id);
const state = {
  docs: [],
  off: new Set(JSON.parse(localStorage.getItem("rag.off") || "[]")), // documents exclus de la recherche
  history: JSON.parse(localStorage.getItem("rag.history") || "[]"),   // [{role, content, sources}]
  sending: false,
  replaceId: null,
  pollTimer: null,
};

/* ---------------------------------------------------------------- utilitaires */
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtSize = (b) => (b > 1048576 ? (b / 1048576).toFixed(1) + " Mo" : Math.max(1, Math.round(b / 1024)) + " Ko");

function toast(msg, type = "") {
  const t = document.createElement("div");
  t.className = "toast " + type;
  t.textContent = msg;
  $("toasts").append(t);
  setTimeout(() => t.remove(), 5000);
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { /* réponse vide */ }
  if (!res.ok) throw new Error((data && data.detail) || `Erreur ${res.status}`);
  return data;
}

/* Mini rendu Markdown (sûr : tout est échappé avant) + citations [n] cliquables */
function renderMarkdown(src) {
  const blocks = [];
  let text = esc(src).replace(/```(?:\w+)?\n?([\s\S]*?)```/g, (_, code) => {
    blocks.push(`<pre><code>${code.trim()}</code></pre>`);
    return `\u0000${blocks.length - 1}\u0000`;
  });
  const inline = (s) => s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<i>$2</i>")
    .replace(/\[(\d{1,2})\]/g, '<button class="cite" data-n="$1">$1</button>');
  const out = [];
  let list = null;
  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    let m;
    if ((m = line.match(/^\s*[-*•]\s+(.*)/))) {
      if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) {
      if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else {
      closeList();
      if (!line.trim()) continue;
      if ((m = line.match(/^#{1,4}\s+(.*)/))) out.push(`<h4>${inline(m[1])}</h4>`);
      else if ((m = line.match(/^&gt;\s?(.*)/))) out.push(`<blockquote>${inline(m[1])}</blockquote>`);
      else if (/^\u0000\d+\u0000$/.test(line.trim())) out.push(line.trim());
      else out.push(`<p>${inline(line)}</p>`);
    }
  }
  closeList();
  return out.join("").replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[+i]);
}

/* ---------------------------------------------------------------- documents */
const ICON = {
  replace: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 0 1-15.5 6.2L3 16M3 12a9 9 0 0 1 15.5-6.2L21 8"/><path d="M21 3v5h-5M3 21v-5h5"/></svg>',
  trash: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/></svg>',
  open: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
};

function renderDocs() {
  const list = $("docList");
  list.innerHTML = "";
  $("docCount").textContent = state.docs.length;
  $("emptyDocs").hidden = state.docs.length > 0;
  for (const d of state.docs) {
    const el = document.createElement("div");
    el.className = "doc" + (state.off.has(d.id) ? " off" : "");
    const busy = d.status === "queued" || d.status === "processing";
    const meta = d.status === "ready"
      ? `${d.pages} p. · ${d.n_text} passages · ${d.n_images} images · ${fmtSize(d.size)}`
      : fmtSize(d.size);
    let status = "";
    if (busy) {
      status = `<div class="status s-${d.status}"><span class="dot"></span>${esc(d.stage)} · ${d.progress}%</div>
                <div class="bar"><i style="width:${d.progress}%"></i></div>`;
    } else if (d.status === "error") {
      status = `<div class="status s-error"><span class="dot"></span><span>${esc(d.error || "Erreur")}</span>
                <button class="retry" data-act="reindex">Réindexer</button></div>`;
    } else {
      status = `<div class="status s-ready"><span class="dot"></span>Prêt</div>`;
    }
    el.innerHTML = `
      <div class="doc-top">
        <input type="checkbox" class="doc-check" ${state.off.has(d.id) ? "" : "checked"} ${d.status === "ready" ? "" : "disabled"} title="Inclure dans la recherche">
        <div style="min-width:0">
          <div class="doc-name">${esc(d.name)}</div>
          <div class="doc-meta">${meta}</div>
        </div>
        <div class="doc-actions">
          <a class="icon-btn" href="/api/documents/${d.id}/file" target="_blank" title="Ouvrir le PDF">${ICON.open}</a>
          <button class="icon-btn" data-act="replace" title="Remplacer par un autre PDF" ${busy ? "disabled" : ""}>${ICON.replace}</button>
          <button class="icon-btn danger" data-act="delete" title="Supprimer">${ICON.trash}</button>
        </div>
      </div>${status}`;
    el.querySelector(".doc-check").addEventListener("change", (e) => {
      e.target.checked ? state.off.delete(d.id) : state.off.add(d.id);
      localStorage.setItem("rag.off", JSON.stringify([...state.off]));
      renderDocs();
      renderScope();
    });
    el.addEventListener("click", (e) => {
      const act = e.target.closest("[data-act]")?.dataset.act;
      if (act === "delete") deleteDoc(d);
      else if (act === "replace") { state.replaceId = d.id; $("replaceInput").click(); }
      else if (act === "reindex") reindexDoc(d);
    });
    list.append(el);
  }
  renderScope();
}

function scopeIds() {
  return state.docs.filter((d) => d.status === "ready" && !state.off.has(d.id)).map((d) => d.id);
}
function renderScope() {
  const ready = state.docs.filter((d) => d.status === "ready").length;
  const n = scopeIds().length;
  $("scopeLabel").textContent = ready === 0 ? "" : n === ready ? `Recherche dans ${ready} document${ready > 1 ? "s" : ""}` : `Recherche dans ${n} / ${ready} documents`;
}

async function refresh() {
  try {
    const data = await api("/api/documents");
    state.docs = data.documents;
    $("stDocs").textContent = data.stats.documents;
    $("stPassages").textContent = data.stats.passages;
    $("stImages").textContent = data.stats.images;
    renderDocs();
    schedulePoll(data.stats.busy > 0);
  } catch (e) {
    schedulePoll(false);
  }
}
function schedulePoll(fast) {
  clearTimeout(state.pollTimer);
  state.pollTimer = setTimeout(refresh, fast ? 1000 : 6000);
}

/* ---------------------------------------------------------------- upload / remplacement */
async function uploadFiles(fileList) {
  const files = [...fileList].filter((f) => /\.pdf$/i.test(f.name));
  const rejected = fileList.length - files.length;
  if (rejected) toast(`${rejected} fichier(s) ignoré(s) : seuls les PDF sont acceptés.`, "err");
  if (!files.length) return;

  const dups = files.filter((f) => state.docs.some((d) => d.name.toLowerCase() === f.name.toLowerCase()));
  let mode = "replace";
  if (dups.length) {
    mode = await askDuplicate(dups.map((f) => f.name));
    if (mode === "cancel") return;
  }
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f));
  fd.append("on_duplicate", mode);
  try {
    const { results } = await api("/api/documents", { method: "POST", body: fd });
    const added = results.filter((r) => r.action === "added").length;
    const replaced = results.filter((r) => r.action === "replaced").length;
    const parts = [];
    if (added) parts.push(`${added} ajouté${added > 1 ? "s" : ""}`);
    if (replaced) parts.push(`${replaced} remplacé${replaced > 1 ? "s" : ""}`);
    if (parts.length) toast(parts.join(" · ") + " — indexation en cours", "ok");
  } catch (e) { toast(e.message, "err"); }
  refresh();
}

function askDuplicate(names) {
  return new Promise((resolve) => {
    const dlg = $("dupDialog");
    $("dupText").textContent = names.length === 1
      ? `« ${names[0]} » existe déjà. Veux-tu remplacer son contenu par ce nouveau fichier ?`
      : `${names.length} fichiers existent déjà (${names.slice(0, 3).join(", ")}${names.length > 3 ? "…" : ""}). Les remplacer ?`;
    dlg.addEventListener("close", () => resolve(dlg.returnValue || "cancel"), { once: true });
    dlg.returnValue = "";
    dlg.showModal();
  });
}

async function replaceDoc(file) {
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    await api(`/api/documents/${state.replaceId}/replace`, { method: "POST", body: fd });
    toast("Document remplacé — réindexation en cours", "ok");
  } catch (e) { toast(e.message, "err"); }
  refresh();
}
async function deleteDoc(d) {
  if (!confirm(`Supprimer « ${d.name} » et tout son index ?`)) return;
  try { await api(`/api/documents/${d.id}`, { method: "DELETE" }); } catch (e) { toast(e.message, "err"); }
  refresh();
}
async function reindexDoc(d) {
  try { await api(`/api/documents/${d.id}/reindex`, { method: "POST" }); } catch (e) { toast(e.message, "err"); }
  refresh();
}

/* ---------------------------------------------------------------- chat */
function sourcesHtml(sources) {
  if (!sources || !sources.length) return "";
  const cards = sources.map((s) => `
    <div class="src" data-n="${s.n}">
      <div class="src-head">
        <span class="src-n">${s.n}</span>
        <span class="src-doc">${esc(s.doc)}</span>
        <span class="src-meta">page ${s.page} · ${s.kind === "image" ? "image" : "texte"}</span>
        <span class="score">${s.score}</span>
        <a class="src-open" target="_blank" href="/api/documents/${s.doc_id}/file#page=${s.page}">Ouvrir ↗</a>
      </div>
      <div class="src-text">${esc(s.snippet)}</div>
      ${s.image_url ? `<img loading="lazy" src="${s.image_url}" alt="Image du document">` : ""}
    </div>`).join("");
  return `<details class="sources"><summary>${sources.length} source${sources.length > 1 ? "s" : ""}</summary><div class="src-grid">${cards}</div></details>`;
}

function addMessage(role, content, sources, save = true) {
  $("welcome").hidden = true;
  const el = document.createElement("div");
  el.className = "msg " + (role === "user" ? "user" : "bot");
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (role === "user") bubble.textContent = content;
  else bubble.innerHTML = renderMarkdown(content);
  el.append(bubble);
  if (role !== "user" && sources) el.insertAdjacentHTML("beforeend", sourcesHtml(sources));
  $("messages").append(el);
  if (save) {
    state.history.push({ role, content, sources: sources || [] });
    localStorage.setItem("rag.history", JSON.stringify(state.history.slice(-40)));
  }
  scrollDown();
  return el;
}
const scrollDown = () => { const c = $("chat"); c.scrollTop = c.scrollHeight; };

async function send(text) {
  text = text.trim();
  if (!text || state.sending) return;
  const ids = scopeIds();
  if (!ids.length) { toast("Ajoute (ou active) au moins un document prêt.", "err"); return; }
  state.sending = true;
  $("sendBtn").disabled = true;
  const prior = state.history.map(({ role, content }) => ({ role, content }));
  addMessage("user", text);
  $("question").value = "";
  autosize();
  const typing = document.createElement("div");
  typing.className = "msg bot";
  typing.innerHTML = '<div class="bubble"><span class="typing"><i></i><i></i><i></i></span></div>';
  $("messages").append(typing);
  scrollDown();
  try {
    const res = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: text, doc_ids: ids, history: prior }),
    });
    typing.remove();
    addMessage("assistant", res.answer, res.sources);
  } catch (e) {
    typing.remove();
    addMessage("assistant", "⚠️ " + e.message, [], false);
  } finally {
    state.sending = false;
    $("sendBtn").disabled = false;
    $("question").focus();
  }
}

function autosize() {
  const q = $("question");
  q.style.height = "auto";
  q.style.height = Math.min(q.scrollHeight, 180) + "px";
}

/* ---------------------------------------------------------------- événements */
$("fileInput").addEventListener("change", (e) => { uploadFiles(e.target.files); e.target.value = ""; });
$("replaceInput").addEventListener("change", (e) => { replaceDoc(e.target.files[0]); e.target.value = ""; });
$("dropzone").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("fileInput").click(); } });

// glisser-déposer sur toute la page
let dragDepth = 0;
const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
window.addEventListener("dragenter", (e) => { if (hasFiles(e)) { dragDepth++; $("dropOverlay").hidden = false; } });
window.addEventListener("dragleave", (e) => { if (hasFiles(e) && --dragDepth <= 0) { dragDepth = 0; $("dropOverlay").hidden = true; } });
window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener("drop", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  $("dropOverlay").hidden = true;
  uploadFiles(e.dataTransfer.files);
});

$("sendBtn").addEventListener("click", () => send($("question").value));
$("question").addEventListener("input", autosize);
$("question").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send($("question").value); }
});
$("suggestions").addEventListener("click", (e) => { if (e.target.classList.contains("chip")) send(e.target.textContent); });
$("clearBtn").addEventListener("click", () => {
  state.history = [];
  localStorage.removeItem("rag.history");
  $("messages").innerHTML = "";
  $("welcome").hidden = false;
});
$("menuBtn").addEventListener("click", () => $("sidebar").classList.toggle("open"));
$("chat").addEventListener("click", () => $("sidebar").classList.remove("open"));

// clic sur une citation [n] -> ouvre et met en évidence la source
$("messages").addEventListener("click", (e) => {
  const b = e.target.closest(".cite");
  if (!b) return;
  const msg = b.closest(".msg");
  const details = msg.querySelector("details.sources");
  if (!details) return;
  details.open = true;
  const card = details.querySelector(`.src[data-n="${b.dataset.n}"]`);
  if (card) {
    card.scrollIntoView({ block: "nearest", behavior: "smooth" });
    card.classList.add("flash");
    setTimeout(() => card.classList.remove("flash"), 1400);
  }
});

/* ---------------------------------------------------------------- démarrage */
(async function init() {
  for (const m of state.history) addMessage(m.role, m.content, m.sources, false);
  try {
    const s = await api("/api/status");
    $("modePill").innerHTML = s.demo_mode
      ? "<b>Mode démo</b> · ajoute NVIDIA_API_KEY dans .env pour les vrais modèles"
      : `<b>NVIDIA</b> · ${esc(s.models.texte.split("/").pop())}`;
  } catch (_) { $("modePill").textContent = "Serveur injoignable"; }
  refresh();
})();
