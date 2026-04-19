/* WhatsApp Web Contact Extractor - Content Script */

const WA_STORE_TIMEOUT = 10000;

function waitForStore(timeout = WA_STORE_TIMEOUT) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const check = () => {
      if (window.Store && window.Store.Chat) return resolve(window.Store);
      if (Date.now() - start > timeout) return reject(new Error("Store non disponible"));
      setTimeout(check, 500);
    };
    check();
  });
}

function injectStoreExtractor() {
  return new Promise((resolve) => {
    const script = document.createElement("script");
    script.id = "__wa_extractor__";
    script.textContent = `
      (function() {
        const MAX_WAIT = 15000;
        const start = Date.now();

        function findStore() {
          try {
            // Méthode 1: window.Store direct
            if (window.Store && window.Store.Chat) return window.Store;

            // Méthode 2: via les modules webpack
            const keys = Object.keys(window).filter(k =>
              window[k] && typeof window[k] === 'object' &&
              window[k].Chat && window[k].Contact
            );
            if (keys.length) return window[keys[0]];

            // Méthode 3: via require (WhatsApp Web module system)
            if (typeof require !== 'undefined') {
              try {
                const storeModule = require('WAWebChatStore');
                if (storeModule) return storeModule;
              } catch(e) {}
              try {
                const chatMod = require('WAWebContactStore');
                if (chatMod) return chatMod;
              } catch(e) {}
            }
          } catch(e) {}
          return null;
        }

        function extractViaStore(store) {
          const contacts = [];
          try {
            if (store.Contact && store.Contact.models) {
              store.Contact.models.forEach(c => {
                const phone = c.id && c.id.user ? c.id.user : null;
                if (phone && !phone.includes('-') && /^\\d+$/.test(phone)) {
                  contacts.push({
                    phone: '+' + phone,
                    name: c.name || c.pushname || c.notify || '',
                    shortName: c.shortName || '',
                    isBusiness: c.isBusiness || false,
                    isWAContact: c.isWAContact !== false,
                    statusMute: c.statusMute || false,
                    source: 'Store.Contact'
                  });
                }
              });
            }
          } catch(e) {}

          try {
            if (store.Chat && store.Chat.models) {
              store.Chat.models.forEach(chat => {
                const jid = chat.id && chat.id._serialized ? chat.id._serialized : '';
                const phone = chat.id && chat.id.user ? chat.id.user : '';
                if (phone && !phone.includes('-') && /^\\d+$/.test(phone)) {
                  const existing = contacts.find(c => c.phone === '+' + phone);
                  if (!existing) {
                    contacts.push({
                      phone: '+' + phone,
                      name: chat.name || chat.formattedTitle || '',
                      lastSeen: chat.t ? new Date(chat.t * 1000).toISOString() : null,
                      unreadCount: chat.unreadCount || 0,
                      isMuted: chat.mute > 0,
                      isGroup: chat.isGroup || false,
                      source: 'Store.Chat'
                    });
                  } else {
                    existing.lastSeen = chat.t ? new Date(chat.t * 1000).toISOString() : null;
                    existing.unreadCount = chat.unreadCount || 0;
                    existing.isMuted = chat.mute > 0;
                  }
                }
              });
            }
          } catch(e) {}

          return contacts;
        }

        function extractViaDOM() {
          const contacts = [];
          const phoneRegex = /\\+?[1-9]\\d{6,14}/g;
          const seen = new Set();

          // Panneau de chat list
          document.querySelectorAll('[data-testid="cell-frame-container"]').forEach(el => {
            const titleEl = el.querySelector('[data-testid="cell-frame-title"]');
            const name = titleEl ? titleEl.textContent.trim() : '';
            const dataId = el.getAttribute('data-id') || '';
            const matches = dataId.match(phoneRegex) || [];
            matches.forEach(phone => {
              if (!seen.has(phone)) {
                seen.add(phone);
                contacts.push({ phone, name, source: 'DOM-chatlist' });
              }
            });
          });

          // Contact info panel
          document.querySelectorAll('[data-testid="contact-info-drawer"]').forEach(panel => {
            const text = panel.innerText;
            const phones = text.match(phoneRegex) || [];
            const nameEl = panel.querySelector('h2, [data-testid="contact-name"]');
            const name = nameEl ? nameEl.textContent.trim() : '';
            phones.forEach(phone => {
              if (!seen.has(phone)) {
                seen.add(phone);
                contacts.push({ phone, name, source: 'DOM-info-panel' });
              }
            });
          });

          // Scan global DOM pour numéros
          const allText = document.body.innerText;
          const allPhones = allText.match(/\\+\\d{8,15}/g) || [];
          allPhones.forEach(phone => {
            if (!seen.has(phone)) {
              seen.add(phone);
              contacts.push({ phone, name: '', source: 'DOM-scan' });
            }
          });

          return contacts;
        }

        function tryExtract() {
          const store = findStore();
          let contacts = [];

          if (store) {
            contacts = extractViaStore(store);
          }

          const domContacts = extractViaDOM();
          domContacts.forEach(dc => {
            if (!contacts.find(c => c.phone === dc.phone)) {
              contacts.push(dc);
            }
          });

          window.__WA_EXTRACTED__ = {
            contacts,
            timestamp: new Date().toISOString(),
            storeFound: !!store,
            total: contacts.length
          };

          document.dispatchEvent(new CustomEvent('__wa_data_ready__', {
            detail: window.__WA_EXTRACTED__
          }));
        }

        if (Date.now() - start < MAX_WAIT) {
          tryExtract();
        }
      })();
    `;
    document.head.appendChild(script);
    script.remove();

    const handler = (e) => {
      document.removeEventListener("__wa_data_ready__", handler);
      resolve(e.detail);
    };
    document.addEventListener("__wa_data_ready__", handler);

    setTimeout(() => {
      document.removeEventListener("__wa_data_ready__", handler);
      resolve(window.__WA_EXTRACTED__ || { contacts: [], storeFound: false, total: 0 });
    }, 5000);
  });
}

function extractFromCurrentChat() {
  const phoneRegex = /\+?[1-9]\d{6,14}/g;
  const result = { phone: null, name: null, about: null };

  try {
    const header = document.querySelector('[data-testid="conversation-header"]');
    if (header) {
      const name = header.querySelector('[data-testid="conversation-info-header-chat-title"]');
      if (name) result.name = name.textContent.trim();
    }

    const infoPanel = document.querySelector('[data-testid="contact-info-drawer"]');
    if (infoPanel) {
      const text = infoPanel.innerText;
      const phones = text.match(phoneRegex);
      if (phones) result.phone = phones[0];

      const aboutEl = infoPanel.querySelector('[data-testid="contact-info-about"]');
      if (aboutEl) result.about = aboutEl.textContent.trim();
    }
  } catch (e) {}

  return result;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === "extract") {
    injectStoreExtractor().then(data => {
      const currentChat = extractFromCurrentChat();
      sendResponse({ success: true, data, currentChat });
    }).catch(err => {
      sendResponse({ success: false, error: err.message });
    });
    return true;
  }

  if (msg.action === "ping") {
    sendResponse({ alive: true, url: window.location.href });
    return true;
  }

  if (msg.action === "openContact") {
    const phone = msg.phone.replace(/\D/g, "");
    window.open(`https://web.whatsapp.com/send?phone=${phone}`, "_self");
    sendResponse({ done: true });
    return true;
  }
});
