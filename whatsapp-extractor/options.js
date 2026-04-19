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
