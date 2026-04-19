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
