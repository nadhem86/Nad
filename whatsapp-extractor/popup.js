/* WhatsApp → vTiger — Popup principal (contacts + groupes + conversations) */

let data = { contacts: [], groups: [], conversations: [] };
let filtered = { contacts: [], groups: [], conversations: [] };
let activeTab = 'contacts';
let vtigerConfig = null;
let vtigerOpts = { module: 'Contacts', duplicate: 'skip', businessTag: true, waSource: true, onlyNamed: false };

const $ = id => document.getElementById(id);

/* ── UI refs ── */
const ui = {
  badge:       $('status-badge'),
  btnExtract:  $('btn-extract'),
  btnSync:     $('btn-sync'),
  btnCsv:      $('btn-export-csv'),
  btnJson:     $('btn-export-json'),
  btnClear:    $('btn-clear'),
  btnSettings: $('btn-settings'),
  tabsBar:     $('tabs-bar'),
  searchBar:   $('search-bar'),
  searchInput: $('search-input'),
  loader:      $('loader'),
  loaderLabel: $('loader-label'),
  errorBox:    $('error-box'),
  errorMsg:    $('error-msg'),
  emptyState:  $('empty-state'),
  emptyMsg:    $('empty-msg'),
  footerInfo:  $('footer-info'),
  vtigerDot:   $('vtiger-status'),
  syncProgress:$('sync-progress'),
  syncBar:     $('sync-bar'),
  syncLabel:   $('sync-label'),
  syncResult:  $('sync-result'),
  syncCreated: $('sync-created'),
  syncUpdated: $('sync-updated'),
  syncSkipped: $('sync-skipped'),
  syncErrors:  $('sync-errors'),
  panels: {
    contacts:      $('panel-contacts'),
    groups:        $('panel-groups'),
    conversations: $('panel-conversations')
  }
};

/* ── Helpers ── */
const show = el => el?.classList.remove('hidden');
const hide = el => el?.classList.add('hidden');
function setStatus(state, text) { ui.badge.className = `badge badge-${state}`; ui.badge.textContent = text; }

function initials(name) {
  if (!name) return '?';
  const p = name.trim().split(/\s+/);
  return (p.length >= 2 ? p[0][0] + p[1][0] : p[0].slice(0, 2)).toUpperCase();
}

function timeAgo(iso) {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1)   return 'maintenant';
  if (m < 60)  return `${m}min`;
  const h = Math.floor(m / 60);
  if (h < 24)  return `${h}h`;
  const d = Math.floor(h / 24);
  if (d < 7)   return `${d}j`;
  return new Date(iso).toLocaleDateString('fr-FR');
}

function copyToClipboard(text) {
  navigator.clipboard.writeText(text).catch(() => {
    const ta = Object.assign(document.createElement('textarea'), { value: text });
    document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
  });
}

/* ═══════════════════════════════════════
   RENDER — CONTACTS
═══════════════════════════════════════ */
function renderContacts(list) {
  const panel = ui.panels.contacts;
  panel.innerHTML = '';
  list.forEach(c => {
    const name = c.name || c.shortName || '';
    const div = document.createElement('div');
    div.className = 'contact-item';
    div.innerHTML = `
      <div class="contact-avatar ${c.isBusiness ? 'business' : ''}">${initials(name)}</div>
      <div class="contact-info">
        <div class="contact-name">${name || c.phone}</div>
        <div class="contact-phone">${c.phone}${c.about ? ` · ${c.about.slice(0,40)}` : ''}</div>
        <div class="contact-meta">
          ${c.isBusiness  ? '<span class="tag tag-business">Business</span>' : ''}
          ${c.isMuted     ? '<span class="tag tag-muted">Muet</span>' : ''}
          ${c.unreadCount > 0 ? `<span class="tag tag-unread">${c.unreadCount}</span>` : ''}
          ${c.isPinned    ? '<span class="tag tag-pin">📌</span>' : ''}
          ${c.lastActivity ? `<span class="tag tag-time">${timeAgo(c.lastActivity)}</span>` : ''}
          <span class="tag tag-source">${c.source || ''}</span>
        </div>
        ${c.lastMessage ? `<div class="last-msg">${c.lastMessage.fromMe ? '✓ ' : ''}${c.lastMessage.body}</div>` : ''}
      </div>
      <div class="contact-actions">
        <button class="action-btn" data-copy="${c.phone}" title="Copier le numéro">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>
        </button>
        <button class="action-btn" data-open="${c.phone}" title="Ouvrir WhatsApp">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51a6.47 6.47 0 00-.57-.01c-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347z"/></svg>
        </button>
      </div>`;
    div.querySelector('[data-copy]').onclick = e => {
      copyToClipboard(c.phone);
      e.currentTarget.style.color = '#25D366';
      setTimeout(() => (e.currentTarget.style.color = ''), 1200);
    };
    div.querySelector('[data-open]').onclick = () => openWA(c.phone);
    panel.appendChild(div);
  });
}

/* ═══════════════════════════════════════
   RENDER — GROUPES
═══════════════════════════════════════ */
function renderGroups(list) {
  const panel = ui.panels.groups;
  panel.innerHTML = '';
  list.forEach(g => {
    const card = document.createElement('div');
    card.className = 'group-card';
    card.innerHTML = `
      <div class="group-header">
        <div class="contact-avatar group">${initials(g.name)}</div>
        <div class="group-meta">
          <div class="contact-name">${g.name}</div>
          <div class="contact-phone">
            ${g.participantCount || g.participants?.length || 0} membres
            ${g.createdAt ? ` · créé ${timeAgo(g.createdAt)}` : ''}
            ${g.lastActivity ? ` · actif ${timeAgo(g.lastActivity)}` : ''}
          </div>
          ${g.description ? `<div class="group-desc">${g.description.slice(0, 80)}</div>` : ''}
          <div class="contact-meta">
            ${g.isMuted   ? '<span class="tag tag-muted">Muet</span>' : ''}
            ${g.unreadCount > 0 ? `<span class="tag tag-unread">${g.unreadCount}</span>` : ''}
            ${g.isPinned  ? '<span class="tag tag-pin">📌</span>' : ''}
            ${g.owner     ? `<span class="tag tag-source">Owner: ${g.owner}</span>` : ''}
          </div>
          ${g.lastMessage ? `<div class="last-msg">${g.lastMessage.fromMe ? '✓ ' : ''}${g.lastMessage.body}</div>` : ''}
        </div>
        <div class="contact-actions">
          <button class="action-btn" data-copy-group="${JSON.stringify(g.participants?.map(p=>p.phone)||[]).replace(/"/g,'&quot;')}" title="Copier les numéros du groupe">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>
          </button>
        </div>
      </div>
      ${g.participants?.length ? `
      <div class="participants-list">
        ${g.participants.slice(0, 8).map(p => `
          <div class="participant">
            <span class="p-name">${p.name || p.phone}</span>
            <span class="p-phone">${p.phone}</span>
            ${p.isAdmin ? '<span class="tag tag-admin">Admin</span>' : ''}
          </div>`).join('')}
        ${g.participants.length > 8 ? `<div class="p-more">+${g.participants.length - 8} autres</div>` : ''}
      </div>` : ''}`;

    card.querySelector('[data-copy-group]').onclick = e => {
      const phones = g.participants?.map(p => p.phone).join('\n') || '';
      copyToClipboard(phones);
      e.currentTarget.style.color = '#25D366';
      setTimeout(() => (e.currentTarget.style.color = ''), 1200);
    };
    panel.appendChild(card);
  });
}

/* ═══════════════════════════════════════
   RENDER — CONVERSATIONS
═══════════════════════════════════════ */
function renderConversations(list) {
  const panel = ui.panels.conversations;
  panel.innerHTML = '';
  list.forEach(c => {
    const name = c.name || c.phone || c.id || '';
    const div = document.createElement('div');
    div.className = 'contact-item';
    div.innerHTML = `
      <div class="contact-avatar ${c.isGroup ? 'group' : ''}">${initials(name)}</div>
      <div class="contact-info">
        <div class="conv-row">
          <span class="contact-name">${name}</span>
          <span class="conv-time">${timeAgo(c.lastActivity)}</span>
        </div>
        <div class="conv-row">
          <span class="last-msg-inline">${c.lastMessage ? (c.lastMessage.fromMe ? '✓ ' : '') + c.lastMessage.body : (c.lastMsg || '')}</span>
          ${c.unreadCount > 0 ? `<span class="unread-badge">${c.unreadCount}</span>` : ''}
        </div>
        <div class="contact-meta">
          ${c.isGroup   ? '<span class="tag tag-group">Groupe</span>' : ''}
          ${c.isMuted   ? '<span class="tag tag-muted">Muet</span>' : ''}
          ${c.isPinned  ? '<span class="tag tag-pin">📌</span>' : ''}
          ${c.isArchived? '<span class="tag tag-source">Archivé</span>' : ''}
        </div>
      </div>
      ${!c.isGroup && c.phone ? `
      <div class="contact-actions">
        <button class="action-btn" data-open="${c.phone}" title="Ouvrir">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51a6.47 6.47 0 00-.57-.01c-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347z"/></svg>
        </button>
      </div>` : ''}`;
    div.querySelector('[data-open]')?.addEventListener('click', () => openWA(c.phone));
    panel.appendChild(div);
  });
}

/* ── Ouvrir WhatsApp ── */
function openWA(phone) {
  const num = (phone || '').replace(/\D/g, '');
  chrome.tabs.query({ url: 'https://web.whatsapp.com/*' }, tabs => {
    if (tabs[0]) {
      chrome.tabs.sendMessage(tabs[0].id, { action: 'openContact', phone });
      chrome.tabs.update(tabs[0].id, { active: true });
    } else {
      chrome.tabs.create({ url: `https://web.whatsapp.com/send?phone=${num}` });
    }
  });
}

/* ── Mise à jour des compteurs d'onglets ── */
function updateCounts() {
  $('tab-count-contacts').textContent     = filtered.contacts.length;
  $('tab-count-groups').textContent       = filtered.groups.length;
  $('tab-count-conversations').textContent= filtered.conversations.length;
}

/* ── Rendu par onglet actif ── */
function renderActive() {
  const list = filtered[activeTab];
  if (!list.length) {
    show(ui.emptyState);
    ui.emptyMsg.textContent = {
      contacts:      'Aucun contact trouvé.',
      groups:        'Aucun groupe trouvé. Ouvrez des conversations de groupe dans WhatsApp.',
      conversations: 'Aucune conversation trouvée.'
    }[activeTab];
  } else {
    hide(ui.emptyState);
  }
  if (activeTab === 'contacts')      renderContacts(list);
  if (activeTab === 'groups')        renderGroups(list);
  if (activeTab === 'conversations') renderConversations(list);
}

/* ── Filtre de recherche ── */
function applyFilter(q) {
  q = q.toLowerCase().trim();
  const f = (arr, keys) => q ? arr.filter(x => keys.some(k => (x[k] || '').toLowerCase().includes(q))) : [...arr];
  filtered.contacts      = f(data.contacts,      ['phone','name','shortName','about']);
  filtered.groups        = f(data.groups,        ['name','description']);
  filtered.conversations = f(data.conversations, ['name','phone']);
  updateCounts();
  renderActive();
}

/* ── Changement d'onglet ── */
function switchTab(tab) {
  activeTab = tab;
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  Object.entries(ui.panels).forEach(([k, p]) => k === tab ? p.classList.remove('hidden') : p.classList.add('hidden'));
  renderActive();
}

/* ── Export ── */
function exportCSV() {
  if (activeTab === 'contacts') {
    const rows = [['Téléphone','Nom','Business','Muet','Non-lus','Dernière activité','Source']];
    filtered.contacts.forEach(c => rows.push([c.phone, `"${(c.name||'').replace(/"/g,'""')}"`, c.isBusiness?'Oui':'Non', c.isMuted?'Oui':'Non', c.unreadCount||0, c.lastActivity||'', c.source||'']));
    download('contacts-wa.csv', 'text/csv', rows.map(r=>r.join(',')).join('\n'));
  } else if (activeTab === 'groups') {
    const rows = [['Groupe','Membres','Owner','Dernière activité','Description']];
    filtered.groups.forEach(g => rows.push([`"${g.name}"`, g.participantCount||0, g.owner||'', g.lastActivity||'', `"${(g.description||'').replace(/"/g,'""')}"`]));
    download('groupes-wa.csv', 'text/csv', rows.map(r=>r.join(',')).join('\n'));
  } else {
    const rows = [['Nom','Téléphone','Groupe','Non-lus','Muet','Épinglé','Dernière activité']];
    filtered.conversations.forEach(c => rows.push([`"${(c.name||'').replace(/"/g,'""')}"`, c.phone||'', c.isGroup?'Oui':'Non', c.unreadCount||0, c.isMuted?'Oui':'Non', c.isPinned?'Oui':'Non', c.lastActivity||'']));
    download('conversations-wa.csv', 'text/csv', rows.map(r=>r.join(',')).join('\n'));
  }
}

function exportJSON() {
  const payload = { contacts: filtered.contacts, groups: filtered.groups, conversations: filtered.conversations, exportedAt: new Date().toISOString() };
  download('whatsapp-export.json', 'application/json', JSON.stringify(payload, null, 2));
}

function download(name, type, content) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  Object.assign(document.createElement('a'), { href: url, download: name }).click();
  URL.revokeObjectURL(url);
}

/* ── Extraction ── */
async function doExtract() {
  ui.btnExtract.disabled = true;
  hide(ui.errorBox); hide(ui.syncResult);
  ui.loaderLabel.textContent = 'Extraction en cours…';
  show(ui.loader);
  setStatus('loading', 'Extraction…');

  try {
    let [tab] = await chrome.tabs.query({ url: 'https://web.whatsapp.com/*' });
    if (!tab) throw new Error("Aucun onglet WhatsApp Web trouvé.");

    const ping = await chrome.tabs.sendMessage(tab.id, { action: 'ping' }).catch(() => null);
    if (!ping) {
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['content.js'] });
      await new Promise(r => setTimeout(r, 600));
    }

    const res = await chrome.tabs.sendMessage(tab.id, { action: 'extract' });
    if (!res?.success) throw new Error(res?.error || 'Extraction échouée');

    data = {
      contacts:      res.data.contacts || [],
      groups:        res.data.groups || [],
      conversations: res.data.conversations || []
    };
    filtered = { contacts: [...data.contacts], groups: [...data.groups], conversations: [...data.conversations] };

    chrome.storage.local.set({ waData: data, extractedAt: Date.now() });

    hide(ui.loader);
    show(ui.tabsBar);
    show(ui.searchBar);
    updateCounts();
    renderActive();

    const total = data.contacts.length + data.groups.length + data.conversations.length;
    setStatus('ok', 'Extrait');
    ui.footerInfo.textContent = `${data.contacts.length} contacts · ${data.groups.length} groupes · ${data.conversations.length} conv.`;
    ui.btnCsv.disabled   = false;
    ui.btnJson.disabled  = false;
    ui.btnClear.disabled = false;
    ui.btnSync.disabled  = !vtigerConfig;

  } catch (err) {
    hide(ui.loader);
    show(ui.errorBox);
    ui.errorMsg.textContent = err.message;
    setStatus('error', 'Erreur');
  }
  ui.btnExtract.disabled = false;
}

/* ── Sync vTiger ── */
async function doSync() {
  if (!vtigerConfig) { chrome.runtime.openOptionsPage(); return; }
  const toSync = filtered.contacts.length ? filtered.contacts : data.contacts;
  if (!toSync.length) return;

  ui.btnSync.disabled = true;
  hide(ui.syncResult);
  show(ui.syncProgress);
  ui.syncBar.style.width = '0%';
  setStatus('loading', 'Sync…');

  let p = 0;
  const iv = setInterval(() => { p = Math.min(p + 2, 88); ui.syncBar.style.width = p + '%'; ui.syncLabel.textContent = `Sync contacts… ${p}%`; }, 250);

  try {
    // Sync groupes members aussi si option active
    const groupMembers = data.groups.flatMap(g => g.participants || []).filter(p => p.phone);
    const allToSync = [...toSync];
    groupMembers.forEach(m => { if (!allToSync.find(c => c.phone === m.phone)) allToSync.push({ phone: m.phone, name: m.name, source: 'groupe-membre' }); });

    const res = await chrome.runtime.sendMessage({ action: 'vtigerSync', contacts: allToSync, config: vtigerConfig, opts: vtigerOpts });
    clearInterval(iv);
    ui.syncBar.style.width = '100%';
    hide(ui.syncProgress);

    if (!res.success) throw new Error(res.error || 'Sync échouée');

    const log = res.log;
    ui.syncCreated.textContent = `+${log.created} créés`;
    ui.syncUpdated.textContent = `${log.updated} mis à jour`;
    ui.syncSkipped.textContent = `${log.skipped} ignorés`;
    log.errors?.length ? (ui.syncErrors.textContent = `${log.errors.length} erreurs`, show(ui.syncErrors)) : hide(ui.syncErrors);
    show(ui.syncResult);
    setStatus('ok', 'Synchronisé');
    ui.footerInfo.textContent = `Sync: ${log.created} créés, ${log.updated} màj — ${new Date().toLocaleTimeString('fr-FR')}`;
  } catch (err) {
    clearInterval(iv);
    hide(ui.syncProgress);
    show(ui.errorBox);
    ui.errorMsg.textContent = err.message;
    setStatus('error', 'Sync échouée');
  }
  ui.btnSync.disabled = false;
}

/* ── Clear ── */
function clearAll() {
  data = { contacts: [], groups: [], conversations: [] };
  filtered = { contacts: [], groups: [], conversations: [] };
  Object.values(ui.panels).forEach(p => p.innerHTML = '');
  hide(ui.tabsBar); hide(ui.searchBar); hide(ui.emptyState); hide(ui.errorBox); hide(ui.syncResult);
  ui.searchInput.value = '';
  [ui.btnCsv, ui.btnJson, ui.btnClear, ui.btnSync].forEach(b => b.disabled = true);
  setStatus('idle', 'En attente');
  ui.footerInfo.textContent = 'Données effacées';
  chrome.storage.local.remove(['waData', 'extractedAt', 'syncLog']);
}

/* ── vTiger dot ── */
function updateVtigerDot() {
  const ok = vtigerConfig?.url && vtigerConfig?.user && vtigerConfig?.key;
  ui.vtigerDot.className = `vtiger-dot vtiger-${ok ? 'ok' : 'nc'}`;
  ui.vtigerDot.title = ok ? `vTiger: ${vtigerConfig.url}` : 'vTiger non configuré';
  if (ok && data.contacts.length) ui.btnSync.disabled = false;
}

/* ── Init depuis cache ── */
chrome.storage.local.get(['vtigerConfig', 'vtigerOpts', 'waData', 'extractedAt', 'syncLog'],
  ({ vtigerConfig: cfg, vtigerOpts: opts, waData, extractedAt, syncLog }) => {
    if (cfg)  vtigerConfig = cfg;
    if (opts) vtigerOpts = { ...vtigerOpts, ...opts };
    updateVtigerDot();

    if (waData?.contacts?.length || waData?.groups?.length) {
      data = { contacts: waData.contacts || [], groups: waData.groups || [], conversations: waData.conversations || [] };
      filtered = { contacts: [...data.contacts], groups: [...data.groups], conversations: [...data.conversations] };
      show(ui.tabsBar); show(ui.searchBar);
      updateCounts(); renderActive();
      [ui.btnCsv, ui.btnJson, ui.btnClear].forEach(b => b.disabled = false);
      ui.btnSync.disabled = !vtigerConfig;
      setStatus('ok', 'Cache');
      ui.footerInfo.textContent = `${data.contacts.length} contacts · ${data.groups.length} groupes · ${data.conversations.length} conv.`;
      if (extractedAt) ui.footerInfo.textContent += ` (${new Date(extractedAt).toLocaleTimeString('fr-FR')})`;

      if (syncLog) {
        ui.syncCreated.textContent = `+${syncLog.created} créés`;
        ui.syncUpdated.textContent = `${syncLog.updated} mis à jour`;
        ui.syncSkipped.textContent = `${syncLog.skipped} ignorés`;
        if (syncLog.errors?.length) { ui.syncErrors.textContent = `${syncLog.errors.length} erreurs`; show(ui.syncErrors); }
        show(ui.syncResult);
      }
    }
  }
);

/* ── Events ── */
ui.btnExtract.addEventListener('click', doExtract);
ui.btnSync.addEventListener('click', doSync);
ui.btnCsv.addEventListener('click', exportCSV);
ui.btnJson.addEventListener('click', exportJSON);
ui.btnClear.addEventListener('click', clearAll);
ui.btnSettings.addEventListener('click', () => chrome.runtime.openOptionsPage());
ui.searchInput.addEventListener('input', e => applyFilter(e.target.value));
$('link-settings').addEventListener('click', e => { e.preventDefault(); chrome.runtime.openOptionsPage(); });
document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => switchTab(t.dataset.tab)));
