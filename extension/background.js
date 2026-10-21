/*
 * Polaris - Background Script
 *
 * Verantwortlich fuer: Browser-Downloads in die Polaris Inbox, Ablage des
 * pro-Tab erkannten Kontexts fuer das Popup, und Relais von
 * Popup-Aktionen an den Content Script.
 *
 * Enthaelt keine ChatGPT-DOM-Logik und keine Projektpfad-Logik.
 *
 * Messaging-Pattern: Listener geben bei asynchroner Arbeit direkt ein
 * Promise zurueck (Firefox-idiomatisch), statt sendResponse()+"return
 * true" zu mischen. Letzteres fuehrte im ersten Live-Test zu
 * "Promised response from onMessage listener went out of scope" -
 * Firefox invalidiert den Message-Port, wenn die sendResponse-Callback-
 * Kette zu lange offen bleibt bzw. der Aufrufer-Kontext inzwischen
 * wegfaellt. Ein direkt zurueckgegebenes Promise umgeht das.
 */

function api() {
  return typeof browser !== 'undefined' ? browser : chrome;
}

const tabContexts = new Map(); // tabId -> {projectId, projectName, conversationId, conversationTitle}

function sanitizeForFilename(value) {
  return String(value || 'unknown').replace(/[^a-zA-Z0-9_.-]/g, '_').slice(0, 120);
}

// Dauerhaftes Gedaechtnis, welche Nachrichten schon heruntergeladen wurden
// (2026-09-25: der Content Script merkt sich das nur bis zum Neuladen der
// Seite, jede Rueckkehr in eine Unterhaltung loeste deshalb erneut Downloads
// aller Nachrichten aus). Nur IDs, keine Inhalte; begrenzt auf die zuletzt
// benutzten Unterhaltungen. Als erledigt gilt eine Nachricht erst, wenn ihr
// Download wirklich abgeschlossen ist - ein abgebrochener wird wiederholt.
const EXPORTED_KEY = 'exportedMessages';
const MAX_CONVERSATIONS = 500;
const MAX_IDS_PER_CONVERSATION = 5000;
const inflight = new Set(); // "<conv>|<msg>" waehrend des Downloads

function exportKey(event) {
  return `${event.conversation?.id || 'unknown'}|${event.message?.id || 'unknown'}`;
}

async function loadExported() {
  const data = await api().storage.local.get({ [EXPORTED_KEY]: {} });
  return data[EXPORTED_KEY] || {};
}

// Lesen-Aendern-Schreiben der Liste nacheinander: zwei gleichzeitige Exporte
// (Timer + "Sync now", zwei Tabs) verloren sonst gegenseitig ihre Eintraege
// (Review 2026-09-29).
let markChain = Promise.resolve();

function markExported(events) {
  const run = markChain.then(() => markExportedNow(events));
  markChain = run.catch(() => {});
  return run;
}

async function markExportedNow(events) {
  const list = Array.isArray(events) ? events : [events];
  const exported = await loadExported();
  const now = Date.now();
  for (const event of list) {
    const conv = event.conversation?.id || 'unknown';
    const msg = event.message?.id;
    if (!msg) continue;
    const entry = exported[conv] || { ids: [], at: 0 };
    if (!entry.ids.includes(msg)) entry.ids.push(msg);
    if (entry.ids.length > MAX_IDS_PER_CONVERSATION) {
      entry.ids = entry.ids.slice(-MAX_IDS_PER_CONVERSATION);
    }
    entry.at = now;
    exported[conv] = entry;
  }
  const convs = Object.keys(exported);
  if (convs.length > MAX_CONVERSATIONS) {
    convs
      .sort((a, b) => exported[a].at - exported[b].at)
      .slice(0, convs.length - MAX_CONVERSATIONS)
      .forEach((c) => delete exported[c]);
  }
  await api().storage.local.set({ [EXPORTED_KEY]: exported });
}

// --- Native Messaging (Hauptweg seit 2026-09-25) ---------------------------
// Firefox startet den lokalen Host (bridge/bin/polaris-native-host, registriert
// per bridge/install-native-host.sh) nur bei Bedarf; er schreibt die Events
// direkt ueber die Bridge ins Projekt - kein Download, kein Downloads-Ordner.
// Der Port bleibt offen, solange Nachrichten kommen, und wird nach
// NATIVE_IDLE_MS ohne Aktivitaet geschlossen (Host beendet sich dann).
// Ist der Host nicht erreichbar, faellt die Extension auf Downloads zurueck
// und versucht Native erst nach NATIVE_RETRY_MS erneut.
const NATIVE_HOST = 'polaris_bridge';
const NATIVE_IDLE_MS = 30 * 1000;
const NATIVE_REPLY_TIMEOUT_MS = 90 * 1000;
const NATIVE_RETRY_MS = 5 * 60 * 1000;
let nativePort = null;
let nativeIdleTimer = null;
let nativeUnavailableUntil = 0;
let nativeRequestId = 0;
const nativePending = new Map(); // id -> {resolve, reject, timer}

function closeNativePort() {
  if (nativePort) {
    try {
      nativePort.disconnect();
    } catch (_) {}
  }
  nativePort = null;
}

function failAllNativePending(reason) {
  for (const [, p] of nativePending) {
    clearTimeout(p.timer);
    p.reject(new Error(reason));
  }
  nativePending.clear();
}

function getNativePort() {
  if (nativePort) return nativePort;
  if (Date.now() < nativeUnavailableUntil) return null;
  let port;
  try {
    port = api().runtime.connectNative(NATIVE_HOST);
  } catch (err) {
    nativeUnavailableUntil = Date.now() + NATIVE_RETRY_MS;
    console.warn('[Polaris] Native Host nicht verfuegbar, nutze Downloads:', err);
    return null;
  }
  port.onMessage.addListener((reply) => {
    const pending = reply && nativePending.get(reply.id);
    if (!pending) return;
    nativePending.delete(reply.id);
    clearTimeout(pending.timer);
    pending.resolve(reply);
  });
  port.onDisconnect.addListener((p) => {
    const error = (p && p.error) || api().runtime.lastError;
    if (nativePort === port) nativePort = null;
    if (error) {
      nativeUnavailableUntil = Date.now() + NATIVE_RETRY_MS;
      console.warn('[Polaris] Native Host getrennt, nutze Downloads:', error.message || error);
    }
    failAllNativePending('Native Host getrennt');
  });
  nativePort = port;
  return port;
}

function sendNative(payload) {
  const port = getNativePort();
  if (!port) return Promise.reject(new Error('Native Host nicht verfuegbar'));
  if (nativeIdleTimer) clearTimeout(nativeIdleTimer);
  nativeIdleTimer = setTimeout(closeNativePort, NATIVE_IDLE_MS + NATIVE_REPLY_TIMEOUT_MS);
  const id = ++nativeRequestId;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      nativePending.delete(id);
      reject(new Error('Native Host antwortet nicht'));
    }, NATIVE_REPLY_TIMEOUT_MS);
    nativePending.set(id, { resolve, reject, timer });
    try {
      port.postMessage({ id, ...payload });
    } catch (err) {
      nativePending.delete(id);
      clearTimeout(timer);
      reject(err);
    }
  }).finally(() => {
    if (nativeIdleTimer) clearTimeout(nativeIdleTimer);
    nativeIdleTimer = setTimeout(closeNativePort, NATIVE_IDLE_MS);
  });
}

// Liefert die Events, die der Host NICHT angenommen hat (-> Download-Rueckfall).
async function exportViaNative(events) {
  const reply = await sendNative({ type: 'export-events', events });
  const accepted = new Set((reply && reply.accepted) || []);
  const done = events.filter((e) => accepted.has(e.message?.id));
  if (done.length) await markExported(done);
  if (reply && reply.error) console.warn('[Polaris] Native Host meldet:', reply.error);
  return events.filter((e) => !accepted.has(e.message?.id));
}

function settleDownload(downloadId, url, event) {
  const key = exportKey(event);
  const listener = (delta) => {
    if (delta.id !== downloadId || !delta.state) return;
    const state = delta.state.current;
    if (state !== 'complete' && state !== 'interrupted') return;
    URL.revokeObjectURL(url);
    api().downloads.onChanged.removeListener(listener);
    inflight.delete(key);
    if (state === 'complete') {
      markExported(event).catch((err) => console.error('[Polaris] Merken fehlgeschlagen:', err));
      // Aus der Firefox-Downloadliste entfernen; die Datei bleibt liegen
      // und wird von der Polaris Bridge abgeholt.
      api().downloads.erase({ id: downloadId }).catch(() => {});
    }
  };
  api().downloads.onChanged.addListener(listener);
}

async function downloadEvent(event) {
  const projectId = sanitizeForFilename(event.project?.id);
  const conversationId = sanitizeForFilename(event.conversation?.id);
  const messageId = sanitizeForFilename(event.message?.id);
  const filename = `polaris-inbox/${projectId}__${conversationId}__${messageId}.json`;
  const json = JSON.stringify(event, null, 2);
  // Firefox lehnt data:-URLs fuer downloads.download() ab ("Access
  // denied for URL", im Live-Test 2026-09-12 gefunden). Blob +
  // createObjectURL ist der auf Firefox uebliche Weg dafuer - geht nur,
  // weil unser Background ein echtes Script (background.scripts) mit
  // Fenster-Kontext ist, kein service_worker ohne DOM-APIs.
  const blob = new Blob([json], { type: 'application/json' });
  const url = URL.createObjectURL(blob);

  inflight.add(exportKey(event));
  try {
    const downloadId = await api().downloads.download({
      url,
      filename,
      saveAs: false,
      conflictAction: 'uniquify',
    });
    settleDownload(downloadId, url, event);
  } catch (err) {
    inflight.delete(exportKey(event));
    URL.revokeObjectURL(url);
    console.error('[Polaris] Download fehlgeschlagen:', filename, err);
    throw err;
  }
}

// Eigener Schluessel statt projects[..].lastExport: das Zurueckschreiben des
// ganzen projects-Objekts konnte eine gleichzeitige Sync-Umschaltung im Popup
// rueckgaengig machen (Review Runde 2).
async function noteLastExport(projectId) {
  if (!projectId) return;
  const data = await api().storage.local.get({ lastExport: {} });
  data.lastExport[projectId] = new Date().toISOString();
  await api().storage.local.set({ lastExport: data.lastExport });
}

// Chat-Abbild (seit 0.4.0): an den Native Host, sonst als Abbild-Datei in den
// Downloads-Inbox-Ordner. Die Bridge ist idempotent - kein eigenes Dedup.
async function handleExportSnapshot(snapshot) {
  const conv = snapshot.conversation || {};
  const convId = sanitizeForFilename(conv.conversation_id || conv.id);
  try {
    const reply = await sendNative({ type: 'export-snapshot', snapshot });
    if (reply && reply.ok) {
      await noteLastExport(snapshot.hint && snapshot.hint.project_id);
      return { ok: true, native: true, counts: reply.counts };
    }
    // Die Bridge hat geantwortet und abgelehnt: das IST das Ergebnis - nicht
    // zusaetzlich herunterladen und nicht als Erfolg melden (Review Runde 4, F2).
    return { ok: false, native: true, error: (reply && reply.error) || 'von der Bridge abgelehnt' };
  } catch (err) {
    console.warn('[Polaris] Native Export fehlgeschlagen, nutze Downloads:', err.message || err);
  }
  const blob = new Blob([JSON.stringify(snapshot)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  try {
    await api().downloads.download({
      url,
      filename: `polaris-inbox/snapshot__${convId}__${Date.now()}.json`,
      saveAs: false,
      conflictAction: 'uniquify',
    });
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }
  await noteLastExport(snapshot.hint && snapshot.hint.project_id);
  return { ok: true, native: false };
}

async function handleExportEvents(events) {
  const exported = await loadExported();
  const fresh = events.filter((event) => {
    const conv = event.conversation?.id || 'unknown';
    const known = exported[conv] && exported[conv].ids.includes(event.message?.id);
    return !known && !inflight.has(exportKey(event));
  });
  let viaDownload = fresh;
  let viaNative = 0;
  if (fresh.length > 0) {
    // Waehrend der Uebergabe als "unterwegs" markieren, damit ein parallel
    // laufender zweiter Check dieselben Nachrichten nicht nochmal schickt.
    const keys = fresh.map(exportKey);
    keys.forEach((k) => inflight.add(k));
    try {
      viaDownload = await exportViaNative(fresh);
      viaNative = fresh.length - viaDownload.length;
    } catch (err) {
      console.warn('[Polaris] Native Export fehlgeschlagen, nutze Downloads:', err.message || err);
      viaDownload = fresh;
    } finally {
      // Nur die erledigten freigeben; die fuer den Download bleiben
      // "unterwegs", bis downloadEvent sie selbst fuehrt (sonst konnte ein
      // paralleler Check sie doppelt herunterladen - Review Runde 2).
      const pendingDownload = new Set(viaDownload.map(exportKey));
      keys.filter((k) => !pendingDownload.has(k)).forEach((k) => inflight.delete(k));
    }
  }
  for (const event of viaDownload) {
    await downloadEvent(event);
  }
  if (fresh.length > 0) await noteLastExport(fresh[0].project?.id);
  return {
    downloaded: fresh.length,
    native: viaNative,
    fallbackDownloads: viaDownload.length,
    skipped: events.length - fresh.length,
  };
}

api().runtime.onMessage.addListener((msg, sender) => {
  const tabId = sender.tab ? sender.tab.id : msg.tabId;

  if (msg.type === 'context-update') {
    if (tabId != null) {
      const apiStatus = (tabContexts.get(tabId) || {}).apiStatus;
      tabContexts.set(tabId, { ...msg.context, ...(apiStatus ? { apiStatus } : {}) });
    }
    return;
  }

  // Zustand der Erfassung ueber die ChatGPT-Schnittstelle, fuers Popup und -
  // ueber den Native Host - fuer den Health Check (sonst bemerkte niemand einen
  // Ausfall, solange der DOM-Rueckfall noch Nachrichten liefert).
  if (msg.type === 'api-status') {
    if (tabId != null) tabContexts.set(tabId, { ...(tabContexts.get(tabId) || {}), apiStatus: msg.status });
    if (msg.status) sendNative({ type: 'capture-status', status: msg.status }).catch(() => {});
    return;
  }

  if (msg.type === 'export-snapshot') {
    return handleExportSnapshot(msg.snapshot).catch((err) => {
      console.error('[Polaris] export-snapshot fehlgeschlagen:', err);
      return { ok: false, error: String(err) };
    });
  }

  if (msg.type === 'export-events') {
    return handleExportEvents(msg.events)
      .then((result) => ({ ok: true, ...result }))
      .catch((err) => {
        console.error('[Polaris] export-events fehlgeschlagen:', err);
        return { ok: false, error: String(err) };
      });
  }

  // Projektzuordnung aus dem Popup (nur diese beiden Anfragen werden an den
  // Native Host durchgereicht).
  if (msg.type === 'native-request' && ['list-folders', 'map-project'].includes(msg.payload?.type)) {
    return sendNative(msg.payload).catch((err) => ({ ok: false, error: String(err.message || err) }));
  }

  if (msg.type === 'get-context') {
    return Promise.resolve(tabContexts.get(msg.tabId) || null);
  }

  if (msg.type === 'sync-now-relay') {
    return api()
      .tabs.sendMessage(msg.tabId, { type: 'sync-now' })
      .catch((err) => {
        console.error('[Polaris] sync-now-relay fehlgeschlagen:', err);
        return { ok: false, error: String(err) };
      });
  }

  return undefined;
});

api().tabs.onRemoved.addListener((tabId) => {
  tabContexts.delete(tabId);
});
