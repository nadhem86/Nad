/* WhatsApp Extractor - Service Worker */

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.clear();
});

// Re-inject content script if tab is refreshed
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (
    changeInfo.status === 'complete' &&
    tab.url &&
    tab.url.startsWith('https://web.whatsapp.com')
  ) {
    chrome.scripting.executeScript({
      target: { tabId },
      files: ['content.js']
    }).catch(() => {});
  }
});
