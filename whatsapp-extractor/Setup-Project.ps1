#Requires -Version 5.1
# ============================================================
#  WhatsApp -> vTiger  |  Setup complet du projet VS Code
#  Cible : C:\Users\think\Documents\Project\Chrome extention
# ============================================================

$ErrorActionPreference = 'Stop'
$TargetDir = "C:\Users\think\Documents\Project\Chrome extention"

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "   WhatsApp -> vTiger  |  Creation du projet" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

# -- Creer les dossiers ----------------------------------------
Write-Host "Creation des dossiers..." -ForegroundColor Yellow
foreach ($d in @($TargetDir, "$TargetDir\icons", "$TargetDir\.vscode")) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}

# -- Helper ----------------------------------------------------
function Set-File($RelPath, $Content) {
    $full = Join-Path $TargetDir $RelPath
    $dir  = Split-Path $full -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [System.IO.File]::WriteAllText($full, $Content, [System.Text.UTF8Encoding]::new($false))
    Write-Host "  [OK] $RelPath" -ForegroundColor Green
}

Write-Host ""
Write-Host "Ecriture des fichiers source..." -ForegroundColor Yellow

Set-File 'manifest.json' @'
{
  "manifest_version": 3,
  "name": "WhatsApp Contact Extractor",
  "version": "1.0.0",
  "description": "Extrait les numéros de téléphone et infos des contacts WhatsApp Web",
  "permissions": [
    "activeTab",
    "scripting",
    "storage",
    "downloads"
  ],
  "host_permissions": [
    "https://web.whatsapp.com/*"
  ],
  "action": {
    "default_popup": "popup.html",
    "default_icon": {
      "16": "icons/icon16.png",
      "48": "icons/icon48.png",
      "128": "icons/icon128.png"
    }
  },
  "content_scripts": [
    {
      "matches": ["https://web.whatsapp.com/*"],
      "js": ["content.js"],
      "run_at": "document_idle"
    }
  ],
  "options_page": "options.html",
  "background": {
    "service_worker": "background.js"
  },
  "icons": {
    "16": "icons/icon16.png",
    "48": "icons/icon48.png",
    "128": "icons/icon128.png"
  }
}

'@

Set-File 'content.js' @'
/* WhatsApp Web — Extracteur complet : contacts, groupes, conversations */

function injectExtractor() {
  return new Promise((resolve) => {
    const script = document.createElement('script');
    script.textContent = `
    (function() {
      /* ── Trouver le Store WhatsApp Web ── */
      function findStore() {
        try {
          if (window.Store?.Chat) return window.Store;

          // Scan des clés globales
          for (const k of Object.keys(window)) {
            const v = window[k];
            if (v && typeof v === 'object' && v.Chat && v.Contact) return v;
          }

          // Système de modules webpack (require)
          if (typeof require === 'function') {
            for (const mod of ['WAWebChatStore','WAWebContactStore','WAWebGroupStore']) {
              try { const m = require(mod); if (m) return m; } catch(_) {}
            }
            // Scan tous les modules enregistrés
            try {
              const cache = require.m || {};
              for (const id of Object.keys(cache)) {
                try {
                  const m = require(id);
                  if (m?.Chat?.models && m?.Contact?.models) return m;
                } catch(_) {}
              }
            } catch(_) {}
          }
        } catch(_) {}
        return null;
      }

      /* ── Normalise un JID en numéro E.164 ── */
      function jidToPhone(id) {
        if (!id) return null;
        const user = typeof id === 'string' ? id.split('@')[0] : (id.user || '');
        if (!user || user.includes('-') || !/^\\d{6,15}$/.test(user)) return null;
        return '+' + user;
      }

      /* ── Résoudre le nom d'un participant depuis Contact Store ── */
      function resolveName(store, jid) {
        try {
          if (!store?.Contact?.get) return '';
          const c = store.Contact.get(jid);
          return c ? (c.name || c.pushname || c.notify || '') : '';
        } catch(_) { return ''; }
      }

      /* ── Extraire le dernier message d'un chat ── */
      function lastMsg(chat) {
        try {
          const msg = chat.lastMessage || (chat.msgs?.last ? chat.msgs.last() : null);
          if (!msg) return null;
          return {
            body: (msg.body || msg.caption || '').slice(0, 120),
            type: msg.type || 'chat',
            fromMe: !!msg.fromMe,
            timestamp: msg.t ? new Date(msg.t * 1000).toISOString() : null
          };
        } catch(_) { return null; }
      }

      /* ═══════════════════════════════════════
         EXTRACTION PRINCIPALE VIA STORE
      ═══════════════════════════════════════ */
      function extractAll(store) {
        const contacts     = [];   // contacts individuels
        const groups       = [];   // groupes
        const conversations = [];  // toutes les conversations (privées + groupes)
        const seenPhones   = new Set();
        const seenChats    = new Set();

        /* ── 1. Store.Contact — liste complète des contacts ── */
        try {
          (store.Contact?.models || []).forEach(c => {
            const phone = jidToPhone(c.id);
            if (!phone || seenPhones.has(phone)) return;
            seenPhones.add(phone);
            contacts.push({
              phone,
              name:        c.name || c.pushname || c.notify || '',
              shortName:   c.shortName || '',
              pushname:    c.pushname || '',
              isBusiness:  !!(c.isBusiness),
              isEnterprise:!!(c.isEnterprise),
              isWAContact: c.isWAContact !== false,
              about:       c.statusV3?.status || '',
              source:      'Store.Contact'
            });
          });
        } catch(_) {}

        /* ── 2. Store.Chat — conversations + groupes ── */
        try {
          (store.Chat?.models || []).forEach(chat => {
            const chatId = chat.id?._serialized || '';
            if (seenChats.has(chatId)) return;
            seenChats.add(chatId);

            const isGroup = !!(chat.isGroup || chat.id?.server === 'g.us');
            const name    = chat.name || chat.formattedTitle || '';
            const t       = chat.t ? new Date(chat.t * 1000).toISOString() : null;
            const unread  = chat.unreadCount || 0;
            const muted   = (chat.mute || 0) > 0;
            const pinned  = !!(chat.pin);
            const archived= !!(chat.archive);
            const lm      = lastMsg(chat);

            /* ── Conversation (toutes) ── */
            conversations.push({
              id:          chatId,
              name,
              isGroup,
              isPinned:    pinned,
              isArchived:  archived,
              isMuted:     muted,
              unreadCount: unread,
              lastActivity:t,
              lastMessage: lm,
              source:      'Store.Chat'
            });

            if (isGroup) {
              /* ── Groupe ── */
              const participants = [];
              try {
                const meta = chat.groupMetadata || store.GroupMetadata?.get(chatId);
                const parts = meta?.participants?.models || meta?.participants || [];
                parts.forEach(p => {
                  const pPhone = jidToPhone(p.id);
                  if (!pPhone) return;
                  const pName  = resolveName(store, p.id?._serialized || p.id);
                  participants.push({
                    phone:        pPhone,
                    name:         pName || p.id?.user || '',
                    isAdmin:      !!(p.isAdmin || p.isSuperAdmin),
                    isSuperAdmin: !!(p.isSuperAdmin)
                  });
                  // Ajouter les membres de groupe dans contacts s'ils n'y sont pas
                  if (!seenPhones.has(pPhone)) {
                    seenPhones.add(pPhone);
                    contacts.push({
                      phone:      pPhone,
                      name:       pName || '',
                      isBusiness: false,
                      isWAContact:true,
                      source:     'Store.Group.participant'
                    });
                  }
                });
              } catch(_) {}

              const meta = chat.groupMetadata || null;
              groups.push({
                id:           chatId,
                name,
                description:  meta?.desc || '',
                createdAt:    meta?.creation ? new Date(meta.creation * 1000).toISOString() : null,
                owner:        jidToPhone(meta?.owner) || '',
                participantCount: participants.length,
                participants,
                isPinned:     pinned,
                isArchived:   archived,
                isMuted:      muted,
                unreadCount:  unread,
                lastActivity: t,
                lastMessage:  lm,
                source:       'Store.Chat.group'
              });

            } else {
              /* ── Conversation individuelle ── */
              const phone = jidToPhone(chat.id);
              if (phone) {
                const existing = contacts.find(c => c.phone === phone);
                const extra = {
                  lastSeen:    t,
                  unreadCount: unread,
                  isMuted:     muted,
                  isPinned:    pinned,
                  isArchived:  archived,
                  lastMessage: lm
                };
                if (existing) {
                  Object.assign(existing, extra);
                } else if (!seenPhones.has(phone)) {
                  seenPhones.add(phone);
                  contacts.push({ phone, name, ...extra, isBusiness: false, source: 'Store.Chat.private' });
                }
              }
            }
          });
        } catch(_) {}

        return { contacts, groups, conversations };
      }

      /* ═══════════════════════════════════════
         FALLBACK — SCAN DOM
      ═══════════════════════════════════════ */
      function extractDOM() {
        const contacts     = [];
        const groups       = [];
        const conversations = [];
        const seen = new Set();
        const phoneRe = /\\+?[1-9]\\d{6,14}/g;

        /* Liste de chats dans le panneau gauche */
        document.querySelectorAll('[data-testid="cell-frame-container"]').forEach(el => {
          const titleEl = el.querySelector('[data-testid="cell-frame-title"]');
          const subtitleEl = el.querySelector('[data-testid="last-msg-status"] ~ span, [data-testid="cell-frame-secondary"]');
          const name    = titleEl?.textContent.trim() || '';
          const lastMsg = subtitleEl?.textContent.trim() || '';
          const dataId  = el.getAttribute('data-id') || '';
          const phones  = dataId.match(phoneRe) || [];
          const isGroup = dataId.includes('@g.us') || dataId.includes('-');

          const badgeEl = el.querySelector('[data-testid="icon-muted"]');
          const unreadEl= el.querySelector('[aria-label*="non lu"], [data-testid="icon-unread-count"]');
          const unread  = unreadEl ? parseInt(unreadEl.textContent) || 1 : 0;

          if (isGroup) {
            if (!seen.has(dataId)) {
              seen.add(dataId);
              groups.push({ id: dataId, name, participantCount: 0, participants: [], lastMsg, source: 'DOM' });
              conversations.push({ id: dataId, name, isGroup: true, unreadCount: unread, lastMsg, source: 'DOM' });
            }
          } else {
            phones.forEach(phone => {
              if (!seen.has(phone)) {
                seen.add(phone);
                contacts.push({ phone, name, source: 'DOM-chatlist' });
                conversations.push({ id: dataId, name, phone, isGroup: false, unreadCount: unread, lastMsg, source: 'DOM' });
              }
            });
          }
        });

        /* Panneau info contact ouvert */
        document.querySelectorAll('[data-testid="contact-info-drawer"]').forEach(panel => {
          const text    = panel.innerText;
          const phones  = text.match(phoneRe) || [];
          const nameEl  = panel.querySelector('h2, [data-testid="contact-name"]');
          const aboutEl = panel.querySelector('[data-testid="contact-info-about"]');
          const name    = nameEl?.textContent.trim() || '';
          const about   = aboutEl?.textContent.trim() || '';
          phones.forEach(phone => {
            if (!seen.has(phone)) {
              seen.add(phone);
              contacts.push({ phone, name, about, source: 'DOM-info-panel' });
            }
          });
        });

        /* Panneau info groupe ouvert */
        document.querySelectorAll('[data-testid="group-info-drawer"]').forEach(panel => {
          const name    = panel.querySelector('h2')?.textContent.trim() || '';
          const desc    = panel.querySelector('[data-testid="group-description"]')?.textContent.trim() || '';
          const members = [];
          panel.querySelectorAll('[data-testid="participant-item"]').forEach(item => {
            const pName  = item.querySelector('[data-testid="participant-name"]')?.textContent.trim() || '';
            const phones = item.innerText.match(phoneRe) || [];
            phones.forEach(phone => {
              members.push({ phone, name: pName });
              if (!seen.has(phone)) { seen.add(phone); contacts.push({ phone, name: pName, source: 'DOM-group-member' }); }
            });
          });
          if (name) groups.push({ name, description: desc, participants: members, participantCount: members.length, source: 'DOM-group-panel' });
        });

        return { contacts, groups, conversations };
      }

      /* ═══════════════════════════════════════
         MAIN
      ═══════════════════════════════════════ */
      (function run() {
        const store  = findStore();
        let result   = { contacts: [], groups: [], conversations: [], storeFound: false };

        if (store) {
          result = { ...extractAll(store), storeFound: true };
        }

        // Merge DOM data
        const dom = extractDOM();
        const mergeByPhone = (arr, add) => {
          add.forEach(d => {
            if (!d.phone || arr.find(x => x.phone === d.phone)) return;
            arr.push(d);
          });
        };
        mergeByPhone(result.contacts, dom.contacts);
        dom.groups.forEach(dg => {
          if (!result.groups.find(g => g.name === dg.name)) result.groups.push(dg);
        });
        dom.conversations.forEach(dc => {
          const key = dc.id || dc.phone;
          if (key && !result.conversations.find(c => (c.id || c.phone) === key)) result.conversations.push(dc);
        });

        result.timestamp = new Date().toISOString();
        result.totals = {
          contacts:      result.contacts.length,
          groups:        result.groups.length,
          conversations: result.conversations.length
        };

        window.__WA_EXTRACTED__ = result;
        document.dispatchEvent(new CustomEvent('__wa_data_ready__', { detail: result }));
      })();
    })();
    `;

    document.head.appendChild(script);
    script.remove();

    const handler = (e) => {
      document.removeEventListener('__wa_data_ready__', handler);
      resolve(e.detail);
    };
    document.addEventListener('__wa_data_ready__', handler);

    setTimeout(() => {
      document.removeEventListener('__wa_data_ready__', handler);
      resolve(window.__WA_EXTRACTED__ || { contacts: [], groups: [], conversations: [], storeFound: false, totals: { contacts: 0, groups: 0, conversations: 0 } });
    }, 6000);
  });
}

/* ── Listener messages depuis le popup ── */
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {

  if (msg.action === 'extract') {
    injectExtractor()
      .then(data => sendResponse({ success: true, data }))
      .catch(err  => sendResponse({ success: false, error: err.message }));
    return true;
  }

  if (msg.action === 'ping') {
    sendResponse({ alive: true, url: window.location.href });
    return true;
  }

  if (msg.action === 'openContact') {
    const phone = msg.phone.replace(/\D/g, '');
    window.open(`https://web.whatsapp.com/send?phone=${phone}`, '_self');
    sendResponse({ done: true });
    return true;
  }

  if (msg.action === 'openGroup') {
    const id = msg.id;
    window.open(`https://web.whatsapp.com/accept?code=${id}`, '_self');
    sendResponse({ done: true });
    return true;
  }
});

'@

Set-File 'background.js' @'
/* WhatsApp Extractor - Service Worker + vTiger sync */

importScripts('vtiger.js');

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.remove(['contacts', 'extractedAt', 'syncLog']);
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === 'complete' && tab.url?.startsWith('https://web.whatsapp.com')) {
    chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] }).catch(() => {});
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === 'vtigerTest') {
    handleTest(msg.config).then(sendResponse);
    return true;
  }
  if (msg.action === 'vtigerSync') {
    handleSync(msg.contacts, msg.config, msg.opts).then(sendResponse);
    return true;
  }
});

async function handleTest(config) {
  try {
    const client = new VtigerClient(config);
    await client.login();
    const session = client.sessionName;
    const user = client.userId;
    await client.logout();
    return { success: true, session, user };
  } catch (e) {
    return { success: false, error: e.message };
  }
}

async function handleSync(contacts, config, opts) {
  const log = { created: 0, updated: 0, skipped: 0, errors: [], total: contacts.length };

  if (!contacts.length) return { success: true, log };

  let client;
  try {
    client = new VtigerClient(config);
    await client.login();
  } catch (e) {
    return { success: false, error: 'Login vTiger échoué: ' + e.message, log };
  }

  const module = opts.module || 'Contacts';

  for (const wa of contacts) {
    if (opts.onlyNamed && !wa.name) { log.skipped++; continue; }

    try {
      const mapped = VtigerClient.mapContact(wa);
      if (!opts.waSource) delete mapped.leadsource;

      if (opts.duplicate === 'create') {
        await client.create(module, mapped);
        log.created++;
        continue;
      }

      const existing = await client.findContactByPhone(wa.phone);

      if (existing) {
        if (opts.duplicate === 'update') {
          await client.update({ ...mapped, id: existing.id });
          log.updated++;
        } else {
          log.skipped++;
        }
      } else {
        await client.create(module, mapped);
        log.created++;
      }
    } catch (e) {
      log.errors.push({ phone: wa.phone, error: e.message });
    }
  }

  try { await client.logout(); } catch {}

  // Persist sync log
  await chrome.storage.local.set({ syncLog: { ...log, syncedAt: Date.now() } });

  return { success: true, log };
}

'@

Set-File 'vtiger.js' @'
/* vTiger CRM REST API client */

class VtigerClient {
  constructor({ url, username, accessKey }) {
    this.baseUrl = url.replace(/\/$/, '') + '/webservice.php';
    this.username = username;
    this.accessKey = accessKey;
    this.sessionName = null;
    this.userId = null;
  }

  async _md5(str) {
    const buf = await crypto.subtle.digest('SHA-1', new TextEncoder().encode(str));
    // vTiger uses MD5 — use a pure-JS fallback
    return md5(str);
  }

  async _request(params, method = 'GET', body = null) {
    const url = new URL(this.baseUrl);
    if (method === 'GET') {
      Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
      const res = await fetch(url.toString(), { method: 'GET' });
      return res.json();
    } else {
      const form = new URLSearchParams(params);
      const res = await fetch(url.toString(), {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: form.toString()
      });
      return res.json();
    }
  }

  async login() {
    // Step 1: get challenge token
    const ch = await this._request({ operation: 'getchallenge', username: this.username });
    if (!ch.success) throw new Error('Challenge échoué: ' + (ch.error?.message || JSON.stringify(ch)));

    const token = ch.result.token;
    const key = md5(token + this.accessKey);

    // Step 2: login
    const login = await this._request({
      operation: 'login',
      username: this.username,
      accessKey: key
    }, 'POST');

    if (!login.success) throw new Error('Login échoué: ' + (login.error?.message || JSON.stringify(login)));

    this.sessionName = login.result.sessionName;
    this.userId = login.result.userId;
    return this;
  }

  async logout() {
    if (!this.sessionName) return;
    await this._request({ operation: 'logout', sessionName: this.sessionName }, 'POST');
    this.sessionName = null;
  }

  async query(soql) {
    const res = await this._request({
      operation: 'query',
      sessionName: this.sessionName,
      query: soql
    });
    if (!res.success) throw new Error(res.error?.message || 'Query échouée');
    return res.result;
  }

  async create(elementType, element) {
    const res = await this._request({
      operation: 'create',
      sessionName: this.sessionName,
      elementType,
      element: JSON.stringify(element)
    }, 'POST');
    if (!res.success) throw new Error(res.error?.message || 'Création échouée');
    return res.result;
  }

  async update(element) {
    const res = await this._request({
      operation: 'update',
      sessionName: this.sessionName,
      element: JSON.stringify(element)
    }, 'POST');
    if (!res.success) throw new Error(res.error?.message || 'Mise à jour échouée');
    return res.result;
  }

  async findContactByPhone(phone) {
    const clean = phone.replace(/\D/g, '');
    const variants = [clean, '+' + clean, phone].map(p => `'${p.replace(/'/g, "\\'")}'`);
    const q = `SELECT * FROM Contacts WHERE phone IN (${variants.join(',')}) OR mobile IN (${variants.join(',')}) LIMIT 1;`;
    try {
      const res = await this.query(q);
      return res.length ? res[0] : null;
    } catch {
      return null;
    }
  }

  // Map WhatsApp contact → vTiger Contacts fields
  static mapContact(wa) {
    const nameParts = (wa.name || '').trim().split(/\s+/);
    const firstname = nameParts[0] || wa.phone;
    const lastname = nameParts.slice(1).join(' ') || '';

    return {
      firstname,
      lastname,
      phone: wa.phone,
      mobile: wa.phone,
      description: [
        wa.isBusiness ? 'Business WhatsApp' : '',
        wa.lastSeen ? `Dernière activité: ${new Date(wa.lastSeen).toLocaleString('fr-FR')}` : '',
        `Source: ${wa.source || 'WhatsApp Web Extractor'}`
      ].filter(Boolean).join('\n'),
      leadsource: 'WhatsApp'
    };
  }
}

/* Pure-JS MD5 implementation (RFC 1321) */
function md5(str) {
  function safeAdd(x, y) {
    const lsw = (x & 0xffff) + (y & 0xffff);
    return ((((x >> 16) + (y >> 16) + (lsw >> 16)) << 16) | (lsw & 0xffff)) >>> 0;
  }
  function bitRotateLeft(num, cnt) { return ((num << cnt) | (num >>> (32 - cnt))) >>> 0; }
  function md5cmn(q, a, b, x, s, t) { return safeAdd(bitRotateLeft(safeAdd(safeAdd(a, q), safeAdd(x, t)), s), b); }
  function md5ff(a, b, c, d, x, s, t) { return md5cmn((b & c) | (~b & d), a, b, x, s, t); }
  function md5gg(a, b, c, d, x, s, t) { return md5cmn((b & d) | (c & ~d), a, b, x, s, t); }
  function md5hh(a, b, c, d, x, s, t) { return md5cmn(b ^ c ^ d, a, b, x, s, t); }
  function md5ii(a, b, c, d, x, s, t) { return md5cmn(c ^ (b | ~d), a, b, x, s, t); }

  const utf8 = unescape(encodeURIComponent(str));
  const len8 = utf8.length;
  const x = [];
  for (let i = 0; i < len8; i++) x[i >> 2] |= utf8.charCodeAt(i) << ((i % 4) * 8);
  x[len8 >> 2] |= 0x80 << ((len8 % 4) * 8);
  x[(((len8 + 8) >> 6) << 4) + 14] = len8 * 8;

  let a = 0x67452301, b = 0xefcdab89, c = 0x98badcfe, d = 0x10325476;

  for (let i = 0; i < x.length; i += 16) {
    const [oa, ob, oc, od] = [a, b, c, d];
    a = md5ff(a,b,c,d,x[i],7,-680876936); d=md5ff(d,a,b,c,x[i+1],12,-389564586); c=md5ff(c,d,a,b,x[i+2],17,606105819); b=md5ff(b,c,d,a,x[i+3],22,-1044525330);
    a=md5ff(a,b,c,d,x[i+4],7,-176418897); d=md5ff(d,a,b,c,x[i+5],12,1200080426); c=md5ff(c,d,a,b,x[i+6],17,-1473231341); b=md5ff(b,c,d,a,x[i+7],22,-45705983);
    a=md5ff(a,b,c,d,x[i+8],7,1770035416); d=md5ff(d,a,b,c,x[i+9],12,-1958414417); c=md5ff(c,d,a,b,x[i+10],17,-42063); b=md5ff(b,c,d,a,x[i+11],22,-1990404162);
    a=md5ff(a,b,c,d,x[i+12],7,1804603682); d=md5ff(d,a,b,c,x[i+13],12,-40341101); c=md5ff(c,d,a,b,x[i+14],17,-1502002290); b=md5ff(b,c,d,a,x[i+15],22,1236535329);
    a=md5gg(a,b,c,d,x[i+1],5,-165796510); d=md5gg(d,a,b,c,x[i+6],9,-1069501632); c=md5gg(c,d,a,b,x[i+11],14,643717713); b=md5gg(b,c,d,a,x[i],20,-373897302);
    a=md5gg(a,b,c,d,x[i+5],5,-701558691); d=md5gg(d,a,b,c,x[i+10],9,38016083); c=md5gg(c,d,a,b,x[i+15],14,-660478335); b=md5gg(b,c,d,a,x[i+4],20,-405537848);
    a=md5gg(a,b,c,d,x[i+9],5,568446438); d=md5gg(d,a,b,c,x[i+14],9,-1019803690); c=md5gg(c,d,a,b,x[i+3],14,-187363961); b=md5gg(b,c,d,a,x[i+8],20,1163531501);
    a=md5gg(a,b,c,d,x[i+13],5,-1444681467); d=md5gg(d,a,b,c,x[i+2],9,-51403784); c=md5gg(c,d,a,b,x[i+7],14,1735328473); b=md5gg(b,c,d,a,x[i+12],20,-1926607734);
    a=md5hh(a,b,c,d,x[i+5],4,-378558); d=md5hh(d,a,b,c,x[i+8],11,-2022574463); c=md5hh(c,d,a,b,x[i+11],16,1839030562); b=md5hh(b,c,d,a,x[i+14],23,-35309556);
    a=md5hh(a,b,c,d,x[i+1],4,-1530992060); d=md5hh(d,a,b,c,x[i+4],11,1272893353); c=md5hh(c,d,a,b,x[i+7],16,-155497632); b=md5hh(b,c,d,a,x[i+10],23,-1094730640);
    a=md5hh(a,b,c,d,x[i+13],4,681279174); d=md5hh(d,a,b,c,x[i],11,-358537222); c=md5hh(c,d,a,b,x[i+3],16,-722521979); b=md5hh(b,c,d,a,x[i+6],23,76029189);
    a=md5hh(a,b,c,d,x[i+9],4,-640364487); d=md5hh(d,a,b,c,x[i+12],11,-421815835); c=md5hh(c,d,a,b,x[i+15],16,530742520); b=md5hh(b,c,d,a,x[i+2],23,-995338651);
    a=md5ii(a,b,c,d,x[i],6,-198630844); d=md5ii(d,a,b,c,x[i+7],10,1126891415); c=md5ii(c,d,a,b,x[i+14],15,-1416354905); b=md5ii(b,c,d,a,x[i+5],21,-57434055);
    a=md5ii(a,b,c,d,x[i+12],6,1700485571); d=md5ii(d,a,b,c,x[i+3],10,-1894986606); c=md5ii(c,d,a,b,x[i+10],15,-1051523); b=md5ii(b,c,d,a,x[i+1],21,-2054922799);
    a=md5ii(a,b,c,d,x[i+8],6,1873313359); d=md5ii(d,a,b,c,x[i+15],10,-30611744); c=md5ii(c,d,a,b,x[i+6],15,-1560198380); b=md5ii(b,c,d,a,x[i+13],21,1309151649);
    a=md5ii(a,b,c,d,x[i+4],6,-145523070); d=md5ii(d,a,b,c,x[i+11],10,-1120210379); c=md5ii(c,d,a,b,x[i+2],15,718787259); b=md5ii(b,c,d,a,x[i+9],21,-343485551);
    a=safeAdd(a,oa); b=safeAdd(b,ob); c=safeAdd(c,oc); d=safeAdd(d,od);
  }

  return [a,b,c,d].map(n =>
    ('00000000' + (n >>> 0).toString(16)).slice(-8)
      .match(/../g).map(h => h[0]+h[1]).reverse().join('')
  ).join('');
}

'@

Set-File 'popup.html' @'
<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>WA → vTiger</title>
  <link rel="stylesheet" href="popup.css" />
</head>
<body>
  <!-- HEADER -->
  <div class="header">
    <div class="logo">
      <svg viewBox="0 0 24 24" fill="#25D366" width="22" height="22">
        <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/>
      </svg>
      <span>WA → vTiger</span>
    </div>
    <div class="header-right">
      <div id="status-badge" class="badge badge-idle">En attente</div>
      <button id="btn-settings" class="btn-icon-header" title="Paramètres vTiger">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor">
          <path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/>
        </svg>
      </button>
    </div>
  </div>

  <!-- TOOLBAR -->
  <div class="toolbar">
    <button id="btn-extract" class="btn btn-primary">
      <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>
      Extraire
    </button>
    <button id="btn-sync" class="btn btn-vtiger" disabled>
      <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><path d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46C19.54 15.03 20 13.57 20 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74C4.46 8.97 4 10.43 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z"/></svg>
      Sync vTiger
    </button>
    <button id="btn-export-csv" class="btn btn-secondary" disabled>CSV</button>
    <button id="btn-export-json" class="btn btn-secondary" disabled>JSON</button>
    <button id="btn-clear" class="btn btn-danger" disabled>
      <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
    </button>
  </div>

  <!-- SYNC PROGRESS -->
  <div id="sync-progress" class="sync-progress hidden">
    <div class="sync-bar-wrap"><div id="sync-bar" class="sync-bar" style="width:0%"></div></div>
    <span id="sync-label">Synchronisation…</span>
  </div>

  <!-- SYNC RESULT -->
  <div id="sync-result" class="sync-result hidden">
    <span id="sync-created" class="sr-item sr-green"></span>
    <span id="sync-updated" class="sr-item sr-blue"></span>
    <span id="sync-skipped" class="sr-item sr-gray"></span>
    <span id="sync-errors"  class="sr-item sr-red hidden"></span>
  </div>

  <!-- TABS -->
  <div id="tabs-bar" class="tabs-bar hidden">
    <button class="tab active" data-tab="contacts">
      Contacts <span id="tab-count-contacts" class="tab-count">0</span>
    </button>
    <button class="tab" data-tab="groups">
      Groupes <span id="tab-count-groups" class="tab-count">0</span>
    </button>
    <button class="tab" data-tab="conversations">
      Conversations <span id="tab-count-conversations" class="tab-count">0</span>
    </button>
  </div>

  <!-- SEARCH -->
  <div id="search-bar" class="search-bar hidden">
    <input type="text" id="search-input" placeholder="Rechercher…" />
  </div>

  <!-- LOADER -->
  <div id="loader" class="loader hidden">
    <div class="spinner"></div>
    <span id="loader-label">Extraction en cours…</span>
  </div>

  <!-- ERROR -->
  <div id="error-box" class="error-box hidden">
    <strong>Erreur :</strong> <span id="error-msg"></span>
    <div class="error-hint">
      Ouvrez <a href="https://web.whatsapp.com" target="_blank">web.whatsapp.com</a> et connectez-vous.
      Pour vTiger : <a id="link-settings" href="#">paramètres</a>.
    </div>
  </div>

  <!-- PANELS -->
  <div id="panel-contacts"     class="panel active"></div>
  <div id="panel-groups"       class="panel hidden"></div>
  <div id="panel-conversations"class="panel hidden"></div>

  <!-- EMPTY STATE -->
  <div id="empty-state" class="empty-state hidden">
    <svg viewBox="0 0 24 24" width="44" height="44" fill="#ccc"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>
    <p id="empty-msg">Aucun élément trouvé.</p>
  </div>

  <!-- FOOTER -->
  <div class="footer">
    <span id="footer-info">Connectez WhatsApp Web pour commencer</span>
    <span id="vtiger-status" class="vtiger-dot vtiger-nc" title="vTiger non configuré"></span>
  </div>

  <script src="vtiger.js"></script>
  <script src="popup.js"></script>
</body>
</html>

'@

Set-File 'popup.js' @'
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

'@

Set-File 'popup.css' @'
* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  width: 380px;
  min-height: 200px;
  max-height: 600px;
  display: flex;
  flex-direction: column;
  background: #f0f2f5;
  color: #111b21;
  font-size: 13px;
}

.header {
  background: #075e54;
  color: white;
  padding: 10px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}

.logo {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 15px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.btn-icon-header {
  background: rgba(255,255,255,0.15);
  border: none;
  border-radius: 50%;
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: white;
  transition: background 0.2s;
}
.btn-icon-header:hover { background: rgba(255,255,255,0.28); }

.badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 500;
}

.badge-idle { background: rgba(255,255,255,0.2); }
.badge-ok { background: #25D366; }
.badge-error { background: #f44336; }
.badge-loading { background: #ff9800; }

.toolbar {
  padding: 8px 10px;
  display: flex;
  gap: 6px;
  background: white;
  border-bottom: 1px solid #e9edef;
  flex-shrink: 0;
}

.btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 10px;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  transition: opacity 0.2s, background 0.2s;
}

.btn:disabled { opacity: 0.4; cursor: not-allowed; }

.btn-primary {
  background: #25D366;
  color: white;
  flex: 1;
}
.btn-primary:hover:not(:disabled) { background: #1da851; }

.btn-vtiger {
  background: #1e5fa8;
  color: white;
  flex: 1;
}
.btn-vtiger:hover:not(:disabled) { background: #174d8a; }

.btn-secondary {
  background: #e9edef;
  color: #3b4a54;
}
.btn-secondary:hover:not(:disabled) { background: #d1d7db; }

.btn-danger {
  background: #fde8e8;
  color: #c0392b;
}
.btn-danger:hover:not(:disabled) { background: #f5b7b1; }

.stats-bar {
  background: #dcf8c6;
  padding: 5px 14px;
  display: flex;
  gap: 12px;
  font-size: 11px;
  color: #075e54;
  font-weight: 500;
  flex-shrink: 0;
}

.search-bar {
  padding: 6px 10px;
  background: white;
  border-bottom: 1px solid #e9edef;
  flex-shrink: 0;
}

.search-bar input {
  width: 100%;
  padding: 6px 10px;
  border: 1px solid #d1d7db;
  border-radius: 20px;
  font-size: 12px;
  outline: none;
  background: #f0f2f5;
}

.search-bar input:focus { border-color: #25D366; background: white; }

.contact-list {
  flex: 1;
  overflow-y: auto;
  max-height: 340px;
}

.contact-item {
  display: flex;
  align-items: center;
  padding: 9px 14px;
  background: white;
  border-bottom: 1px solid #f0f2f5;
  gap: 10px;
  transition: background 0.15s;
}

.contact-item:hover { background: #f5f6f6; }

.contact-avatar {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  background: #25D366;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-weight: 600;
  font-size: 15px;
  flex-shrink: 0;
}

.contact-avatar.business { background: #075e54; }
.contact-avatar.group { background: #7b68ee; }

.contact-info { flex: 1; min-width: 0; }

.contact-name {
  font-weight: 600;
  font-size: 13px;
  color: #111b21;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.contact-phone {
  font-size: 12px;
  color: #667781;
  margin-top: 1px;
}

.contact-meta {
  display: flex;
  gap: 4px;
  margin-top: 2px;
  flex-wrap: wrap;
}

.tag {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 4px;
  font-weight: 500;
}

.tag-business { background: #e8f5e9; color: #2e7d32; }
.tag-muted    { background: #fce4ec; color: #c62828; }
.tag-unread   { background: #25D366; color: white; }
.tag-source   { background: #e3f2fd; color: #1565c0; }
.tag-group    { background: #ede7f6; color: #4527a0; }
.tag-admin    { background: #fff3e0; color: #e65100; }
.tag-pin      { background: #f5f5f5; color: #555; }
.tag-time     { background: #f5f5f5; color: #888; }

.contact-actions {
  display: flex;
  gap: 4px;
}

.action-btn {
  background: none;
  border: none;
  cursor: pointer;
  padding: 4px;
  border-radius: 50%;
  color: #667781;
  transition: background 0.15s, color 0.15s;
}

.action-btn:hover { background: #e9edef; color: #111b21; }

.loader {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 30px;
  gap: 12px;
  color: #667781;
}

.spinner {
  width: 28px;
  height: 28px;
  border: 3px solid #e9edef;
  border-top-color: #25D366;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 30px 20px;
  gap: 10px;
  color: #999;
  text-align: center;
  line-height: 1.5;
}

.error-box {
  margin: 10px;
  padding: 10px 12px;
  background: #fde8e8;
  border: 1px solid #f5b7b1;
  border-radius: 8px;
  color: #c0392b;
  font-size: 12px;
}

.error-hint { margin-top: 6px; color: #667781; font-size: 11px; }
.error-hint a { color: #25D366; }

/* ── Tabs ── */
.tabs-bar {
  display: flex;
  background: white;
  border-bottom: 2px solid #e9edef;
  flex-shrink: 0;
}

.tab {
  flex: 1;
  padding: 8px 4px;
  border: none;
  background: none;
  font-size: 12px;
  font-weight: 500;
  color: #667781;
  cursor: pointer;
  border-bottom: 3px solid transparent;
  margin-bottom: -2px;
  transition: color 0.2s, border-color 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
}
.tab:hover { color: #111b21; }
.tab.active { color: #075e54; border-bottom-color: #25D366; }

.tab-count {
  background: #e9edef;
  border-radius: 10px;
  padding: 0 5px;
  font-size: 10px;
  color: #667781;
}
.tab.active .tab-count { background: #dcf8c6; color: #1a5c2a; }

/* ── Panel ── */
.panel {
  flex: 1;
  overflow-y: auto;
  max-height: 300px;
}

/* ── Groupe card ── */
.group-card {
  background: white;
  border-bottom: 1px solid #f0f2f5;
}

.group-header {
  display: flex;
  align-items: flex-start;
  padding: 9px 14px;
  gap: 10px;
}

.group-meta { flex: 1; min-width: 0; }

.group-desc {
  font-size: 11px;
  color: #888;
  margin-top: 2px;
  font-style: italic;
}

.participants-list {
  padding: 0 14px 8px 52px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.participant {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
}

.p-name {
  font-weight: 500;
  color: #3b4a54;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.p-phone { color: #667781; }

.p-more {
  font-size: 11px;
  color: #25D366;
  font-weight: 500;
  margin-top: 2px;
}

/* ── Conversation row ── */
.conv-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
}

.conv-time {
  font-size: 11px;
  color: #667781;
  flex-shrink: 0;
}

.last-msg {
  font-size: 11px;
  color: #667781;
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 220px;
}

.last-msg-inline {
  font-size: 11px;
  color: #667781;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.unread-badge {
  background: #25D366;
  color: white;
  border-radius: 50%;
  min-width: 18px;
  height: 18px;
  font-size: 10px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  padding: 0 3px;
}

/* ── Avatar variants ── */
.contact-avatar.group   { background: #7b68ee; }
.contact-avatar.business{ background: #075e54; }

.hidden { display: none !important; }

.sync-progress {
  padding: 6px 14px;
  background: #e3f2fd;
  flex-shrink: 0;
}

.sync-bar-wrap {
  height: 4px;
  background: #c5e0ff;
  border-radius: 4px;
  margin-bottom: 4px;
  overflow: hidden;
}

.sync-bar {
  height: 100%;
  background: #1e5fa8;
  border-radius: 4px;
  transition: width 0.3s ease;
}

#sync-label { font-size: 11px; color: #1565c0; font-weight: 500; }

.sync-result {
  display: flex;
  gap: 8px;
  padding: 5px 14px;
  background: #f0f7ff;
  border-bottom: 1px solid #c5e0ff;
  flex-shrink: 0;
  flex-wrap: wrap;
}

.sr-item { font-size: 11px; font-weight: 600; padding: 2px 7px; border-radius: 10px; }
.sr-green { background: #dcf8c6; color: #1a5c2a; }
.sr-blue  { background: #e3f2fd; color: #1565c0; }
.sr-gray  { background: #f0f2f5; color: #667781; }
.sr-red   { background: #fde8e8; color: #c0392b; }

.footer {
  background: #ededed;
  padding: 5px 14px;
  font-size: 11px;
  color: #667781;
  flex-shrink: 0;
  border-top: 1px solid #d1d7db;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.vtiger-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
  flex-shrink: 0;
  cursor: help;
}
.vtiger-ok { background: #25D366; }
.vtiger-nc { background: #b0bec5; }

::-webkit-scrollbar { width: 4px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #d1d7db; border-radius: 4px; }

'@

Set-File 'options.html' @'
<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <title>Paramètres vTiger</title>
  <link rel="stylesheet" href="options.css" />
</head>
<body>
  <div class="page">
    <header>
      <div class="logo">
        <svg viewBox="0 0 24 24" fill="#075e54" width="28" height="28">
          <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/>
        </svg>
        <div>
          <h1>WhatsApp → vTiger</h1>
          <p class="subtitle">Configuration de la connexion CRM</p>
        </div>
      </div>
    </header>

    <section class="card">
      <h2>Connexion vTiger CRM</h2>

      <div class="form-group">
        <label for="vtiger-url">URL vTiger <span class="required">*</span></label>
        <input type="url" id="vtiger-url" placeholder="https://votre-crm.vtiger.com" />
        <small>URL de base de votre instance vTiger (sans /webservice.php)</small>
      </div>

      <div class="form-group">
        <label for="vtiger-user">Nom d'utilisateur <span class="required">*</span></label>
        <input type="text" id="vtiger-user" placeholder="admin" />
      </div>

      <div class="form-group">
        <label for="vtiger-key">Clé d'accès (Access Key) <span class="required">*</span></label>
        <div class="input-group">
          <input type="password" id="vtiger-key" placeholder="Votre clé API vTiger" />
          <button id="btn-toggle-key" class="btn-icon" title="Afficher/Masquer">
            <svg id="eye-icon" viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
              <path d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z"/>
            </svg>
          </button>
        </div>
        <small>Trouvez votre clé dans vTiger : <em>Mon profil → Clé d'accès</em></small>
      </div>

      <div class="form-actions">
        <button id="btn-test" class="btn btn-secondary">Tester la connexion</button>
        <button id="btn-save" class="btn btn-primary">Enregistrer</button>
      </div>

      <div id="test-result" class="alert hidden"></div>
    </section>

    <section class="card">
      <h2>Options de synchronisation</h2>

      <div class="form-group">
        <label>Mode de gestion des doublons</label>
        <div class="radio-group">
          <label class="radio-label">
            <input type="radio" name="duplicate" value="skip" checked />
            <span>Ignorer si le numéro existe déjà</span>
          </label>
          <label class="radio-label">
            <input type="radio" name="duplicate" value="update" />
            <span>Mettre à jour le contact existant</span>
          </label>
          <label class="radio-label">
            <input type="radio" name="duplicate" value="create" />
            <span>Toujours créer (sans vérification)</span>
          </label>
        </div>
      </div>

      <div class="form-group">
        <label>Module cible vTiger</label>
        <select id="vtiger-module">
          <option value="Contacts">Contacts</option>
          <option value="Leads">Prospects (Leads)</option>
        </select>
      </div>

      <div class="form-group checkbox-group">
        <label class="checkbox-label">
          <input type="checkbox" id="chk-business-tag" checked />
          <span>Taguer les comptes Business WhatsApp</span>
        </label>
        <label class="checkbox-label">
          <input type="checkbox" id="chk-wa-source" checked />
          <span>Renseigner "Source = WhatsApp" dans vTiger</span>
        </label>
        <label class="checkbox-label">
          <input type="checkbox" id="chk-only-named" />
          <span>Synchroniser uniquement les contacts avec un nom</span>
        </label>
      </div>

      <div class="form-actions">
        <button id="btn-save-opts" class="btn btn-primary">Enregistrer les options</button>
      </div>
    </section>

    <section class="card card-info">
      <h2>Comment obtenir votre clé d'accès vTiger ?</h2>
      <ol>
        <li>Connectez-vous à votre vTiger CRM</li>
        <li>Cliquez sur votre nom en haut à droite → <strong>Mon profil</strong></li>
        <li>Dans l'onglet <strong>Avancé</strong>, copiez la valeur <strong>Clé d'accès</strong></li>
        <li>Collez-la dans le champ ci-dessus</li>
      </ol>
    </section>
  </div>

  <script src="options.js"></script>
</body>
</html>

'@

Set-File 'options.js' @'
/* Options page - vTiger config */

const $ = id => document.getElementById(id);

const KEYS = ['vtiger-url', 'vtiger-user', 'vtiger-key', 'vtiger-module',
              'chk-business-tag', 'chk-wa-source', 'chk-only-named'];

function loadSettings() {
  chrome.storage.local.get(['vtigerConfig', 'vtigerOpts'], ({ vtigerConfig = {}, vtigerOpts = {} }) => {
    if (vtigerConfig.url)  $('vtiger-url').value  = vtigerConfig.url;
    if (vtigerConfig.user) $('vtiger-user').value = vtigerConfig.user;
    if (vtigerConfig.key)  $('vtiger-key').value  = vtigerConfig.key;

    if (vtigerOpts.module) $('vtiger-module').value = vtigerOpts.module;
    if (vtigerOpts.duplicate) {
      document.querySelector(`input[name="duplicate"][value="${vtigerOpts.duplicate}"]`).checked = true;
    }
    $('chk-business-tag').checked = vtigerOpts.businessTag !== false;
    $('chk-wa-source').checked    = vtigerOpts.waSource    !== false;
    $('chk-only-named').checked   = !!vtigerOpts.onlyNamed;
  });
}

function saveConfig() {
  const config = {
    url:  $('vtiger-url').value.trim(),
    user: $('vtiger-user').value.trim(),
    key:  $('vtiger-key').value.trim()
  };
  if (!config.url || !config.user || !config.key) {
    alert('Veuillez remplir tous les champs obligatoires.');
    return;
  }
  chrome.storage.local.set({ vtigerConfig: config }, () => {
    showAlert('test-result', 'ok', 'Configuration enregistrée.');
  });
}

function saveOptions() {
  const opts = {
    module:      $('vtiger-module').value,
    duplicate:   document.querySelector('input[name="duplicate"]:checked').value,
    businessTag: $('chk-business-tag').checked,
    waSource:    $('chk-wa-source').checked,
    onlyNamed:   $('chk-only-named').checked
  };
  chrome.storage.local.set({ vtigerOpts: opts }, () => {
    showAlert('test-result', 'ok', 'Options enregistrées.');
  });
}

function showAlert(id, type, msg) {
  const el = $(id);
  el.className = `alert alert-${type}`;
  el.textContent = msg;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 4000);
}

async function testConnection() {
  const btn = $('btn-test');
  btn.disabled = true;
  btn.textContent = 'Connexion…';

  const config = {
    url:  $('vtiger-url').value.trim(),
    user: $('vtiger-user').value.trim(),
    key:  $('vtiger-key').value.trim()
  };

  try {
    // Send test to background script which has no CORS restriction
    const res = await chrome.runtime.sendMessage({ action: 'vtigerTest', config });
    if (res.success) {
      showAlert('test-result', 'ok', `Connexion réussie — utilisateur: ${res.user}, session: ${res.session.slice(0, 12)}…`);
    } else {
      showAlert('test-result', 'error', 'Échec: ' + res.error);
    }
  } catch (e) {
    showAlert('test-result', 'error', e.message);
  }

  btn.disabled = false;
  btn.textContent = 'Tester la connexion';
}

$('btn-toggle-key').addEventListener('click', () => {
  const input = $('vtiger-key');
  input.type = input.type === 'password' ? 'text' : 'password';
});

$('btn-test').addEventListener('click', testConnection);
$('btn-save').addEventListener('click', saveConfig);
$('btn-save-opts').addEventListener('click', saveOptions);

loadSettings();

'@

Set-File 'options.css' @'
* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #f0f2f5;
  color: #111b21;
  font-size: 14px;
  padding: 20px;
}

.page { max-width: 600px; margin: 0 auto; display: flex; flex-direction: column; gap: 16px; }

header {
  background: #075e54;
  border-radius: 12px;
  padding: 16px 20px;
  color: white;
}

.logo { display: flex; align-items: center; gap: 14px; }
.logo h1 { font-size: 18px; font-weight: 700; }
.logo .subtitle { font-size: 12px; opacity: 0.8; margin-top: 2px; }

.card {
  background: white;
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}

.card h2 {
  font-size: 14px;
  font-weight: 600;
  color: #075e54;
  margin-bottom: 16px;
  padding-bottom: 8px;
  border-bottom: 2px solid #e9edef;
}

.card-info { background: #f0f7ff; border: 1px solid #c5e0ff; }
.card-info h2 { color: #1565c0; border-color: #c5e0ff; }
.card-info ol { padding-left: 18px; line-height: 2; color: #444; font-size: 13px; }

.form-group { margin-bottom: 16px; }

.form-group label {
  display: block;
  font-weight: 500;
  font-size: 13px;
  margin-bottom: 5px;
  color: #3b4a54;
}

.required { color: #e53935; }

.form-group input[type="url"],
.form-group input[type="text"],
.form-group input[type="password"],
.form-group select {
  width: 100%;
  padding: 9px 12px;
  border: 1.5px solid #d1d7db;
  border-radius: 8px;
  font-size: 13px;
  outline: none;
  transition: border-color 0.2s;
  background: white;
}

.form-group input:focus,
.form-group select:focus { border-color: #25D366; }

.form-group small { display: block; margin-top: 4px; font-size: 11px; color: #667781; }
.form-group small em { font-style: normal; font-weight: 500; }

.input-group { display: flex; gap: 6px; }
.input-group input { flex: 1; }

.btn-icon {
  background: #f0f2f5;
  border: 1.5px solid #d1d7db;
  border-radius: 8px;
  padding: 0 10px;
  cursor: pointer;
  color: #667781;
  display: flex;
  align-items: center;
}
.btn-icon:hover { background: #e9edef; color: #111b21; }

.radio-group, .checkbox-group { display: flex; flex-direction: column; gap: 8px; }

.radio-label, .checkbox-label {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  font-size: 13px;
  color: #3b4a54;
}

.radio-label input, .checkbox-label input { accent-color: #25D366; cursor: pointer; }

.form-actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  margin-top: 4px;
}

.btn {
  padding: 9px 18px;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  transition: opacity 0.2s, background 0.2s;
}
.btn:disabled { opacity: 0.4; cursor: not-allowed; }

.btn-primary { background: #25D366; color: white; }
.btn-primary:hover:not(:disabled) { background: #1da851; }
.btn-secondary { background: #e9edef; color: #3b4a54; }
.btn-secondary:hover:not(:disabled) { background: #d1d7db; }

.alert {
  margin-top: 12px;
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
}
.alert-ok { background: #dcf8c6; color: #1a5c2a; border: 1px solid #a8d8a8; }
.alert-error { background: #fde8e8; color: #c0392b; border: 1px solid #f5b7b1; }

.hidden { display: none !important; }

'@

Set-File '.vscode\launch.json' @'
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "🚀 Lancer Chrome + Extension",
      "type": "chrome",
      "request": "launch",
      "url": "https://web.whatsapp.com",
      "runtimeExecutable": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
      "runtimeArgs": [
        "--load-extension=${workspaceFolder}",
        "--disable-extensions-except=${workspaceFolder}",
        "--remote-debugging-port=9222",
        "--no-first-run",
        "--no-default-browser-check"
      ],
      "userDataDir": "${workspaceFolder}\\.chrome-profile",
      "sourceMaps": true,
      "webRoot": "${workspaceFolder}"
    },
    {
      "name": "🔧 Debug Content Script (attach)",
      "type": "chrome",
      "request": "attach",
      "port": 9222,
      "urlFilter": "https://web.whatsapp.com/*",
      "sourceMaps": true,
      "webRoot": "${workspaceFolder}"
    },
    {
      "name": "🔧 Debug Service Worker (attach)",
      "type": "chrome",
      "request": "attach",
      "port": 9222,
      "urlFilter": "chrome-extension://*/background.js",
      "sourceMaps": true,
      "webRoot": "${workspaceFolder}"
    },
    {
      "name": "🚀 Chrome (Edge - alternative)",
      "type": "chrome",
      "request": "launch",
      "url": "https://web.whatsapp.com",
      "runtimeExecutable": "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
      "runtimeArgs": [
        "--load-extension=${workspaceFolder}",
        "--disable-extensions-except=${workspaceFolder}",
        "--remote-debugging-port=9222",
        "--no-first-run"
      ],
      "userDataDir": "${workspaceFolder}\\.edge-profile",
      "sourceMaps": true,
      "webRoot": "${workspaceFolder}"
    }
  ]
}

'@

Set-File '.vscode\settings.json' @'
{
  // ── Éditeur ──
  "editor.tabSize": 2,
  "editor.insertSpaces": true,
  "editor.formatOnSave": true,
  "editor.defaultFormatter": "esbenp.prettier-vscode",
  "editor.rulers": [100],
  "editor.wordWrap": "off",
  "editor.bracketPairColorization.enabled": true,
  "editor.guides.bracketPairs": true,
  "editor.suggest.snippetsPreventQuickSuggestions": false,

  // ── Fichiers ──
  "files.eol": "\n",
  "files.trimTrailingWhitespace": true,
  "files.insertFinalNewline": true,
  "files.exclude": {
    ".chrome-profile": true,
    "**/.DS_Store": true
  },

  // ── JS / Chrome Extension ──
  "javascript.validate.enable": false,
  "javascript.suggest.autoImports": false,
  "[javascript]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  },
  "[json]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  },
  "[html]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  },
  "[css]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  },

  // ── Prettier ──
  "prettier.singleQuote": true,
  "prettier.semi": true,
  "prettier.trailingComma": "es5",
  "prettier.printWidth": 100,

  // ── ESLint ──
  "eslint.validate": ["javascript"],
  "eslint.run": "onSave",

  // ── Coloration manifest.json / _locales ──
  "files.associations": {
    "manifest.json": "jsonc"
  },

  // ── IntelliSense Chrome Extension APIs ──
  "javascript.preferences.quoteStyle": "single",

  // ── Terminal ──
  "terminal.integrated.defaultProfile.linux": "bash",

  // ── Explorer ──
  "explorer.fileNesting.enabled": true,
  "explorer.fileNesting.patterns": {
    "popup.html": "popup.js, popup.css",
    "options.html": "options.js, options.css",
    "manifest.json": "background.js, content.js, vtiger.js",
    "*.html": "${capture}.js, ${capture}.css"
  }
}

'@

Set-File '.vscode\tasks.json' @'
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "📦 Packager l'extension (ZIP)",
      "type": "shell",
      "command": "Compress-Archive",
      "args": [
        "-Path", "${workspaceFolder}\\*",
        "-DestinationPath", "${workspaceFolder}\\..\\whatsapp-vtiger-extension.zip",
        "-Force"
      ],
      "windows": {
        "command": "powershell",
        "args": [
          "-Command",
          "Compress-Archive -Path '${workspaceFolder}\\*' -DestinationPath '${workspaceFolder}\\..\\whatsapp-vtiger-extension.zip' -Force; Write-Host '✅ ZIP créé'"
        ]
      },
      "group": { "kind": "build", "isDefault": true },
      "presentation": { "reveal": "always", "panel": "shared" },
      "problemMatcher": []
    },
    {
      "label": "🧹 Nettoyer profil Chrome de test",
      "type": "shell",
      "windows": {
        "command": "powershell",
        "args": [
          "-Command",
          "Remove-Item -Recurse -Force '${workspaceFolder}\\.chrome-profile' -ErrorAction SilentlyContinue; Write-Host '✅ Profil supprimé'"
        ]
      },
      "group": "none",
      "presentation": { "reveal": "always", "panel": "shared" },
      "problemMatcher": []
    },
    {
      "label": "🌐 Ouvrir Chrome avec Extension",
      "type": "shell",
      "windows": {
        "command": "powershell",
        "args": [
          "-Command",
          "Start-Process 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' -ArgumentList '--load-extension=${workspaceFolder}','--disable-extensions-except=${workspaceFolder}','--remote-debugging-port=9222','--user-data-dir=${workspaceFolder}\\.chrome-profile','https://web.whatsapp.com'"
        ]
      },
      "isBackground": true,
      "group": "none",
      "presentation": { "reveal": "silent", "panel": "shared" },
      "problemMatcher": []
    },
    {
      "label": "🔍 Valider manifest.json",
      "type": "shell",
      "windows": {
        "command": "powershell",
        "args": [
          "-Command",
          "try { $m=Get-Content '${workspaceFolder}\\manifest.json' | ConvertFrom-Json; Write-Host '✅ manifest.json valide -' $m.name 'v' $m.version } catch { Write-Error '❌ Erreur manifest.json'; exit 1 }"
        ]
      },
      "group": "test",
      "presentation": { "reveal": "always", "panel": "shared" },
      "problemMatcher": []
    }
  ]
}

'@

Set-File '.vscode\extensions.json' @'
{
  "recommendations": [
    // ── Formatage & Linting ──
    "esbenp.prettier-vscode",
    "dbaeumer.vscode-eslint",

    // ── Chrome Extension Development ──
    "formulahendry.auto-close-tag",
    "formulahendry.auto-rename-tag",
    "ms-vscode.js-debug",

    // ── IntelliSense ──
    "christian-kohler.path-intellisense",
    "visualstudioexptteam.vscodeintellicode",

    // ── Git ──
    "eamodio.gitlens",
    "mhutchie.git-graph",

    // ── HTML / CSS ──
    "ecmel.vscode-html-css",
    "stylelint.vscode-stylelint",
    "zignd.html-css-class-completion",

    // ── JSON / Manifest ──
    "zainchen.json",
    "nickdemayo.vscode-json-editor",

    // ── UI ──
    "PKief.material-icon-theme",
    "GitHub.github-vscode-theme",

    // ── Productivité ──
    "streetsidesoftware.code-spell-checker",
    "streetsidesoftware.code-spell-checker-french",
    "aaron-bond.better-comments",
    "oderwat.indent-rainbow"
  ]
}

'@

Set-File '.vscode\snippets.code-snippets' @'
{
  "Chrome Message Listener": {
    "prefix": "wa-listener",
    "body": [
      "chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {",
      "  if (msg.action === '${1:action}') {",
      "    ${2:// traitement}",
      "    sendResponse({ success: true, data: ${3:null} });",
      "    return true;",
      "  }",
      "});"
    ],
    "description": "Listener de message Chrome Extension"
  },

  "Send Message to Tab": {
    "prefix": "wa-send-tab",
    "body": [
      "chrome.tabs.query({ url: 'https://web.whatsapp.com/*' }, tabs => {",
      "  if (!tabs[0]) return;",
      "  chrome.tabs.sendMessage(tabs[0].id, { action: '${1:action}' }, res => {",
      "    ${2:console.log(res);}",
      "  });",
      "});"
    ],
    "description": "Envoyer un message à l'onglet WhatsApp Web"
  },

  "Chrome Storage Get": {
    "prefix": "wa-storage-get",
    "body": [
      "chrome.storage.local.get(['${1:key}'], ({ ${1:key} }) => {",
      "  ${2:console.log(${1:key});}",
      "});"
    ],
    "description": "Lire depuis chrome.storage.local"
  },

  "Chrome Storage Set": {
    "prefix": "wa-storage-set",
    "body": [
      "chrome.storage.local.set({ ${1:key}: ${2:value} }, () => {",
      "  ${3:// callback}",
      "});"
    ],
    "description": "Écrire dans chrome.storage.local"
  },

  "Inject Page Script": {
    "prefix": "wa-inject",
    "body": [
      "function injectScript(code) {",
      "  return new Promise(resolve => {",
      "    const script = document.createElement('script');",
      "    script.textContent = `(function() { ${1:// code injecté dans la page} })();`;",
      "    document.head.appendChild(script);",
      "    script.remove();",
      "    const handler = e => {",
      "      document.removeEventListener('${2:event-name}', handler);",
      "      resolve(e.detail);",
      "    };",
      "    document.addEventListener('${2:event-name}', handler);",
      "    setTimeout(() => { document.removeEventListener('${2:event-name}', handler); resolve(null); }, 5000);",
      "  });",
      "}"
    ],
    "description": "Injecter un script dans la page WhatsApp Web"
  },

  "vTiger Client Usage": {
    "prefix": "wa-vtiger",
    "body": [
      "const client = new VtigerClient({ url: '${1:url}', username: '${2:user}', accessKey: '${3:key}' });",
      "await client.login();",
      "try {",
      "  const existing = await client.findContactByPhone('${4:+33600000000}');",
      "  if (!existing) {",
      "    await client.create('Contacts', VtigerClient.mapContact(${5:waContact}));",
      "  }",
      "} finally {",
      "  await client.logout();",
      "}"
    ],
    "description": "Utiliser le client vTiger CRM"
  }
}

'@

Set-File '.eslintrc.json' @'
{
  "env": {
    "browser": true,
    "es2022": true,
    "webextensions": true
  },
  "globals": {
    "md5": "readonly",
    "VtigerClient": "readonly"
  },
  "parserOptions": {
    "ecmaVersion": 2022,
    "sourceType": "script"
  },
  "rules": {
    "no-unused-vars": ["warn", { "argsIgnorePattern": "^_" }],
    "no-console": "off",
    "eqeqeq": ["error", "always"],
    "no-var": "error",
    "prefer-const": "warn",
    "no-eval": "error",
    "no-implied-eval": "error"
  }
}

'@

Set-File '.prettierrc' @'
{
  "singleQuote": true,
  "semi": true,
  "trailingComma": "es5",
  "printWidth": 100,
  "tabWidth": 2,
  "bracketSpacing": true,
  "arrowParens": "avoid",
  "htmlWhitespaceSensitivity": "css"
}

'@

Set-File '.gitignore' @'
# Profil Chrome de test (généré par VS Code launch)
.chrome-profile/

# Archives de packaging
*.zip
*.crx
*.pem

# OS
.DS_Store
Thumbs.db

# Éditeurs
*.swp
*.swo
.idea/

'@

Set-File 'whatsapp-vtiger.code-workspace' @'
{
  "folders": [
    {
      "name": "WhatsApp → vTiger Extension",
      "path": "."
    }
  ],
  "settings": {
    "editor.tabSize": 2,
    "editor.formatOnSave": true,
    "editor.defaultFormatter": "esbenp.prettier-vscode",
    "editor.bracketPairColorization.enabled": true,
    "explorer.fileNesting.enabled": true,
    "explorer.fileNesting.patterns": {
      "popup.html":   "popup.js, popup.css",
      "options.html": "options.js, options.css",
      "manifest.json":"background.js, content.js, vtiger.js, .eslintrc.json, .prettierrc, .gitignore"
    },
    "files.exclude": {
      ".chrome-profile": true
    },
    "workbench.iconTheme": "material-icon-theme",
    "workbench.colorTheme": "GitHub Dark"
  },
  "extensions": {
    "recommendations": [
      "esbenp.prettier-vscode",
      "dbaeumer.vscode-eslint",
      "eamodio.gitlens",
      "PKief.material-icon-theme",
      "GitHub.github-vscode-theme",
      "ms-vscode.js-debug",
      "christian-kohler.path-intellisense",
      "aaron-bond.better-comments"
    ]
  },
  "launch": {
    "version": "0.2.0",
    "configurations": [
      {
        "name": "🚀 WhatsApp Web + Extension",
        "type": "chrome",
        "request": "launch",
        "url": "https://web.whatsapp.com",
        "runtimeArgs": [
          "--load-extension=${workspaceFolder}",
          "--disable-extensions-except=${workspaceFolder}",
          "--remote-debugging-port=9222",
          "--no-first-run"
        ],
        "userDataDir": "${workspaceFolder}/.chrome-profile",
        "webRoot": "${workspaceFolder}"
      }
    ]
  },
  "tasks": {
    "version": "2.0.0",
    "tasks": [
      {
        "label": "📦 Packager ZIP",
        "type": "shell",
        "command": "zip -r ../whatsapp-vtiger-extension.zip . -x '.vscode/*' -x '.chrome-profile/*' -x '*.zip'",
        "group": { "kind": "build", "isDefault": true },
        "presentation": { "reveal": "always" },
        "problemMatcher": []
      }
    ]
  }
}

'@

# -- Generer les icones PNG ------------------------------------
Write-Host ""
Write-Host "Generation des icones..." -ForegroundColor Yellow
Add-Type -AssemblyName System.Drawing
foreach ($sz in @(16, 48, 128)) {
    $iconPath = Join-Path $TargetDir "icons\icon$sz.png"
    try {
        $bmp   = New-Object System.Drawing.Bitmap($sz, $sz)
        $gfx   = [System.Drawing.Graphics]::FromImage($bmp)
        $gfx.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $gfx.Clear([System.Drawing.Color]::Transparent)
        $brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(0x25, 0xD3, 0x66))
        $gfx.FillEllipse($brush, 1, 1, $sz-2, $sz-2)
        # Ajouter un W blanc au centre
        $font  = New-Object System.Drawing.Font("Arial", [Math]::Max(6, $sz/3), [System.Drawing.FontStyle]::Bold)
        $sf    = New-Object System.Drawing.StringFormat
        $sf.Alignment = [System.Drawing.StringAlignment]::Center
        $sf.LineAlignment = [System.Drawing.StringAlignment]::Center
        $wb    = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
        $gfx.DrawString("W", $font, $wb, [System.Drawing.RectangleF]::new(0,0,$sz,$sz), $sf)
        $brush.Dispose(); $wb.Dispose(); $font.Dispose(); $gfx.Dispose()
        $bmp.Save($iconPath, [System.Drawing.Imaging.ImageFormat]::Png)
        $bmp.Dispose()
        Write-Host "  [OK] icons/icon$sz.png" -ForegroundColor Green
    } catch {
        Write-Host "  [SKIP] icons/icon$sz.png" -ForegroundColor DarkYellow
    }
}

# -- Detecter Chrome -------------------------------------------
Write-Host ""
Write-Host "Detection de Chrome..." -ForegroundColor Yellow
$chromePaths = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $chromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
$launchFile = Join-Path $TargetDir ".vscode\launch.json"
if ($chrome -and (Test-Path $launchFile)) {
    $esc  = $chrome.Replace('\','\\\\')
    $json = [System.IO.File]::ReadAllText($launchFile)
    $json = $json -replace 'C:\\\\Program Files\\\\Google\\\\Chrome\\\\Application\\\\chrome\\.exe', $esc
    [System.IO.File]::WriteAllText($launchFile, $json, [System.Text.UTF8Encoding]::new($false))
    Write-Host "  [OK] Chrome detecte : $chrome" -ForegroundColor Green
} else {
    Write-Host "  [INFO] Chrome non detecte - editez .vscode\launch.json" -ForegroundColor DarkYellow
}

# -- Résumé final ----------------------------------------------
Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  Projet cree avec succes !" -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Dossier : $TargetDir" -ForegroundColor White
Write-Host ""
Write-Host "  Etapes suivantes :" -ForegroundColor Yellow
Write-Host "  1. Ouvrir VS Code :" -ForegroundColor White
Write-Host "     code `"$TargetDir\whatsapp-vtiger.code-workspace`"" -ForegroundColor Cyan
Write-Host ""
Write-Host "  2. Charger dans Chrome :" -ForegroundColor White
Write-Host "     chrome://extensions -> Mode developpeur -> Charger extension" -ForegroundColor Cyan
Write-Host "     Selectionner : $TargetDir" -ForegroundColor Cyan
Write-Host ""
Write-Host "  3. Configurer vTiger :" -ForegroundColor White
Write-Host "     Cliquer sur l icone engrenage dans le popup de l extension" -ForegroundColor Cyan
Write-Host ""

$code = Get-Command "code" -ErrorAction SilentlyContinue
if ($code) {
    $rep = Read-Host "Ouvrir VS Code maintenant ? [O/n]"
    if ($rep -eq '' -or $rep -match '^[Oo]') {
        $ws = Join-Path $TargetDir "whatsapp-vtiger.code-workspace"
        Start-Process "code" -ArgumentList "`"$ws`""
        Write-Host "  VS Code ouvert !" -ForegroundColor Green
    }
} else {
    Write-Host "  (VS Code non detecte dans le PATH)" -ForegroundColor DarkYellow
}
Write-Host ""
