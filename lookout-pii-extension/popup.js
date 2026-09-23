const $ = (id) => document.getElementById(id);

function send(cmd, extra = {}) {
  return chrome.runtime.sendMessage({ cmd, ...extra });
}

function short(v, n = 10) {
  if (!v) return "—";
  return String(v).length > n ? String(v).slice(0, n) + "…" : String(v);
}

async function refresh() {
  const s = await send("getState");
  mark("st-token", !!s.token, s.token ? "capté" : "manquant");
  mark("st-ent", !!s.ent, short(s.ent));
  mark("st-conn", !!s.connection, short(s.connection));
  mark("st-learn", !!s.learned, s.learned ? "appris" : "à apprendre");
  $("btn-run").disabled = !(s.token && s.learned);
}

function mark(id, ok, txt) {
  const el = $(id);
  el.textContent = txt;
  el.className = ok ? "ok" : "no";
}

$("btn-learn").addEventListener("click", async () => {
  setMsg("learn-msg", "Apprentissage…", "");
  const r = await send("learn");
  setMsg("learn-msg", r.msg, r.ok ? "ok" : "err");
  refresh();
});

$("btn-reset").addEventListener("click", async () => {
  await send("reset");
  setMsg("learn-msg", "Réinitialisé.", "");
  refresh();
});

$("btn-run").addEventListener("click", async () => {
  const lines = $("issues").value
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
  if (!lines.length) {
    setMsg("progress", "Colle au moins un lien d'issue.", "err");
    return;
  }
  $("btn-run").disabled = true;
  setMsg("progress", "Traitement de " + lines.length + " issue(s)…", "");
  const r = await send("run", { issues: lines });
  if (r && !r.ok) setMsg("progress", r.msg, "err");
  $("btn-run").disabled = false;
});

// progression envoyée par le background
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.cmd !== "progress") return;
  if (msg.finished) {
    setMsg(
      "progress",
      (msg.stopped ? "⏹ Arrêt : token expiré. " : "✔ Terminé. ") +
        msg.count +
        " ligne(s) — CSV téléchargé.",
      msg.stopped ? "err" : "ok"
    );
  } else {
    setMsg("progress", `${msg.done}/${msg.total} traité(s)…`, "");
  }
});

function setMsg(id, txt, cls) {
  const el = $(id);
  el.textContent = txt;
  el.className = "msg" + (cls ? " " + cls : "");
}

refresh();
