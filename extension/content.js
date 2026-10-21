/*
 * Polaris - Sync Detection + Event Builder
 *
 * Nutzt ausschliesslich PolarisExtractor fuer DOM-Wissen. Enthaelt selbst
 * keine ChatGPT-spezifischen Selektoren.
 */

(() => {
  const DEBOUNCE_MS = 1500;
  // Obergrenze fuers Warten auf Ruhe im DOM: das aktuelle ChatGPT veraendert
  // die Seite offenbar laufend, der reine Debounce kam dann nie zum Zug und
  // der Auto-Export blieb aus, bis "Sync now" geklickt wurde (2026-09-25).
  const MAX_WAIT_MS = 5000;
  const SCHEMA_VERSION = 1;

  // chatgptApi: Erfassung ueber die Web-Schnittstelle (seit 0.3.0 Standard,
  // im Popup abschaltbar); faellt bei jeder Unstimmigkeit auf das DOM zurueck.
  let settings = { projects: {}, excludedConversations: {}, chatgptApi: true };
  let currentProjectId = null;
  let currentConversationId = null;
  let exportedIds = new Set(); // per page-load, reset on navigation
  // Turn-Schluessel (native ID, sonst Text) -> Chat, unter dem der Turn zuerst
  // im DOM stand. Bleibt ueber Navigationen hinweg bestehen (Erkennung
  // veralteter DOM-Inhalte).
  const seenInConversation = new Map();
  // So lange nach einem Chatwechsel auf das Ersetzen des DOM warten, danach
  // fremde Nachrichten einzeln aussortieren.
  const STALE_DOM_GRACE_MS = 20000;
  let navigatedAt = 0;
  // Was beim Erkennen des Chatwechsels im DOM stand. Waehrend der Frist gilt
  // nur als Inhalt des neuen Chats, was danach neu erscheint - deckt auch
  // Chats ab, die nur kurz (vor dem ersten Scan) offen waren (Review
  // 2026-09-29: X -> A -> B in < 1,5 s exportierte A unter B).
  let navSnapshot = new Set();
  // Letzte Antwort erst exportieren, wenn ihr Text so lange unveraendert ist
  // - unabhaengig davon, ob der Stop-Knopf erkannt wird. Eine halbe Antwort
  // bliebe sonst fuer immer halb (die ID gilt danach als exportiert).
  const SETTLE_MS = 8000;
  let settling = null; // { key, content, since }
  let settleTimer = null;
  let debounceTimer = null;
  let firstPendingAt = 0;
  let lastHref = location.href;

  function api() {
    return typeof browser !== 'undefined' ? browser : chrome;
  }

  function normalizeContent(text) {
    return text.trim().toLowerCase().replace(/\s+/g, ' ');
  }

  // Schluessel eines DOM-Turns: native ID, sonst der normalisierte Text
  // (Ersatz-IDs enthalten die Chat-ID und wuerden bei veraltetem DOM immer
  // neu aussehen). Liest innerText nur fuer Turns ohne native ID.
  function domKey(turn) {
    if (turn.nativeId) return turn.nativeId;
    if (turn._content === undefined) turn._content = PolarisExtractor.getTurnContent(turn.node) || '';
    return 'text:' + normalizeContent(turn._content).slice(0, 200);
  }

  async function sha256Hex(text) {
    const enc = new TextEncoder().encode(text);
    const digest = await crypto.subtle.digest('SHA-256', enc);
    return Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, '0'))
      .join('');
  }

  async function computeFallbackId(conversationId, role, parentId, content, position) {
    const canonical = [
      conversationId || '',
      role || '',
      parentId || '',
      normalizeContent(content || ''),
      String(position),
    ].join('|');
    const hex = await sha256Hex(canonical);
    return 'fb_' + hex.slice(0, 32);
  }

  function loadSettings() {
    return api().storage.local.get({ projects: {}, excludedConversations: {}, chatgptApi: true });
  }

  function saveSettings(partial) {
    return api().storage.local.set(partial);
  }

  async function ensureProjectRegistered(projectId, projectName) {
    if (!projectId) return;
    if (settings.projects[projectId]) {
      if (settings.projects[projectId].name !== projectName) {
        settings.projects[projectId].name = projectName;
        await saveSettings({ projects: settings.projects });
      }
      return;
    }
    settings.projects[projectId] = {
      name: projectName,
      syncEnabled: false,
      lastExport: null,
    };
    await saveSettings({ projects: settings.projects });
  }

  function sendContextUpdate(project, conversationId, conversationTitle) {
    api().runtime.sendMessage({
      type: 'context-update',
      context: {
        projectId: project ? project.id : null,
        projectName: project ? project.name : null,
        conversationId: conversationId || null,
        conversationTitle: conversationTitle || null,
      },
    });
  }

  function isSyncActiveForCurrentContext(project, conversationId) {
    if (!project) return false;
    const projSettings = settings.projects[project.id];
    if (!projSettings || !projSettings.syncEnabled) return false;
    if (conversationId && settings.excludedConversations[conversationId]) return false;
    return true;
  }

  async function buildEvent(project, conversationId, conversationTitle, turn, index, parent, href, capture = 'dom') {
    let messageId = turn.nativeId;
    let idSource = 'native';
    if (!messageId) {
      messageId = await computeFallbackId(
        conversationId,
        turn.role,
        parent ? parent.messageId : null,
        turn.content,
        index
      );
      idSource = 'fallback';
    }

    return {
      schema_version: SCHEMA_VERSION,
      source: 'chatgpt',
      project: { id: project.id, name: project.name },
      conversation: {
        id: conversationId,
        title: conversationTitle,
        // Zu Beginn des Scans festgehalten: nach den await-Schritten kann
        // location schon zum naechsten Chat gehoeren (url_mismatch).
        url: href,
      },
      message: {
        id: messageId,
        id_source: idSource,
        parent_id: parent ? parent.messageId : null,
        parent_id_source: parent ? (capture === 'api' ? 'chatgpt-api' : 'dom-order') : null,
        role: turn.role,
        source_timestamp: null,
        content: turn.content,
        capture,
        // Immer mitgeben, auch eine einzelne ID: die Bridge schlaegt sie in der
        // Mitglieder-Tabelle nach (Review Runde 3, H1).
        ...(turn.memberIds && turn.memberIds.length ? { member_ids: turn.memberIds } : {}),
        ...(turn.attachments && turn.attachments.length ? { attachments: turn.attachments } : {}),
      },
      captured_at: new Date().toISOString(),
    };
  }

  // ---- Chat-Abbild ueber die Web-Schnittstelle (seit 0.4.0, Standard) ----
  // Die Extension interpretiert hier NICHTS: sie holt das rohe Unterhaltungs-
  // JSON und reicht es an die Bridge weiter, die Turns bildet, das Projekt aus
  // den Anbieterdaten nimmt und entscheidet, was fertig ist (bridge/polaris/
  // snapshot.py). Bis 0.3.0 geschah das an drei Stellen unterschiedlich -
  // Quelle vieler Dubletten und Abschneidungen (Review 2026-09-29).
  // Gesendet wird nur, wenn sich der Chat geaendert hat (update_time).
  const SNAPSHOT_MIN_INTERVAL_MS = 10000;
  const SNAPSHOT_POLL_MS = 20000;
  const SNAPSHOT_IDLE_S = 190; // etwas ueber bridge snapshot.IDLE_SECONDS (180)
  // Seit 0.4.1: erst abrufen/auslesen, wenn der Nutzer auf einem Chat bleibt.
  // Schnelles Hin- und Herklicken loeste sonst viele Abrufe aus -> HTTP 429 ->
  // Rueckfall auf die (noch alte) Seite -> Nachrichten im falschen Chat
  // (Vorfall 2026-09-30 14:28). Wer nur durchklickt, verpasst nichts: beim
  // naechsten Verweilen wird der Chat vollstaendig abgerufen.
  const SNAPSHOT_DWELL_MS = 5000;
  // Voruebergehende Fehler (429, 5xx, Netz): warten und erneut abrufen, NICHT
  // aus der Seite lesen. Nur ein veraendertes Antwortformat faellt auf die
  // Seite zurueck.
  const API_BACKOFF_MIN_MS = 30000;
  const API_BACKOFF_MAX_MS = 600000;
  let apiBackoffUntil = 0;
  let apiBackoffMs = 0;

  function isTransientApiError(err) {
    const s = String(err);
    return /HTTP (429|5\d\d)\b/.test(s) || /NetworkError|Failed to fetch|TypeError|AbortError/.test(s);
  }
  const snapshotLastFetch = new Map(); // conversationId -> Zeitpunkt
  const snapshotLastVersion = new Map(); // conversationId -> update_time des zuletzt gesendeten
  let snapshotTimer = null;

  function apiStatus(status) {
    api().runtime.sendMessage({ type: 'api-status', status });
  }

  function scheduleSnapshotPoll() {
    if (snapshotTimer) clearTimeout(snapshotTimer);
    snapshotTimer = setTimeout(() => {
      snapshotTimer = null;
      if (document.visibilityState === 'hidden') {
        scheduleSnapshotPoll();
        return;
      }
      runExportCheck();
    }, SNAPSHOT_POLL_MS);
  }

  // Ergebnis: { handled: true, sent: n } oder { handled: false } (-> DOM-Weg).
  async function exportSnapshot(project, conversationId, { force = false } = {}) {
    if (!force && Date.now() < apiBackoffUntil) {
      setTimeout(scheduleExportCheck, apiBackoffUntil - Date.now() + 50);
      return { handled: true, sent: 0, deferred: true };
    }
    const last = snapshotLastFetch.get(conversationId) || 0;
    if (!force && Date.now() - last < SNAPSHOT_MIN_INTERVAL_MS) {
      scheduleExportCheck();
      return { handled: true, sent: 0 };
    }
    snapshotLastFetch.set(conversationId, Date.now());
    let raw;
    try {
      raw = await PolarisChatGptApi.fetchConversationRaw(conversationId, (...a) => fetch(...a));
    } catch (err) {
      apiStatus({ ok: false, error: String(err).slice(0, 120), at: new Date().toISOString() });
      if (isTransientApiError(err)) {
        apiBackoffMs = Math.min(API_BACKOFF_MAX_MS, Math.max(API_BACKOFF_MIN_MS, apiBackoffMs * 2));
        apiBackoffUntil = Date.now() + apiBackoffMs;
        snapshotLastFetch.delete(conversationId);
        console.warn('[Polaris] Schnittstelle voruebergehend nicht nutzbar (%s) - neuer Versuch in %d s',
          String(err), Math.round(apiBackoffMs / 1000));
        setTimeout(scheduleExportCheck, apiBackoffMs + 50);
        return { handled: true, sent: 0, deferred: true };
      }
      console.warn('[Polaris] Schnittstelle nicht nutzbar (%s) - lese aus der Seite', String(err));
      return { handled: false };
    }
    apiBackoffMs = 0;
    apiBackoffUntil = 0;
    scheduleSnapshotPoll(); // spaete Fertigstellung (Werkzeugschritte, lange Antworten)
    // Version + Ruhe: ein Chat, der seit IDLE_S unveraendert ist, wird einmal
    // erneut gesendet - die Bridge gibt die letzte Antwort erst bei Ruhe frei,
    // wenn sie nicht mit sichtbarem Text endet (Bild, Werkzeugschritt). Ohne
    // das blieb sie fuer immer liegen (Review Runde 4, F1).
    const updated = Number(raw && raw.update_time);
    const idle = Number.isFinite(updated) && Date.now() / 1000 - updated >= SNAPSHOT_IDLE_S;
    const base = raw && (raw.update_time || raw.current_node);
    const version = base ? `${base}${idle ? ':idle' : ''}` : null;
    if (!force && version && snapshotLastVersion.get(conversationId) === version) {
      return { handled: true, sent: 0 };
    }
    const res = await api().runtime.sendMessage({
      type: 'export-snapshot',
      snapshot: {
        polaris_snapshot: 1,
        source: 'chatgpt',
        conversation: raw,
        // Nur zum Gegenpruefen in der Bridge; massgeblich ist gizmo_id.
        hint: { project_id: project.id, conversation_id: conversationId },
        captured_at: new Date().toISOString(),
      },
    });
    if (res && res.ok) {
      if (version) snapshotLastVersion.set(conversationId, version);
      apiStatus({ ok: true, at: new Date().toISOString() });
      return { handled: true, sent: 1, counts: res.counts };
    }
    const error = String((res && res.error) || 'Uebergabe fehlgeschlagen');
    console.error('[Polaris] Abbild nicht uebernommen:', error);
    apiStatus({ ok: false, error: error.slice(0, 120), at: new Date().toISOString() });
    // Widerspruch Seite/Anbieter: nichts exportieren (die Seite koennte falsch
    // zuordnen). Unbekannte Antwortform, fehlendes Projekt, Uebergabefehler:
    // ersatzweise aus der Seite lesen (Review Runde 3).
    if (error.includes('snapshot-mismatch')) return { handled: true, sent: 0, error };
    return { handled: false, error };
  }

  // Auch im Abbild-Modus merken, welcher Chat welche DOM-Turns zeigt - fuer
  // einen spaeteren Rueckfall auf den DOM-Weg (Erkennung veralteter Inhalte).
  function recordDomOwnership(conversationId) {
    if (Date.now() - navigatedAt < STALE_DOM_GRACE_MS) return; // DOM evtl. noch alt
    for (const t of PolarisExtractor.getTurns()) {
      const key = domKey(t);
      if (!seenInConversation.has(key)) seenInConversation.set(key, conversationId);
    }
  }

  // streaming=true: ChatGPT antwortet vermutlich noch - alles exportieren
  // ausser der letzten Antwort (koennte unfertig sein; sie folgt beim
  // naechsten Check). Frueher wurde in diesem Fall komplett gewartet, bei
  // haengender Erkennung fuer immer.
  async function diffAndExport({ streaming = false, force = false } = {}) {
    // Navigation vor dem 700-ms-Intervall bemerkt: erst zuruecksetzen.
    if (location.href !== lastHref) {
      lastHref = location.href;
      resetForNavigation();
    }
    const href = location.href;
    const project = PolarisExtractor.getProjectInfo();
    const conversationId = PolarisExtractor.getConversationId();
    const conversationTitle = PolarisExtractor.getConversationTitle();

    if (project) {
      await ensureProjectRegistered(project.id, project.name);
    }
    sendContextUpdate(project, conversationId, conversationTitle);

    // Ohne Chat-ID (z.B. Projektseite /g/g-p-.../project) gibt es nichts
    // Zuzuordnendes; dort stehen oft noch Nachrichten des vorherigen Chats
    // im DOM (2026-09-29, 29 Ablehnungen durch die Bridge).
    if (!conversationId) return { sent: 0 };

    // Nach den await-Schritten oben kann die Seite schon gewechselt haben:
    // dann gehoert, was jetzt im DOM steht, womoeglich nicht zu href.
    if (location.href !== href) {
      scheduleExportCheck();
      return { sent: 0 };
    }

    // Erst nach kurzem Verweilen auf dem Chat (SNAPSHOT_DWELL_MS), fuer beide Wege.
    const dwellLeft = navigatedAt ? navigatedAt + SNAPSHOT_DWELL_MS - Date.now() : 0;
    if (!force && dwellLeft > 0) {
      setTimeout(scheduleExportCheck, dwellLeft + 50);
      return { sent: 0, deferred: true };
    }

    if (settings.chatgptApi && isSyncActiveForCurrentContext(project, conversationId)) {
      const res = await exportSnapshot(project, conversationId, { force });
      if (res.handled) {
        recordDomOwnership(conversationId);
        return res;
      }
    }

    let turns = PolarisExtractor.getTurns();

    // ChatGPT aendert beim Chatwechsel zuerst die URL und ersetzt die
    // Nachrichten erst danach. Ohne diese Pruefung wurden die alten
    // Nachrichten unter dem neuen Chat exportiert und landeten im falschen
    // Projekt (2026-09-29, zwei Kundenprojekte). Die Pruefung laeuft vor der
    // Sync-Pruefung, damit auch IDs aus nicht synchronisierten Chats
    // bekannt sind.
    const inGrace = Date.now() - navigatedAt < STALE_DOM_GRACE_MS;
    const keyed = turns.map((t) => ({ t, key: domKey(t) }));
    const ownedElsewhere = ({ key }) => {
      const owner = seenInConversation.get(key);
      return owner !== undefined && owner !== conversationId;
    };
    if (inGrace && keyed.some(ownedElsewhere)) {
      console.debug('[Polaris] Nachrichten des vorherigen Chats noch im DOM - warte');
      scheduleExportCheck();
      return;
    }
    let usable = keyed.filter((k) => !ownedElsewhere(k));
    if (usable.length < keyed.length) {
      console.warn(
        '[Polaris] %d Nachricht(en) gehoeren zu einem anderen Chat und werden nicht exportiert',
        keyed.length - usable.length
      );
    }
    if (inGrace && navSnapshot.size) {
      const fresh = usable.filter(({ key }) => !navSnapshot.has(key));
      if (fresh.length < usable.length) {
        // Stand schon beim Wechsel im DOM: erst nach der Frist zulassen.
        scheduleExportCheck();
        if (!fresh.length) return;
      }
      usable = fresh;
    }
    usable.forEach(({ key }) => {
      if (!seenInConversation.has(key)) seenInConversation.set(key, conversationId);
    });
    turns = usable.map(({ t }) => t);

    if (!isSyncActiveForCurrentContext(project, conversationId)) {
      console.debug(
        '[Polaris] Sync nicht aktiv fuer diesen Kontext (project=%s, conversation=%s)',
        project?.id,
        conversationId
      );
      return;
    }


    if (streaming) {
      let last = -1;
      turns.forEach((t, i) => {
        if (t.role === 'assistant') last = i;
      });
      if (last >= 0) {
        console.debug('[Polaris] Antwort laeuft noch - letzte Antwort wird spaeter exportiert');
        turns = turns.slice(0, last);
      }
    }
    // Letzte Antwort erst, wenn ihr Text SETTLE_MS unveraendert ist.
    const tail = turns[turns.length - 1];
    if (tail && tail.role === 'assistant' && !(tail.nativeId && exportedIds.has(tail.nativeId))) {
      const key = domKey(tail);
      const content = tail._content !== undefined ? tail._content : PolarisExtractor.getTurnContent(tail.node) || '';
      const now = Date.now();
      if (!settling || settling.key !== key || settling.content !== content) {
        settling = { key, content, since: now };
      }
      if (now - settling.since < SETTLE_MS) {
        turns = turns.slice(0, -1);
        if (settleTimer) clearTimeout(settleTimer);
        settleTimer = setTimeout(() => {
          settleTimer = null;
          runExportCheck();
        }, SETTLE_MS - (now - settling.since) + 50);
      }
    }

    let parent = null;
    const events = [];
    for (let i = 0; i < turns.length; i++) {
      const turn = turns[i];

      // Bereits bekannte Turns mit nativer ID: ueberspringen OHNE
      // .innerText zu lesen (teuer, erzwingt Layout) - die ID allein
      // reicht zur Dedup-Entscheidung.
      if (turn.nativeId && exportedIds.has(turn.nativeId)) {
        parent = { messageId: turn.nativeId };
        continue;
      }

      const content = turn._content !== undefined ? turn._content : PolarisExtractor.getTurnContent(turn.node);
      if (!content) continue; // noch leer/streamend, naechster Check greift erneut

      const key = turn.nativeId || `${i}:${normalizeContent(content).slice(0, 64)}`;
      if (exportedIds.has(key)) {
        parent = { messageId: turn.nativeId || parent?.messageId };
        continue;
      }

      const event = await buildEvent(
        project,
        conversationId,
        conversationTitle,
        { ...turn, content },
        i,
        parent,
        href
      );
      events.push(event);
      exportedIds.add(key);
      parent = { messageId: event.message.id };
    }

    if (events.length > 0) {
      const res = await api().runtime.sendMessage({ type: 'export-events', events });
      if (!res || !res.ok) {
        console.error('[Polaris] Export fehlgeschlagen:', res && res.error);
        // Beim naechsten Check erneut versuchen (Review Runde 2).
        events.forEach((e) => exportedIds.delete(e.message.id));
        return { sent: 0, error: res && res.error };
      } else {
        console.debug(
          '[Polaris] %d neue Message(s) exportiert (%d direkt an die Bridge, %d per Download), %d bereits frueher exportiert',
          res.downloaded,
          res.native || 0,
          res.fallbackDownloads || 0,
          res.skipped
        );
      }
    } else {
      console.debug('[Polaris] keine neuen Messages zum Exportieren gefunden');
    }
    return { sent: events.length, dom: true };
  }

  function runExportCheck() {
    debounceTimer = null;
    firstPendingAt = 0;
    const streaming = PolarisExtractor.isStreaming();
    diffAndExport({ streaming }).catch((err) => console.error('[Polaris]', err));
    // Waehrend einer laufenden Antwort regelmaessig nachsehen, auch falls
    // nach dem Ende keine DOM-Aenderung mehr kommt.
    if (streaming) scheduleExportCheck();
  }

  function scheduleExportCheck() {
    const now = Date.now();
    if (!firstPendingAt) firstPendingAt = now;
    if (debounceTimer) clearTimeout(debounceTimer);
    const wait = Math.max(0, Math.min(DEBOUNCE_MS, firstPendingAt + MAX_WAIT_MS - now));
    debounceTimer = setTimeout(runExportCheck, wait);
  }

  function resetForNavigation(initial = false) {
    exportedIds = new Set();
    // Beim ersten Laden gehoert das DOM sicher zur Adresse; nur bei einem
    // Wechsel festhalten, was (noch) zu sehen ist.
    navigatedAt = initial ? 0 : Date.now();
    navSnapshot = initial ? new Set() : new Set(PolarisExtractor.getTurns().map(domKey));
    settling = null;
    currentConversationId = PolarisExtractor.getConversationId();
    const project = PolarisExtractor.getProjectInfo();
    currentProjectId = project?.id || null;
    // Kontext sofort melden, damit das Popup das Projekt direkt anzeigt.
    sendContextUpdate(project, currentConversationId, PolarisExtractor.getConversationTitle());
    scheduleExportCheck();
  }

  function watchNavigation() {
    setInterval(() => {
      if (location.href !== lastHref) {
        lastHref = location.href;
        resetForNavigation();
      }
    }, 700);
  }

  function watchDom() {
    const observer = new MutationObserver(() => {
      scheduleExportCheck();
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  api().runtime.onMessage.addListener((msg) => {
    if (msg.type === 'sync-now') {
      // Abbild sofort holen (ohne Drossel); im DOM-Weg laufende Antworten
      // zurueckhalten. Ergebnis ehrlich ans Popup (Review Runde 2).
      return diffAndExport({ streaming: PolarisExtractor.isStreaming(), force: true })
        .then((res) => ({ ok: !(res && res.error), ...(res || {}) }))
        .catch((err) => {
          console.error('[Polaris] sync-now fehlgeschlagen:', err);
          return { ok: false, error: String(err) };
        });
    }
    if (msg.type === 'settings-changed') {
      loadSettings().then((s) => {
        settings = s;
      });
    }
    return undefined;
  });

  api().storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    if (changes.projects) settings.projects = changes.projects.newValue || {};
    if (changes.chatgptApi) settings.chatgptApi = changes.chatgptApi.newValue !== false;
    if (changes.excludedConversations) {
      settings.excludedConversations = changes.excludedConversations.newValue || {};
    }
  });

  (async () => {
    settings = await loadSettings();
    apiStatus(null); // kein veralteter Zustand einer frueheren Seite im Popup
    resetForNavigation(true);
    watchNavigation();
    watchDom();
  })();
})();
