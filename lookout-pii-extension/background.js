/* Service worker (MV3) — Lookout PII export par lot.
 *
 * Rôle :
 *  1. Capter passivement, dans le trafic de la console Lookout/Omnissa :
 *       - le header `auth-token` (JWT à courte durée de vie)
 *       - le header `x-lookout-ent-guid`
 *       - le GUID de connexion MDM (dans les URL .../connections/<conn>/devices/...)
 *       - le lien issue -> device (schéma d'URL de l'endpoint + emplacement du
 *         device_id / de la connexion dans la réponse), APPRIS automatiquement.
 *  2. Rejouer ces appels lui-même pour parcourir une liste d'issues et sortir un CSV.
 *
 * Rien n'est envoyé ailleurs : tout reste local (chrome.storage.local) et ne
 * cible que https://*.lookout.com.
 */

const PII_RE =
  /^(https:\/\/[^/]+)\/mdm\/v1\/connections\/([0-9a-f-]+)\/devices\/([^/]+)\/pii/i;
// GUID type v4 tel qu'utilisé pour les issues.
const GUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
const FIELDS = ["guid", "owner_name", "email", "phone_number", "imsi", "imei"];

// --- petits helpers de stockage -------------------------------------------
async function getState() {
  const d = await chrome.storage.local.get("state");
  return d.state || {};
}
async function setState(patch) {
  const s = await getState();
  const next = { ...s, ...patch };
  await chrome.storage.local.set({ state: next });
  return next;
}

// --- suivi de l'onglet issue courant (pour l'apprentissage) ----------------
// tabId -> { issueGuid, ent, host }
const tabIssue = {};

function parseIssueUrl(url) {
  // https://<host>/<ent-guid>/issues/<issue-guid>
  try {
    const u = new URL(url);
    const m = u.pathname.match(
      /^\/([0-9a-f-]{36})\/issues\/([0-9a-f-]{36})/i
    );
    if (!m) return null;
    return { host: u.origin, ent: m[1], issueGuid: m[2] };
  } catch {
    return null;
  }
}

// --- capture passive des headers -------------------------------------------
chrome.webRequest.onBeforeSendHeaders.addListener(
  (details) => {
    let token = null;
    let ent = null;
    for (const h of details.requestHeaders || []) {
      const n = h.name.toLowerCase();
      if (n === "auth-token") token = h.value;
      else if (n === "x-lookout-ent-guid") ent = h.value;
    }
    const patch = {};
    if (token) {
      patch.token = token;
      patch.tokenSeenAt = Date.now();
    }
    if (ent) patch.ent = ent;

    const pii = details.url.match(PII_RE);
    if (pii) {
      patch.host = pii[1];
      patch.connection = pii[2];
      // Associer ce device au dernier issue ouvert dans cet onglet -> sert
      // à l'apprentissage du lien issue -> device.
      const ctx = tabIssue[details.tabId];
      if (ctx) {
        patch.learnSample = {
          issueGuid: ctx.issueGuid,
          ent: ctx.ent,
          host: pii[1],
          connection: pii[2],
          deviceId: pii[3],
        };
      }
    }

    // Candidat endpoint issue->device : un XHR dont l'URL contient le GUID de
    // l'issue actuellement ouverte dans l'onglet (mais qui n'est pas la
    // navigation de page ni l'appel /pii).
    if (details.type === "xmlhttprequest") {
      const ctx = tabIssue[details.tabId];
      if (ctx && details.url.includes(ctx.issueGuid) && !PII_RE.test(details.url)) {
        patch.issueEndpointCandidate = details.url.replace(
          ctx.issueGuid,
          "{ISSUE_GUID}"
        );
      }
    }

    if (Object.keys(patch).length) setState(patch);
  },
  { urls: ["https://*.lookout.com/*"] },
  ["requestHeaders", "extraHeaders"]
);

// Suivre la navigation vers une page d'issue pour connaître le GUID courant.
chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (details.type !== "main_frame") return;
    const info = parseIssueUrl(details.url);
    if (info) tabIssue[details.tabId] = info;
  },
  { urls: ["https://*.lookout.com/*"] }
);

chrome.tabs.onRemoved.addListener((tabId) => delete tabIssue[tabId]);

// --- recherche d'un chemin JSON menant à une valeur donnée -----------------
function findPath(obj, target, path = []) {
  if (obj === null || obj === undefined) return null;
  if (typeof obj !== "object") {
    return String(obj) === String(target) ? path : null;
  }
  for (const key of Object.keys(obj)) {
    const r = findPath(obj[key], target, path.concat(key));
    if (r) return r;
  }
  return null;
}
function getByPath(obj, path) {
  let cur = obj;
  for (const k of path) {
    if (cur == null) return undefined;
    cur = cur[k];
  }
  return cur;
}

async function apiFetch(url, token, ent) {
  const headers = { "auth-token": token, accept: "application/json" };
  if (ent) headers["x-lookout-ent-guid"] = ent;
  const resp = await fetch(url, { headers, credentials: "include" });
  if (resp.status === 401 || resp.status === 403) {
    const e = new Error("TOKEN_EXPIRED");
    e.code = "TOKEN_EXPIRED";
    throw e;
  }
  if (!resp.ok) {
    const e = new Error("HTTP_" + resp.status);
    e.code = "HTTP_" + resp.status;
    throw e;
  }
  return resp.json();
}

// --- apprentissage : déduire le schéma issue -> device ---------------------
async function learn() {
  const s = await getState();
  if (!s.token) return { ok: false, msg: "Aucun auth-token capté. Ouvre la console et une issue d'abord." };
  if (!s.learnSample)
    return { ok: false, msg: "Ouvre UNE issue dans la console (onglet actif) pour que l'appel /pii soit observé, puis réessaie." };
  if (!s.issueEndpointCandidate)
    return { ok: false, msg: "Endpoint issue non repéré. Recharge une page d'issue avec l'extension active." };

  const smp = s.learnSample;
  const url = s.issueEndpointCandidate.replace("{ISSUE_GUID}", smp.issueGuid);
  let data;
  try {
    data = await apiFetch(url, s.token, smp.ent || s.ent);
  } catch (e) {
    if (e.code === "TOKEN_EXPIRED")
      return { ok: false, msg: "Le auth-token est expiré. Rafraîchis la console et recommence." };
    return { ok: false, msg: "Impossible de lire l'endpoint issue (" + e.message + ")." };
  }

  const devPath = findPath(data, smp.deviceId);
  const connPath = findPath(data, smp.connection);
  if (!devPath)
    return { ok: false, msg: "device_id introuvable dans la réponse de l'issue. Renseigne le schéma manuellement dans les Options." };

  await setState({
    learned: {
      issueEndpoint: s.issueEndpointCandidate, // contient {ISSUE_GUID}
      devicePath: devPath,
      connectionPath: connPath, // peut être null (connexion fixe)
      connectionFixed: connPath ? null : smp.connection,
      host: smp.host,
    },
  });
  return {
    ok: true,
    msg: "Appris ✔ device via " + devPath.join(".") +
      (connPath ? ", connexion via " + connPath.join(".") : ", connexion fixe"),
  };
}

// --- résolution d'une issue -> PII -----------------------------------------
async function resolveIssue(issueGuid, s) {
  const L = s.learned;
  const url = L.issueEndpoint.replace("{ISSUE_GUID}", issueGuid);
  const issueData = await apiFetch(url, s.token, s.ent);
  const deviceId = getByPath(issueData, L.devicePath);
  if (deviceId == null) throw new Error("device_id absent pour cette issue");
  const connection = L.connectionFixed || getByPath(issueData, L.connectionPath);
  if (!connection) throw new Error("connexion introuvable pour cette issue");
  const host = L.host || s.host;
  const piiUrl = `${host}/mdm/v1/connections/${connection}/devices/${deviceId}/pii`;
  const pii = await apiFetch(piiUrl, s.token, s.ent);
  return { deviceId, connection, pii };
}

function extractIssueGuid(line) {
  const m = String(line).match(GUID_RE) ? String(line).match(/\/issues\/([0-9a-f-]{36})/i) : null;
  if (m) return m[1];
  // dernier GUID de la ligne (cas d'un GUID collé seul)
  const all = String(line).match(new RegExp(GUID_RE, "gi"));
  return all ? all[all.length - 1] : null;
}

function toCsv(rows) {
  const cols = ["issue_guid", "device_id", "connection", ...FIELDS, "error"];
  const esc = (v) => {
    v = v == null ? "" : String(v);
    return /[",\n;]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
  };
  const lines = [cols.join(",")];
  for (const r of rows) lines.push(cols.map((c) => esc(r[c])).join(","));
  return lines.join("\r\n");
}

// --- traitement par lot ----------------------------------------------------
async function runBatch(issueLines) {
  const s = await getState();
  if (!s.token) return { ok: false, msg: "Aucun auth-token capté." };
  if (!s.learned) return { ok: false, msg: "Schéma issue non appris. Clique « Apprendre » d'abord." };

  const rows = [];
  const total = issueLines.length;
  let done = 0;
  let stopped = false;

  for (const line of issueLines) {
    const issueGuid = extractIssueGuid(line);
    const row = { issue_guid: issueGuid || line };
    if (!issueGuid) {
      row.error = "GUID d'issue introuvable";
      rows.push(row);
      done++;
      notify(done, total, rows.length, false);
      continue;
    }
    try {
      const r = await resolveIssue(issueGuid, s);
      row.device_id = r.deviceId;
      row.connection = r.connection;
      for (const f of FIELDS) row[f] = r.pii[f] ?? "";
    } catch (e) {
      if (e.code === "TOKEN_EXPIRED") {
        row.error = "auth-token expiré";
        rows.push(row);
        stopped = true;
        break;
      }
      row.error = e.message;
    }
    rows.push(row);
    done++;
    notify(done, total, rows.length, false);
    await new Promise((res) => setTimeout(res, 200));
  }

  const csv = toCsv(rows);
  const dataUrl =
    "data:text/csv;charset=utf-8," + encodeURIComponent("﻿" + csv);
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
  await chrome.downloads.download({
    url: dataUrl,
    filename: `lookout-pii-${stamp}.csv`,
    saveAs: true,
  });

  notify(done, total, rows.length, true, stopped);
  return { ok: true, rows: rows.length, stopped };
}

function notify(done, total, count, finished, stopped) {
  chrome.runtime
    .sendMessage({ cmd: "progress", done, total, count, finished, stopped })
    .catch(() => {}); // popup peut être fermé
}

// --- routeur de messages ---------------------------------------------------
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    if (msg.cmd === "getState") {
      sendResponse(await getState());
    } else if (msg.cmd === "learn") {
      sendResponse(await learn());
    } else if (msg.cmd === "run") {
      sendResponse(await runBatch(msg.issues || []));
    } else if (msg.cmd === "reset") {
      await chrome.storage.local.set({ state: {} });
      sendResponse({ ok: true });
    } else {
      sendResponse({ ok: false, msg: "commande inconnue" });
    }
  })();
  return true; // réponse asynchrone
});
