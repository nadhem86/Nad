/* WhatsApp → vTiger Extractor - Popup Script */

let allContacts = [];
let filteredContacts = [];
let vtigerConfig = null;
let vtigerOpts = { module: 'Contacts', duplicate: 'skip', businessTag: true, waSource: true, onlyNamed: false };

const $ = id => document.getElementById(id);

const ui = {
  badge:       $('status-badge'),
  btnExtract:  $('btn-extract'),
  btnSync:     $('btn-sync'),
  btnCsv:      $('btn-export-csv'),
  btnJson:     $('btn-export-json'),
  btnClear:    $('btn-clear'),
  btnSettings: $('btn-settings'),
  statsBar:    $('stats-bar'),
  statTotal:   $('stat-total'),
  statSource:  $('stat-source'),
  statTime:    $('stat-time'),
  searchBar:   $('search-bar'),
  searchInput: $('search-input'),
  loader:      $('loader'),
  loaderLabel: $('loader-label'),
  errorBox:    $('error-box'),
  errorMsg:    $('error-msg'),
  contactList: $('contact-list'),
  emptyState:  $('empty-state'),
  footerInfo:  $('footer-info'),
  vtigerDot:   $('vtiger-status'),
  syncProgress:$('sync-progress'),
  syncBar:     $('sync-bar'),
  syncLabel:   $('sync-label'),
  syncResult:  $('sync-result'),
  syncCreated: $('sync-created'),
  syncUpdated: $('sync-updated'),
  syncSkipped: $('sync-skipped'),
  syncErrors:  $('sync-errors')
};

function setStatus(state, text) {
  ui.badge.className = `badge badge-${state}`;
  ui.badge.textContent = text;
}

const show = el => el.classList.remove('hidden');
const hide = el => el.classList.add('hidden');

function initials(name) {
  if (!name) return '?';
  const p = name.trim().split(/\s+/);
  return p.length >= 2 ? (p[0][0] + p[1][0]).toUpperCase() : p[0].slice(0, 2).toUpperCase();
}

function copyToClipboard(text) {
  navigator.clipboard.writeText(text).catch(() => {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
  });
}

function renderContact(c) {
  const div = document.createElement('div');
  div.className = 'contact-item';

  const name = c.name || c.shortName || '';
  const cls = c.isGroup ? 'group' : c.isBusiness ? 'business' : '';

  const tags = [];
  if (c.isBusiness)     tags.push('<span class="tag tag-business">Business</span>');
  if (c.isMuted)        tags.push('<span class="tag tag-muted">Muet</span>');
  if (c.unreadCount > 0) tags.push(`<span class="tag tag-unread">${c.unreadCount}</span>`);
  if (c.source)         tags.push(`<span class="tag tag-source">${c.source}</span>`);

  div.innerHTML = `
    <div class="contact-avatar ${cls}">${initials(name)}</div>
    <div class="contact-info">
      <div class="contact-name">${name || c.phone}</div>
      <div class="contact-phone">${c.phone}</div>
      ${tags.length ? `<div class="contact-meta">${tags.join('')}</div>` : ''}
    </div>
    <div class="contact-actions">
      <button class="action-btn btn-copy" title="Copier le numéro">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
          <path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/>
        </svg>
      </button>
      <button class="action-btn btn-open" title="Ouvrir dans WhatsApp">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
          <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51a6.47 6.47 0 00-.57-.01c-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347z"/>
        </svg>
      </button>
    </div>
  `;

  div.querySelector('.btn-copy').addEventListener('click', () => {
    copyToClipboard(c.phone);
    const btn = div.querySelector('.btn-copy');
    btn.style.color = '#25D366';
    setTimeout(() => (btn.style.color = ''), 1200);
  });

  div.querySelector('.btn-open').addEventListener('click', () => {
    const phone = c.phone.replace(/\D/g, '');
    chrome.tabs.query({ url: 'https://web.whatsapp.com/*' }, tabs => {
      if (tabs[0]) {
        chrome.tabs.sendMessage(tabs[0].id, { action: 'openContact', phone: c.phone });
        chrome.tabs.update(tabs[0].id, { active: true });
      } else {
        chrome.tabs.create({ url: `https://web.whatsapp.com/send?phone=${phone}` });
      }
    });
  });

  return div;
}

function renderList(contacts) {
  ui.contactList.innerHTML = '';
  if (!contacts.length) { show(ui.emptyState); return; }
  hide(ui.emptyState);
  const frag = document.createDocumentFragment();
  contacts.forEach(c => frag.appendChild(renderContact(c)));
  ui.contactList.appendChild(frag);
}

function applyFilter(query) {
  const q = query.toLowerCase().trim();
  filteredContacts = q
    ? allContacts.filter(c =>
        (c.phone && c.phone.includes(q)) ||
        (c.name  && c.name.toLowerCase().includes(q))
      )
    : [...allContacts];
  renderList(filteredContacts);
  ui.statTotal.textContent = `${filteredContacts.length} / ${allContacts.length} contacts`;
}

function exportCSV() {
  const rows = [['Téléphone','Nom','Business','Muet','Non-lus','Dernière activité','Source']];
  filteredContacts.forEach(c => {
    rows.push([
      c.phone || '',
      `"${(c.name || '').replace(/"/g,'""')}"`,
      c.isBusiness ? 'Oui' : 'Non',
      c.isMuted    ? 'Oui' : 'Non',
      c.unreadCount || 0,
      c.lastSeen || '',
      c.source || ''
    ]);
  });
  download('contacts-whatsapp.csv', 'text/csv', rows.map(r => r.join(',')).join('\n'));
}

function exportJSON() {
  download('contacts-whatsapp.json', 'application/json', JSON.stringify(filteredContacts, null, 2));
}

function download(filename, type, content) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

async function getWATab() {
  return new Promise(resolve =>
    chrome.tabs.query({ url: 'https://web.whatsapp.com/*' }, tabs => resolve(tabs[0] || null))
  );
}

async function doExtract() {
  ui.btnExtract.disabled = true;
  hide(ui.errorBox); hide(ui.statsBar); hide(ui.searchBar);
  hide(ui.emptyState); hide(ui.syncResult);
  ui.contactList.innerHTML = '';
  ui.loaderLabel.textContent = 'Extraction en cours…';
  show(ui.loader);
  setStatus('loading', 'Extraction…');

  try {
    const tab = await getWATab();
    if (!tab) throw new Error("Aucun onglet WhatsApp Web trouvé.");

    const ping = await chrome.tabs.sendMessage(tab.id, { action: 'ping' }).catch(() => null);
    if (!ping) {
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['content.js'] });
      await new Promise(r => setTimeout(r, 500));
    }

    const res = await chrome.tabs.sendMessage(tab.id, { action: 'extract' });
    if (!res?.success) throw new Error(res?.error || 'Extraction échouée');

    allContacts = res.data.contacts || [];
    filteredContacts = [...allContacts];

    hide(ui.loader);
    chrome.storage.local.set({ contacts: allContacts, extractedAt: Date.now() });

    if (!allContacts.length) {
      show(ui.emptyState);
      setStatus('idle', 'Vide');
    } else {
      show(ui.statsBar); show(ui.searchBar);
      ui.statTotal.textContent = `${allContacts.length} contacts`;
      ui.statSource.textContent = res.data.storeFound ? 'Store JS' : 'DOM';
      ui.statTime.textContent = new Date().toLocaleTimeString('fr-FR');
      ui.btnCsv.disabled = false;
      ui.btnJson.disabled = false;
      ui.btnClear.disabled = false;
      ui.btnSync.disabled = !vtigerConfig;
      setStatus('ok', 'Extrait');
      ui.footerInfo.textContent = `Extrait le ${new Date().toLocaleDateString('fr-FR')}`;
      renderList(filteredContacts);
    }
  } catch (err) {
    hide(ui.loader);
    show(ui.errorBox);
    ui.errorMsg.textContent = err.message;
    setStatus('error', 'Erreur');
  }

  ui.btnExtract.disabled = false;
}

async function doSync() {
  if (!vtigerConfig) {
    chrome.runtime.openOptionsPage();
    return;
  }

  const toSync = filteredContacts.length ? filteredContacts : allContacts;
  if (!toSync.length) return;

  ui.btnSync.disabled = true;
  hide(ui.syncResult);
  show(ui.syncProgress);
  ui.syncBar.style.width = '0%';
  ui.syncLabel.textContent = `Connexion à vTiger…`;
  setStatus('loading', 'Sync…');

  // Simulated progress during background sync
  let progress = 0;
  const progressInterval = setInterval(() => {
    progress = Math.min(progress + 2, 90);
    ui.syncBar.style.width = progress + '%';
    ui.syncLabel.textContent = `Synchronisation… ${Math.round(progress)}%`;
  }, 300);

  try {
    const res = await chrome.runtime.sendMessage({
      action: 'vtigerSync',
      contacts: toSync,
      config: vtigerConfig,
      opts: vtigerOpts
    });

    clearInterval(progressInterval);
    ui.syncBar.style.width = '100%';
    hide(ui.syncProgress);

    if (!res.success) throw new Error(res.error || 'Sync échouée');

    const log = res.log;
    ui.syncCreated.textContent = `+${log.created} créés`;
    ui.syncUpdated.textContent = `${log.updated} mis à jour`;
    ui.syncSkipped.textContent = `${log.skipped} ignorés`;

    if (log.errors.length) {
      ui.syncErrors.textContent = `${log.errors.length} erreurs`;
      show(ui.syncErrors);
    } else {
      hide(ui.syncErrors);
    }

    show(ui.syncResult);
    setStatus('ok', 'Synchronisé');
    ui.footerInfo.textContent = `Sync: ${log.created} créés, ${log.updated} màj, ${log.skipped} ignorés`;
  } catch (err) {
    clearInterval(progressInterval);
    hide(ui.syncProgress);
    show(ui.errorBox);
    ui.errorMsg.textContent = err.message;
    setStatus('error', 'Sync échouée');
  }

  ui.btnSync.disabled = false;
}

function clearData() {
  allContacts = []; filteredContacts = [];
  ui.contactList.innerHTML = '';
  hide(ui.statsBar); hide(ui.searchBar);
  hide(ui.emptyState); hide(ui.errorBox); hide(ui.syncResult);
  ui.btnCsv.disabled = true; ui.btnJson.disabled = true;
  ui.btnClear.disabled = true; ui.btnSync.disabled = true;
  setStatus('idle', 'En attente');
  ui.footerInfo.textContent = 'Données effacées';
  chrome.storage.local.remove(['contacts', 'extractedAt', 'syncLog']);
}

function updateVtigerDot() {
  if (vtigerConfig?.url && vtigerConfig?.user && vtigerConfig?.key) {
    ui.vtigerDot.className = 'vtiger-dot vtiger-ok';
    ui.vtigerDot.title = `vTiger: ${vtigerConfig.url}`;
    if (allContacts.length) ui.btnSync.disabled = false;
  } else {
    ui.vtigerDot.className = 'vtiger-dot vtiger-nc';
    ui.vtigerDot.title = 'vTiger non configuré — cliquez sur paramètres';
  }
}

// Init: load stored config + contacts
chrome.storage.local.get(['vtigerConfig', 'vtigerOpts', 'contacts', 'extractedAt', 'syncLog'],
  ({ vtigerConfig: cfg, vtigerOpts: opts, contacts, extractedAt, syncLog }) => {

    if (cfg)  { vtigerConfig = cfg; }
    if (opts) { vtigerOpts = { ...vtigerOpts, ...opts }; }
    updateVtigerDot();

    if (contacts?.length) {
      allContacts = contacts;
      filteredContacts = [...contacts];
      show(ui.statsBar); show(ui.searchBar);
      ui.statTotal.textContent = `${contacts.length} contacts`;
      ui.statSource.textContent = 'cache';
      ui.statTime.textContent = extractedAt ? new Date(extractedAt).toLocaleTimeString('fr-FR') : '';
      ui.btnCsv.disabled = false;
      ui.btnJson.disabled = false;
      ui.btnClear.disabled = false;
      ui.btnSync.disabled = !vtigerConfig;
      setStatus('ok', 'Cache');
      renderList(filteredContacts);

      if (syncLog) {
        ui.syncCreated.textContent = `+${syncLog.created} créés`;
        ui.syncUpdated.textContent = `${syncLog.updated} mis à jour`;
        ui.syncSkipped.textContent = `${syncLog.skipped} ignorés`;
        if (syncLog.errors?.length) {
          ui.syncErrors.textContent = `${syncLog.errors.length} erreurs`;
          show(ui.syncErrors);
        }
        show(ui.syncResult);
        ui.footerInfo.textContent = `Dernière sync: ${new Date(syncLog.syncedAt).toLocaleString('fr-FR')}`;
      }
    }
  }
);

ui.btnExtract.addEventListener('click', doExtract);
ui.btnSync.addEventListener('click', doSync);
ui.btnCsv.addEventListener('click', exportCSV);
ui.btnJson.addEventListener('click', exportJSON);
ui.btnClear.addEventListener('click', clearData);
ui.searchInput.addEventListener('input', e => applyFilter(e.target.value));
ui.btnSettings.addEventListener('click', () => chrome.runtime.openOptionsPage());
$('link-settings').addEventListener('click', e => { e.preventDefault(); chrome.runtime.openOptionsPage(); });
