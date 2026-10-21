/*
 * Polaris - Content Script fuer claude.ai (seit v0.2.0, 2026-09-25)
 *
 * Liest Claude-Projekt-Unterhaltungen NICHT aus dem Seitenaufbau, sondern
 * ueber dieselbe JSON-Schnittstelle, die die Claude-Web-App selbst laedt
 * (per Konsolen-Probe des Nutzers am 2026-09-25 verifiziert):
 *   GET /api/organizations/<org>/chat_conversations/<id>?tree=True&rendering_mode=messages
 *   -> { uuid, name, project_uuid, current_leaf_message_uuid,
 *        chat_messages: [{ uuid, sender, parent_message_uuid, created_at,
 *                          updated_at, content: [{type: text|thinking|tool_use|tool_result}],
 *                          text, attachments, files }] }
 * Die Schnittstelle ist nicht offiziell dokumentiert, aber stabiler als DOM-
 * Selektoren. Uebernommen wird nur der aktuelle Gespraechsstrang (Leaf ->
 * Wurzel) und nur Inhalt vom Typ "text" - keine Denkprozesse, keine
 * Werkzeugaufrufe. Projekt-IDs werden als "claude-p-<uuid>" gefuehrt, damit
 * sie sich nie mit ChatGPT-IDs ueberschneiden.
 *
 * Settings (Sync pro Projekt, ausgeschlossene Chats), Dedup und Uebergabe
 * (Native Messaging / Download-Rueckfall) teilt sich dieses Script mit dem
 * ChatGPT-Teil ueber storage.local und den Background.
 */
(() => {
  function api() {
    return typeof browser !== 'undefined' ? browser : chrome;
  }

  const SCHEMA_VERSION = 1;
  const CHECK_INTERVAL_MS = 15 * 1000;
  // Eine Antwort, die vor weniger als SETTLE_MS zuletzt geaendert wurde,
  // laeuft moeglicherweise noch; sie wird beim naechsten Check exportiert.
  const SETTLE_MS = 30 * 1000;
  const CHAT_PATH_RE = /^\/chat\/([0-9a-f-]{36})/;
  const ORG_RE = /\/api\/organizations\/([0-9a-f-]{36})\//;

  let settings = { projects: {}, excludedConversations: {} };
  let orgId = null;
  let running = false;
  // Waehrend eines laufenden Checks angeforderter Check (z. B. nach einem
  // Chatwechsel) wird danach nachgeholt statt verworfen (Review 2026-09-29).
  let rerun = null;
  let lastHref = location.href;
  const projectNames = new Map();

  function findOrgId() {
    const hit = performance
      .getEntriesByType('resource')
      .map((e) => e.name.match(ORG_RE))
      .find(Boolean);
    if (hit) return hit[1];
    const cookie = document.cookie.match(/(?:^|;\s*)lastActiveOrg=([0-9a-f-]{36})/);
    return cookie ? cookie[1] : null;
  }

  async function getJson(path) {
    const res = await fetch(path, { credentials: 'include', headers: { accept: 'application/json' } });
    if (!res.ok) throw new Error(`${res.status} fuer ${path.replace(/[0-9a-f-]{36}/g, '<id>')}`);
    return res.json();
  }

  async function getProjectName(projectUuid) {
    if (projectNames.has(projectUuid)) return projectNames.get(projectUuid);
    let name = null;
    try {
      const project = await getJson(`/api/organizations/${orgId}/projects/${projectUuid}`);
      name = project && project.name;
    } catch (_) {}
    name = name || projectUuid;
    projectNames.set(projectUuid, name);
    return name;
  }

  function activeBranch(conv) {
    const messages = conv.chat_messages || [];
    const byId = new Map(messages.map((m) => [m.uuid, m]));
    const chain = [];
    const seen = new Set();
    let id = conv.current_leaf_message_uuid;
    while (id && byId.has(id) && !seen.has(id)) {
      seen.add(id);
      const m = byId.get(id);
      chain.push(m);
      id = m.parent_message_uuid;
    }
    if (!chain.length) return [...messages].sort((a, b) => (a.index || 0) - (b.index || 0));
    return chain.reverse();
  }

  function textOf(message) {
    const blocks = Array.isArray(message.content) ? message.content : [];
    const text = blocks
      .filter((b) => b && b.type === 'text' && typeof b.text === 'string')
      .map((b) => b.text)
      .join('\n\n')
      .trim();
    return text || (typeof message.text === 'string' ? message.text.trim() : '');
  }

  function fileNames(message) {
    const names = [
      ...(message.attachments || []).map((a) => a && (a.file_name || a.name)),
      ...(message.files || []).map((f) => f && (f.file_name || f.name)),
    ].filter(Boolean);
    return [...new Set(names)];
  }

  async function loadSettings() {
    settings = await api().storage.local.get({ projects: {}, excludedConversations: {} });
  }

  async function ensureProjectRegistered(projectId, projectName) {
    const existing = settings.projects[projectId];
    if (existing) {
      if (existing.name !== projectName) {
        existing.name = projectName;
        await api().storage.local.set({ projects: settings.projects });
      }
      return;
    }
    settings.projects[projectId] = { name: projectName, syncEnabled: false, lastExport: null };
    await api().storage.local.set({ projects: settings.projects });
  }

  function sendContext(project, conversationId, title) {
    // Eine spaete Antwort fuer den vorherigen Chat darf das Popup nicht auf
    // dessen Projekt zuruecksetzen (dort wird Sync/Ordner eingestellt).
    const m = location.pathname.match(CHAT_PATH_RE);
    if (conversationId && (!m || m[1] !== conversationId)) return;
    api().runtime.sendMessage({
      type: 'context-update',
      context: {
        projectId: project ? project.id : null,
        projectName: project ? project.name : null,
        conversationId: conversationId || null,
        conversationTitle: title || null,
      },
    });
  }

  async function check({ force = false } = {}) {
    if (running) {
      rerun = { force: force || (rerun && rerun.force) };
      return { ok: true, skipped: 'busy' };
    }
    running = true;
    try {
      const m = location.pathname.match(CHAT_PATH_RE);
      if (!m) {
        sendContext(null, null, null);
        return { ok: true };
      }
      const conversationId = m[1];
      orgId = orgId || findOrgId();
      if (!orgId) {
        console.debug('[Polaris] Claude-Organisation noch nicht erkannt');
        return { ok: false, error: 'Organisation unbekannt' };
      }
      const conv = await getJson(
        `/api/organizations/${orgId}/chat_conversations/${conversationId}?tree=True&rendering_mode=messages&render_all_tools=true`
      );
      if (!conv.project_uuid) {
        sendContext(null, conversationId, conv.name);
        return { ok: true };
      }
      const project = { id: `claude-p-${conv.project_uuid}`, name: await getProjectName(conv.project_uuid) };
      await ensureProjectRegistered(project.id, project.name);
      sendContext(project, conversationId, conv.name);

      const projSettings = settings.projects[project.id];
      if (!projSettings || !projSettings.syncEnabled || settings.excludedConversations[conversationId]) {
        console.debug('[Polaris] Sync nicht aktiv fuer Claude-Projekt %s', project.name);
        return { ok: true };
      }

      const branch = activeBranch(conv);
      const now = Date.now();
      const capturedAt = new Date().toISOString();
      const events = [];
      let parent = null;
      branch.forEach((msg, i) => {
        const role = msg.sender === 'human' ? 'user' : msg.sender === 'assistant' ? 'assistant' : null;
        const isLast = i === branch.length - 1;
        const fresh = now - Date.parse(msg.updated_at || msg.created_at || 0) < SETTLE_MS;
        // Auch bei "Sync now" keine laufende Antwort: einmal exportiert gilt die
        // ID als erledigt, der fertige Text kaeme nie nach (Review 2026-09-29).
        if (!role || (isLast && role === 'assistant' && fresh)) return;
        const content = textOf(msg);
        if (!content) return;
        const files = fileNames(msg);
        events.push({
          schema_version: SCHEMA_VERSION,
          source: 'claude',
          project,
          conversation: { id: conversationId, title: conv.name || null, url: `https://claude.ai/chat/${conversationId}` },
          message: {
            id: msg.uuid,
            id_source: 'native',
            parent_id: parent,
            parent_id_source: parent ? 'claude-api' : null,
            role,
            source_timestamp: msg.created_at || null,
            content,
            ...(files.length ? { attachments: files } : {}),
          },
          captured_at: capturedAt,
        });
        parent = msg.uuid;
      });
      if (!events.length) return { ok: true };
      const res = await api().runtime.sendMessage({ type: 'export-events', events });
      if (res && res.ok) {
        console.debug(
          '[Polaris] Claude: %d neue Nachricht(en) exportiert (%d direkt an die Bridge, %d per Download), %d bereits frueher exportiert',
          res.downloaded, res.native || 0, res.fallbackDownloads || 0, res.skipped
        );
      } else {
        console.error('[Polaris] Claude-Export fehlgeschlagen:', res && res.error);
      }
      return res || { ok: false };
    } catch (err) {
      console.error('[Polaris] Claude:', err);
      return { ok: false, error: String(err) };
    } finally {
      running = false;
      if (rerun) {
        const again = rerun;
        rerun = null;
        setTimeout(() => check(again), 0);
      }
    }
  }

  // "Sync now" waehrend eines laufenden Checks: dessen Ende abwarten und dann
  // selbst pruefen, statt "busy" als Erfolg zu melden (Review Runde 2).
  async function syncNow() {
    for (let i = 0; i < 50 && running; i++) await new Promise((r) => setTimeout(r, 200));
    return check({ force: true });
  }

  api().runtime.onMessage.addListener((msg) => {
    if (msg.type === 'sync-now') return syncNow();
    if (msg.type === 'settings-changed') loadSettings();
    return undefined;
  });

  api().storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    if (changes.projects) settings.projects = changes.projects.newValue || {};
    if (changes.excludedConversations) settings.excludedConversations = changes.excludedConversations.newValue || {};
  });

  (async () => {
    await loadSettings();
    setTimeout(() => check(), 3000); // App laedt die Unterhaltung erst nach dem Seitenaufbau
    // Unterhaltungswechsel (SPA) schnell erkennen, sonst alle 15 s nachsehen.
    setInterval(() => {
      if (location.href !== lastHref) {
        lastHref = location.href;
        setTimeout(() => check(), 2000);
      }
    }, 1000);
    setInterval(() => {
      if (document.visibilityState === 'visible') check();
    }, CHECK_INTERVAL_MS);
  })();
})();
