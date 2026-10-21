function api() {
  return typeof browser !== 'undefined' ? browser : chrome;
}

let activeTabId = null;
let activeProjectId = null;
let activeConversationId = null;

function sendMessage(msg) {
  return new Promise((resolve) => api().runtime.sendMessage(msg, resolve));
}

function getSettings() {
  return new Promise((resolve) =>
    api().storage.local.get({ projects: {}, excludedConversations: {} }, resolve)
  );
}

function setSettings(partial) {
  return new Promise((resolve) => api().storage.local.set(partial, resolve));
}

function formatTime(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function renderApi(context, enabled) {
  document.getElementById('api-toggle').checked = !!enabled;
  const status = (context && context.apiStatus) || {};
  let text = enabled ? 'Ein – noch kein Abruf auf dieser Seite.' : 'Aus – Nachrichten werden aus der Seite gelesen.';
  if (enabled && status.ok) text = `Ein – letzter Abruf erfolgreich (${formatTime(status.at)}).`;
  if (enabled && status.ok === false) text = `Schnittstelle gerade nicht nutzbar (${status.error}). Es wird aus der Seite gelesen.`;
  document.getElementById('api-status').textContent = text;
}

async function render() {
  const [tab] = await api().tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  activeTabId = tab.id;

  const context = await sendMessage({ type: 'get-context', tabId: tab.id });
  const settings = await getSettings();
  const { chatgptApi } = await new Promise((resolve) => api().storage.local.get({ chatgptApi: true }, resolve));
  renderApi(context, chatgptApi);

  if (!context || !context.projectId) {
    document.getElementById('no-project').classList.remove('hidden');
    document.getElementById('project-panel').classList.add('hidden');
    return;
  }

  document.getElementById('no-project').classList.add('hidden');
  document.getElementById('project-panel').classList.remove('hidden');

  activeProjectId = context.projectId;
  activeConversationId = context.conversationId;

  const proj = settings.projects[activeProjectId] || {
    name: context.projectName,
    syncEnabled: false,
    lastExport: null,
  };

  document.getElementById('project-name').textContent = proj.name || context.projectName;
  document.getElementById('sync-toggle').checked = !!proj.syncEnabled;
  document.getElementById('exclude-toggle').checked = !!settings.excludedConversations[
    activeConversationId
  ];
  const { lastExport } = await new Promise((resolve) => api().storage.local.get({ lastExport: {} }, resolve));
  document.getElementById('last-export').textContent = formatTime(lastExport[activeProjectId] || proj.lastExport);
  renderMapping(proj.name || context.projectName);
}

// Projektzuordnung ueber den Native Host (seit 0.2.0): nur Ordner, die der
// Host unter den konfigurierten project_roots anbietet, sind waehlbar.
let activeProjectName = null;

async function renderMapping(projectName, keepStatus = false) {
  activeProjectName = projectName;
  const select = document.getElementById('folder-select');
  const hint = document.getElementById('mapping-hint');
  const status = document.getElementById('mapping-status');
  const button = document.getElementById('map-button');
  if (!keepStatus) status.textContent = '';
  const res = await sendMessage({
    type: 'native-request',
    payload: { type: 'list-folders', projectId: activeProjectId },
  });
  if (!res || !res.ok) {
    select.innerHTML = '';
    select.disabled = true;
    button.disabled = true;
    hint.textContent = 'Zuordnung nur mit Native Host möglich (bridge/install-native-host.sh).';
    hint.classList.remove('hidden');
    return;
  }
  select.disabled = false;
  button.disabled = false;
  select.innerHTML = '';
  const preselect = res.current || res.suggested || '';
  if (!preselect) {
    const empty = document.createElement('option');
    empty.value = '';
    empty.textContent = '– bitte wählen –';
    select.appendChild(empty);
  }
  for (const folder of res.folders) {
    const opt = document.createElement('option');
    opt.value = folder;
    opt.textContent = folder;
    if (folder === preselect) opt.selected = true;
    select.appendChild(opt);
  }
  if (!res.current) {
    hint.textContent =
      'Noch keinem Ordner zugeordnet' +
      (res.pending ? ` – ${res.pending} Nachricht(en) warten.` : '.') +
      (res.suggested ? ` Vorschlag: ${res.suggested}.` : '');
    hint.classList.remove('hidden');
  } else {
    hint.classList.add('hidden');
  }
}

document.getElementById('map-button').addEventListener('click', async () => {
  const folder = document.getElementById('folder-select').value;
  const status = document.getElementById('mapping-status');
  if (!folder) {
    status.textContent = 'Bitte einen Ordner wählen.';
    return;
  }
  status.textContent = 'Ordne zu...';
  const payload = { type: 'map-project', projectId: activeProjectId, projectName: activeProjectName, folder };
  let res = await sendMessage({ type: 'native-request', payload });
  // Umhaengen oder Ordner teilen nur nach Rueckfrage (Review Runde 2, 2026-09-29).
  if (res && res.needs_confirm) {
    if (!confirm(`Achtung: dieses Projekt ${res.error}.\n\nWirklich ${folder} zuordnen?`)) {
      status.textContent = 'Nicht geändert.';
      return;
    }
    res = await sendMessage({ type: 'native-request', payload: { ...payload, confirm: true } });
  }
  if (res && res.ok) {
    const n = (res.counts && res.counts.processed) || 0;
    status.textContent =
      `Zugeordnet zu ${res.current}` +
      (n ? `, ${n} wartende Nachricht(en) übernommen.` : '.') +
      (res.busy ? ' Wartende Nachrichten werden gleich nachverarbeitet.' : '');
    renderMapping(activeProjectName, true);
  } else {
    status.textContent = 'Fehler: ' + ((res && res.error) || 'unbekannt');
  }
});

document.getElementById('sync-toggle').addEventListener('change', async (e) => {
  const settings = await getSettings();
  settings.projects[activeProjectId] = settings.projects[activeProjectId] || {
    name: document.getElementById('project-name').textContent,
    lastExport: null,
  };
  settings.projects[activeProjectId].syncEnabled = e.target.checked;
  await setSettings({ projects: settings.projects });
});

document.getElementById('exclude-toggle').addEventListener('change', async (e) => {
  const settings = await getSettings();
  if (e.target.checked) {
    settings.excludedConversations[activeConversationId] = true;
  } else {
    delete settings.excludedConversations[activeConversationId];
  }
  await setSettings({ excludedConversations: settings.excludedConversations });
});

document.getElementById('sync-now').addEventListener('click', async () => {
  const status = document.getElementById('sync-status');
  status.textContent = 'Synchronisiere...';
  const res = await sendMessage({ type: 'sync-now-relay', tabId: activeTabId });
  // Ehrlich melden, was passiert ist (vorher immer "Fertig." - Review Runde 2).
  if (!res || !res.ok) status.textContent = 'Fehler beim Sync' + (res && res.error ? `: ${res.error}` : '.');
  else if (res.skipped === 'busy') status.textContent = 'Läuft gerade – bitte gleich erneut.';
  else if (res.handled) status.textContent = res.sent ? 'Chat an die Bridge übergeben.' : 'Unverändert – nichts Neues.';
  else if (res.dom) status.textContent = res.sent ? `${res.sent} Nachricht(en) aus der Seite übergeben.` : 'Nichts Neues.';
  else status.textContent = 'Fertig.';
  setTimeout(() => render(), 500);
});

// Firefox erteilt die host_permissions einer MV3-Extension nicht in jedem
// Fall automatisch (z. B. bei temporaer geladenen Extensions). Ohne sie
// laeuft der Content Script nicht - Export nur nach Klick auf das Symbol,
// das voruebergehend Zugriff fuer den Tab gibt (2026-09-25 beobachtet).
const CHATGPT_ORIGINS = { origins: ['https://chatgpt.com/*', 'https://claude.ai/*'] };

async function renderPermission() {
  let granted = true;
  try {
    granted = await api().permissions.contains(CHATGPT_ORIGINS);
  } catch (_) {}
  document.getElementById('permission-warning').classList.toggle('hidden', granted);
}

document.getElementById('grant-permission').addEventListener('click', async () => {
  // permissions.request muss direkt aus der Nutzeraktion heraus aufgerufen werden.
  try {
    await api().permissions.request(CHATGPT_ORIGINS);
  } catch (err) {
    console.error('[Polaris] Berechtigung nicht erteilt:', err);
  }
  renderPermission();
});

renderPermission();
render();

document.getElementById('api-toggle').addEventListener('change', async (e) => {
  await setSettings({ chatgptApi: e.target.checked });
  render();
});
