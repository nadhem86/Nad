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
