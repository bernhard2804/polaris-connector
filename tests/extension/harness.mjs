// Laeuft extension/content.js in einer vm-Sandbox mit nachgebautem Browser:
// steuerbare Uhr und Timer, Adresse, DOM-Turns (ueber einen PolarisExtractor-
// Ersatz), MutationObserver und fetch. Keine Abhaengigkeiten ausser Node.
//
// Anlass (2026-09-29): Nach einem Chatwechsel exportierte content.js die noch
// sichtbaren Nachrichten des alten Chats unter der neuen Adresse (ABC <->
// Falke). Fuer content.js gab es bis dahin keinen einzigen Test.

import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { webcrypto } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
// POLARIS_CONTENT_JS: anderen Stand pruefen (z. B. ob ein Test den alten Fehler erkennt).
const CONTENT_JS = process.env.POLARIS_CONTENT_JS || path.resolve(here, '../../extension/content.js');
const API_JS = path.resolve(here, '../../extension/chatgpt_api.js');

// Nachgebauter ChatGPT-Server fuer den API-Modus: conversations = { <id>:
// Antwort-JSON | HTTP-Status }. Zaehlt Abrufe in server.calls.
export function fakeServer(conversations, { token = 'tok' } = {}) {
  const server = { calls: [], token, delay: null };
  server.fetch = async (url, opts = {}) => {
    server.calls.push(url);
    if (server.delay) await server.delay;
    if (url === '/api/auth/session') {
      return { ok: true, status: 200, json: async () => ({ accessToken: server.token, expires: '2099-01-01T00:00:00Z' }) };
    }
    const m = url.match(/^\/backend-api\/conversation\/([^/?]+)$/);
    const body = m && conversations[m[1]];
    if (!m || body === undefined) return { ok: false, status: 404, json: async () => ({}) };
    if (typeof body === 'number') return { ok: false, status: body, json: async () => ({}) };
    if ((opts.headers || {}).authorization !== `Bearer ${server.token}`) return { ok: false, status: 401, json: async () => ({}) };
    return { ok: true, status: 200, json: async () => structuredClone(body) };
  };
  return server;
}

// Baut eine Schnittstellen-Antwort aus einer Liste
// [{id, role, text, recipient?, status?, hidden?}] als linearen Strang.
export function conversationJson(messages, { title = 'Titel', gizmoId = null, id = null, updateTime = 1 } = {}) {
  const mapping = { root: { id: 'root', parent: null, children: [], message: null } };
  let parent = 'root';
  for (const m of messages) {
    mapping[m.id] = {
      id: m.id,
      parent,
      children: [],
      message: {
        id: m.id,
        author: { role: m.role || 'user' },
        recipient: m.recipient || 'all',
        status: m.status || 'finished_successfully',
        content: { content_type: m.type || 'text', parts: [m.text ?? `Text ${m.id}`] },
        metadata: m.hidden ? { is_visually_hidden_from_conversation: true } : {},
      },
    };
    mapping[parent].children.push(m.id);
    parent = m.id;
  }
  return {
    title, current_node: parent, mapping, update_time: updateTime,
    ...(id ? { conversation_id: id } : {}), ...(gizmoId ? { gizmo_id: gizmoId } : {}),
  };
}

export function createPage({
  path: initialPath,
  turns = [],
  projects = {},
  fetchImpl = null,
  streaming = false,
  chatgptApi = false, // Tests der DOM-Erfassung schalten die Schnittstelle aus
} = {}) {
  const clock = { now: 1_000_000 };
  let timers = [];
  let nextTimer = 1;
  let observerCb = null;
  const exported = [];
  const snapshots = [];
  const listeners = [];
  const messages = [];
  const logs = [];
  const state = { turns: [...turns], path: initialPath, streaming, title: 't' };

  const location = {
    get pathname() {
      return state.path;
    },
    get href() {
      return 'https://chatgpt.com' + state.path;
    },
  };

  class FakeDate extends Date {
    constructor(...args) {
      super(...(args.length ? args : [clock.now]));
    }
    static now() {
      return clock.now;
    }
  }

  const PolarisExtractor = {
    getProjectInfo() {
      const m = state.path.match(/^\/g\/(g-p-[a-zA-Z0-9]+)/);
      return m ? { id: m[1], name: m[1] } : null;
    },
    getConversationId() {
      const m = state.path.match(/\/c\/([a-zA-Z0-9-]+)/);
      return m ? m[1] : null;
    },
    getConversationTitle() {
      return state.title;
    },
    isStreaming() {
      return state.streaming;
    },
    getTurns() {
      return state.turns.map((t) => ({
        role: t.role || 'user',
        nativeId: t.id,
        memberIds: t.memberIds || (t.id ? [t.id] : []),
        node: { innerText: t.text ?? `Text ${t.id}` },
        attachments: [],
      }));
    },
    getTurnContent(node) {
      return node.innerText;
    },
  };

  const runtime = {
    async sendMessage(msg) {
      messages.push(msg);
      if (msg.type === 'export-events') {
        exported.push(...msg.events);
        return { ok: true, downloaded: msg.events.length, skipped: 0 };
      }
      if (msg.type === 'export-snapshot') {
        snapshots.push(msg.snapshot);
        return { ok: true, counts: {} };
      }
      return {};
    },
    onMessage: {
      addListener(fn) {
        listeners.push(fn);
      },
    },
  };
  const storageData = {
    projects: Object.fromEntries(
      Object.entries(projects).map(([id, syncEnabled]) => [id, { name: id, syncEnabled, lastExport: null }])
    ),
    excludedConversations: {},
    chatgptApi,
  };
  const chrome = {
    runtime,
    storage: {
      local: {
        async get(defaults) {
          return { ...defaults, ...structuredClone(storageData) };
        },
        async set(partial) {
          Object.assign(storageData, structuredClone(partial));
        },
      },
      onChanged: { addListener() {} },
    },
  };

  const sandbox = {
    chrome,
    location,
    document: {
      body: {},
      get title() {
        return state.title;
      },
      cookie: '',
    },
    MutationObserver: class {
      constructor(cb) {
        observerCb = cb;
      }
      observe() {}
    },
    setTimeout(fn, ms = 0) {
      const id = nextTimer++;
      timers.push({ id, fn, at: clock.now + ms });
      return id;
    },
    clearTimeout(id) {
      timers = timers.filter((t) => t.id !== id);
    },
    // Echte Wiederholung: content.js erkennt Navigation u. a. per 700-ms-
    // Intervall. Ein No-op-Intervall liess den Test fuer den Vorfall
    // 2026-09-29 auch mit dem fehlerhaften Stand bestehen.
    setInterval(fn, ms = 0) {
      const id = nextTimer++;
      const tick = () => {
        timers.push({ id, fn: () => { tick(); fn(); }, at: clock.now + ms });
      };
      tick();
      return id;
    },
    clearInterval(id) {
      timers = timers.filter((t) => t.id !== id);
    },
    crypto: webcrypto,
    TextEncoder,
    Date: FakeDate,
    PolarisExtractor,
    console: {
      debug: (...a) => logs.push(['debug', ...a]),
      log: (...a) => logs.push(['log', ...a]),
      warn: (...a) => logs.push(['warn', ...a]),
      error: (...a) => logs.push(['error', ...a]),
    },
    fetch: fetchImpl || (async () => ({ ok: false, status: 404, json: async () => ({}) })),
  };
  vm.createContext(sandbox);

  // Echte Makrotasks abwarten: crypto.subtle.digest (Ersatz-IDs) loest
  // nicht innerhalb von ein, zwei Mikrotasks auf (Review 2026-09-29).
  const flush = () => new Promise((r) => setTimeout(r, 0));

  async function advance(ms) {
    const target = clock.now + ms;
    for (;;) {
      await flush();
      await flush();
      timers.sort((a, b) => a.at - b.at);
      const next = timers[0];
      if (!next || next.at > target) break;
      timers.shift();
      clock.now = Math.max(clock.now, next.at);
      next.fn();
    }
    clock.now = target;
    for (let i = 0; i < 5; i++) await flush();
  }

  const page = {
    exported,
    snapshots,
    messages,
    logs,
    async start() {
      // Wie im Manifest: chatgpt_api.js vor content.js. const-Bindungen
      // oberster Ebene sind in vm nur im selben Skript sichtbar, daher als
      // globalThis-Eigenschaft weiterreichen.
      vm.runInContext(fs.readFileSync(API_JS, 'utf8') + '\nglobalThis.PolarisChatGptApi = PolarisChatGptApi;', sandbox, {
        filename: 'chatgpt_api.js',
      });
      vm.runInContext(fs.readFileSync(CONTENT_JS, 'utf8'), sandbox, { filename: 'content.js' });
      await advance(0);
    },
    // ChatGPT: Adresse wechselt, DOM (noch) nicht - danach loest ein
    // DOM-Tick (MutationObserver) aus.
    async navigate(p) {
      state.path = p;
      if (observerCb) observerCb([]);
      await advance(0);
    },
    async setTurns(list) {
      state.turns = [...list];
      if (observerCb) observerCb([]);
      await advance(0);
    },
    setStreaming(v) {
      state.streaming = v;
      if (observerCb) observerCb([]);
    },
    advance,
    // Wie der Klick auf "Sync now" im Popup.
    async syncNow() {
      const results = listeners.map((fn) => fn({ type: 'sync-now' }, {}));
      await Promise.all(results.filter(Boolean));
      await advance(0);
    },
    // Liefert "projekt/chat/id" und prueft dabei fuer JEDES Event, dass URL,
    // Projekt und Chat zusammenpassen (sonst meldet der Curator url_mismatch).
    takeExported() {
      const events = exported.splice(0);
      for (const e of events) {
        const url = e.conversation.url || '';
        if (!url.includes(`/c/${e.conversation.id}`) || !url.includes(`/g/${e.project.id}`)) {
          throw new Error(`URL passt nicht zum Event: ${url} fuer ${e.project.id}/${e.conversation.id}`);
        }
      }
      return events.map((e) => `${e.project.id}/${e.conversation.id}/${e.message.id}`);
    },
    takeEvents() {
      return exported.splice(0);
    },
  };
  return page;
}
