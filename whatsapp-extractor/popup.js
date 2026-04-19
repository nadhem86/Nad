/* WhatsApp Extractor - Popup Script */

let allContacts = [];
let filteredContacts = [];

const $ = id => document.getElementById(id);

const ui = {
  badge: $('status-badge'),
  btnExtract: $('btn-extract'),
  btnCsv: $('btn-export-csv'),
  btnJson: $('btn-export-json'),
  btnClear: $('btn-clear'),
  statsBar: $('stats-bar'),
  statTotal: $('stat-total'),
  statSource: $('stat-source'),
  statTime: $('stat-time'),
  searchBar: $('search-bar'),
  searchInput: $('search-input'),
  loader: $('loader'),
  errorBox: $('error-box'),
  errorMsg: $('error-msg'),
  contactList: $('contact-list'),
  emptyState: $('empty-state'),
  footerInfo: $('footer-info')
};

function setStatus(state, text) {
  ui.badge.className = `badge badge-${state}`;
  ui.badge.textContent = text;
}

function show(el) { el.classList.remove('hidden'); }
function hide(el) { el.classList.add('hidden'); }

function initials(name) {
  if (!name) return '?';
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return parts[0].slice(0, 2).toUpperCase();
}

function avatarClass(c) {
  if (c.isGroup) return 'group';
  if (c.isBusiness) return 'business';
  return '';
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
  div.dataset.phone = c.phone;

  const nameDisplay = c.name || c.shortName || '';
  const cls = avatarClass(c);

  const tags = [];
  if (c.isBusiness) tags.push('<span class="tag tag-business">Business</span>');
  if (c.isMuted) tags.push('<span class="tag tag-muted">Muet</span>');
  if (c.unreadCount > 0) tags.push(`<span class="tag tag-unread">${c.unreadCount}</span>`);
  if (c.source) tags.push(`<span class="tag tag-source">${c.source}</span>`);

  div.innerHTML = `
    <div class="contact-avatar ${cls}">${initials(nameDisplay)}</div>
    <div class="contact-info">
      <div class="contact-name">${nameDisplay || c.phone}</div>
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
          <path d="M19 3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-5 14H7v-2h7v2zm3-4H7v-2h10v2zm0-4H7V7h10v2z"/>
        </svg>
      </button>
    </div>
  `;

  div.querySelector('.btn-copy').addEventListener('click', () => {
    copyToClipboard(c.phone);
    const btn = div.querySelector('.btn-copy');
    btn.style.color = '#25D366';
    setTimeout(() => btn.style.color = '', 1200);
  });

  div.querySelector('.btn-open').addEventListener('click', () => {
    chrome.tabs.query({ url: 'https://web.whatsapp.com/*', active: false }, tabs => {
      const tab = tabs[0];
      if (tab) {
        chrome.tabs.sendMessage(tab.id, { action: 'openContact', phone: c.phone });
        chrome.tabs.update(tab.id, { active: true });
      } else {
        chrome.tabs.create({ url: `https://web.whatsapp.com/send?phone=${c.phone.replace(/\D/g, '')}` });
      }
    });
  });

  return div;
}

function renderList(contacts) {
  ui.contactList.innerHTML = '';
  if (!contacts.length) {
    show(ui.emptyState);
    return;
  }
  hide(ui.emptyState);
  const frag = document.createDocumentFragment();
  contacts.forEach(c => frag.appendChild(renderContact(c)));
  ui.contactList.appendChild(frag);
}

function applyFilter(query) {
  const q = query.toLowerCase().trim();
  if (!q) {
    filteredContacts = [...allContacts];
  } else {
    filteredContacts = allContacts.filter(c =>
      (c.phone && c.phone.includes(q)) ||
      (c.name && c.name.toLowerCase().includes(q)) ||
      (c.shortName && c.shortName.toLowerCase().includes(q))
    );
  }
  renderList(filteredContacts);
  ui.statTotal.textContent = `${filteredContacts.length} / ${allContacts.length} contacts`;
}

function exportCSV() {
  const rows = [['Téléphone', 'Nom', 'Business', 'Muet', 'Non-lus', 'Dernière activité', 'Source']];
  filteredContacts.forEach(c => {
    rows.push([
      c.phone || '',
      `"${(c.name || '').replace(/"/g, '""')}"`,
      c.isBusiness ? 'Oui' : 'Non',
      c.isMuted ? 'Oui' : 'Non',
      c.unreadCount || 0,
      c.lastSeen || '',
      c.source || ''
    ]);
  });
  const csv = rows.map(r => r.join(',')).join('\n');
  download('contacts-whatsapp.csv', 'text/csv', csv);
}

function exportJSON() {
  const json = JSON.stringify(filteredContacts, null, 2);
  download('contacts-whatsapp.json', 'application/json', json);
}

function download(filename, type, content) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

async function getWATab() {
  return new Promise(resolve => {
    chrome.tabs.query({ url: 'https://web.whatsapp.com/*' }, tabs => {
      resolve(tabs[0] || null);
    });
  });
}

async function doExtract() {
  ui.btnExtract.disabled = true;
  hide(ui.errorBox);
  hide(ui.statsBar);
  hide(ui.searchBar);
  hide(ui.emptyState);
  ui.contactList.innerHTML = '';
  show(ui.loader);
  setStatus('loading', 'Extraction…');

  try {
    const tab = await getWATab();
    if (!tab) throw new Error("Aucun onglet WhatsApp Web trouvé.");

    const pingRes = await chrome.tabs.sendMessage(tab.id, { action: 'ping' }).catch(() => null);
    if (!pingRes) {
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['content.js'] });
      await new Promise(r => setTimeout(r, 500));
    }

    const res = await chrome.tabs.sendMessage(tab.id, { action: 'extract' });

    if (!res || !res.success) throw new Error(res?.error || 'Extraction échouée');

    allContacts = res.data.contacts || [];
    filteredContacts = [...allContacts];

    hide(ui.loader);

    if (!allContacts.length) {
      show(ui.emptyState);
      setStatus('idle', 'Vide');
      ui.footerInfo.textContent = 'Aucun contact extrait - naviguez dans vos chats';
    } else {
      show(ui.statsBar);
      show(ui.searchBar);
      ui.statTotal.textContent = `${allContacts.length} contacts`;
      ui.statSource.textContent = res.data.storeFound ? 'via Store JS' : 'via DOM';
      ui.statTime.textContent = new Date().toLocaleTimeString('fr-FR');
      ui.btnCsv.disabled = false;
      ui.btnJson.disabled = false;
      ui.btnClear.disabled = false;
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

function clearData() {
  allContacts = [];
  filteredContacts = [];
  ui.contactList.innerHTML = '';
  hide(ui.statsBar);
  hide(ui.searchBar);
  hide(ui.emptyState);
  hide(ui.errorBox);
  ui.btnCsv.disabled = true;
  ui.btnJson.disabled = true;
  ui.btnClear.disabled = true;
  setStatus('idle', 'En attente');
  ui.footerInfo.textContent = 'Données effacées';
}

// Load previously extracted data from storage
chrome.storage.local.get(['contacts', 'extractedAt'], ({ contacts, extractedAt }) => {
  if (contacts && contacts.length) {
    allContacts = contacts;
    filteredContacts = [...contacts];
    show(ui.statsBar);
    show(ui.searchBar);
    ui.statTotal.textContent = `${contacts.length} contacts`;
    ui.statSource.textContent = 'cache';
    ui.statTime.textContent = extractedAt ? new Date(extractedAt).toLocaleTimeString('fr-FR') : '';
    ui.btnCsv.disabled = false;
    ui.btnJson.disabled = false;
    ui.btnClear.disabled = false;
    setStatus('ok', 'Cache');
    renderList(filteredContacts);
  }
});

ui.btnExtract.addEventListener('click', doExtract);
ui.btnCsv.addEventListener('click', exportCSV);
ui.btnJson.addEventListener('click', exportJSON);
ui.btnClear.addEventListener('click', clearData);
ui.searchInput.addEventListener('input', e => applyFilter(e.target.value));

// Save to storage after extraction
const origDoExtract = doExtract;
ui.btnExtract.addEventListener('click', () => {
  const orig = setStatus;
  const checkSave = setInterval(() => {
    if (allContacts.length > 0) {
      clearInterval(checkSave);
      chrome.storage.local.set({ contacts: allContacts, extractedAt: Date.now() });
    }
  }, 500);
  setTimeout(() => clearInterval(checkSave), 10000);
});
