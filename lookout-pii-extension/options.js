const $ = (id) => document.getElementById(id);

async function load() {
  const d = await chrome.storage.local.get("state");
  const L = (d.state && d.state.learned) || {};
  $("issueEndpoint").value = L.issueEndpoint || "";
  $("devicePath").value = (L.devicePath || []).join(".");
  $("connectionPath").value = (L.connectionPath || []).join(".");
  $("connectionFixed").value = L.connectionFixed || "";
  $("host").value = L.host || "";
}

$("save").addEventListener("click", async () => {
  const d = await chrome.storage.local.get("state");
  const state = d.state || {};
  const split = (v) => (v.trim() ? v.trim().split(".").map((s) => s.trim()) : null);
  const connPath = split($("connectionPath").value);
  state.learned = {
    issueEndpoint: $("issueEndpoint").value.trim(),
    devicePath: split($("devicePath").value),
    connectionPath: connPath,
    connectionFixed: connPath ? null : $("connectionFixed").value.trim() || null,
    host: $("host").value.trim() || null,
  };
  await chrome.storage.local.set({ state });
  $("msg").textContent = "Enregistré ✔";
  setTimeout(() => ($("msg").textContent = ""), 2000);
});

load();
