// Tests fuer extension/claude.js (Claude-Erfassung ueber die Web-Schnittstelle).

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const SRC = fs.readFileSync(new URL('../../extension/claude.js', import.meta.url), 'utf8');
const ORG = '11111111-1111-1111-1111-111111111111';
const PROJ = '22222222-2222-2222-2222-222222222222';
const C1 = 'aaaaaaaa-0000-0000-0000-000000000001';
const C2 = 'aaaaaaaa-0000-0000-0000-000000000002';

function conv(id, messages, { project = PROJ, name = 'Chat' } = {}) {
  let parent = '00000000-0000-4000-8000-000000000000';
  const chat_messages = messages.map((m, i) => {
    const msg = {
      uuid: m.id, sender: m.sender || 'human', parent_message_uuid: parent, index: i,
      created_at: m.at || '2026-09-29T10:00:00Z', updated_at: m.at || '2026-09-29T10:00:00Z',
      content: [{ type: 'text', text: m.text ?? `Text ${m.id}` }],
    };
    parent = m.id;
    return msg;
  });
  return { uuid: id, name, project_uuid: project, current_leaf_message_uuid: parent, chat_messages };
}

function env({ path = `/chat/${C1}`, conversations = {}, projects = { [`claude-p-${PROJ}`]: true }, now = Date.parse('2026-09-29T12:00:00Z') } = {}) {
  const clock = { now };
  let timers = [];
  let nextId = 1;
  const exported = [];
  const contexts = [];
  const listeners = [];
  const pending = []; // manuell freizugebende fetch-Antworten
  const state = { path, holdFetch: false };
  class FakeDate extends Date {
    constructor(...a) { super(...(a.length ? a : [clock.now])); }
    static now() { return clock.now; }
  }
  const storageData = {
    projects: Object.fromEntries(Object.entries(projects).map(([id, on]) => [id, { name: id, syncEnabled: on, lastExport: null }])),
    excludedConversations: {},
  };
  const respond = (url) => {
    const m = url.match(/chat_conversations\/([0-9a-f-]{36})/);
    if (m && conversations[m[1]]) return { ok: true, status: 200, json: async () => structuredClone(conversations[m[1]]) };
    if (/\/projects\//.test(url)) return { ok: true, status: 200, json: async () => ({ name: 'COO' }) };
    return { ok: false, status: 404, json: async () => ({}) };
  };
  const sandbox = {
    location: { get pathname() { return state.path; }, get href() { return 'https://claude.ai' + state.path; } },
    document: { cookie: '', visibilityState: 'visible' },
    performance: { getEntriesByType: () => [{ name: `https://claude.ai/api/organizations/${ORG}/bootstrap` }] },
    fetch: (url) => {
      if (!state.holdFetch) return Promise.resolve(respond(url));
      return new Promise((resolve) => pending.push(() => resolve(respond(url))));
    },
    chrome: {
      runtime: {
        async sendMessage(msg) {
          if (msg.type === 'export-events') {
            exported.push(...msg.events);
            return { ok: true, downloaded: msg.events.length, skipped: 0 };
          }
          if (msg.type === 'context-update') contexts.push(msg.context);
          return {};
        },
        onMessage: { addListener: (fn) => listeners.push(fn) },
      },
      storage: {
        local: { get: async (d) => ({ ...d, ...structuredClone(storageData) }), set: async (p) => Object.assign(storageData, p) },
        onChanged: { addListener() {} },
      },
    },
    setTimeout: (fn, ms = 0) => { const id = nextId++; timers.push({ id, fn, at: clock.now + ms }); return id; },
    clearTimeout: (id) => { timers = timers.filter((t) => t.id !== id); },
    setInterval: (fn, ms) => {
      const id = nextId++;
      const tick = () => timers.push({ id, fn: () => { tick(); fn(); }, at: clock.now + ms });
      tick();
      return id;
    },
    Date: FakeDate,
    console: { debug() {}, log() {}, warn() {}, error() {} },
  };
  vm.createContext(sandbox);
  const flush = () => new Promise((r) => setTimeout(r, 0));
  async function advance(ms) {
    const target = clock.now + ms;
    for (;;) {
      await flush(); await flush();
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
  return {
    exported, contexts, state, pending, conversations,
    async start() { vm.runInContext(SRC, sandbox); await advance(0); },
    advance,
    async syncNow() { await Promise.all(listeners.map((fn) => fn({ type: 'sync-now' }, {})).filter(Boolean)); await advance(0); },
    ids() { return exported.splice(0).map((e) => `${e.project.id}/${e.conversation.id}/${e.message.id}`); },
  };
}

test('Projekt kommt aus der Antwort, nicht aus der Seite; nur aktiver Strang', async () => {
  const e = env({ conversations: { [C1]: conv(C1, [{ id: 'h1' }, { id: 'a1', sender: 'assistant' }]) } });
  await e.start();
  await e.advance(4000);
  assert.deepEqual(e.ids(), [`claude-p-${PROJ}/${C1}/h1`, `claude-p-${PROJ}/${C1}/a1`]);
});

test('Chat ohne Projekt wird nie exportiert', async () => {
  const e = env({ conversations: { [C1]: conv(C1, [{ id: 'h1' }], { project: null }) } });
  await e.start();
  await e.advance(20000);
  assert.deepEqual(e.ids(), []);
});

test('Frische letzte Antwort wartet - auch bei "Sync now"', async () => {
  const fresh = '2026-09-29T11:59:50Z';
  const e = env({ conversations: { [C1]: conv(C1, [{ id: 'h1' }, { id: 'a1', sender: 'assistant', at: fresh }]) } });
  await e.start();
  await e.advance(4000);
  assert.deepEqual(e.ids(), [`claude-p-${PROJ}/${C1}/h1`]);
  await e.syncNow(); // schickt h1 erneut (Dedup macht background.js), a1 aber nicht
  assert.ok(!e.ids().some((x) => x.endsWith('/a1')));
  await e.advance(40000);
  assert.ok(e.ids().includes(`claude-p-${PROJ}/${C1}/a1`));
});

test('Spaete Antwort fuer den alten Chat setzt das Popup nicht auf dessen Projekt', async () => {
  const other = '33333333-3333-3333-3333-333333333333';
  const e = env({
    conversations: {
      [C1]: conv(C1, [{ id: 'h1' }]),
      [C2]: conv(C2, [{ id: 'h2' }], { project: other }),
    },
    projects: { [`claude-p-${PROJ}`]: true, [`claude-p-${other}`]: true },
  });
  e.state.holdFetch = true;
  await e.start();
  await e.advance(3500); // erster Check laeuft, Antwort haengt
  e.state.path = `/chat/${C2}`; // Nutzer wechselt
  e.contexts.length = 0;
  e.state.holdFetch = false;
  while (e.pending.length) e.pending.shift()();
  await e.advance(0);
  assert.ok(e.contexts.every((c) => c.conversationId !== C1), JSON.stringify(e.contexts));
  // der verpasste Check fuer C2 wird nachgeholt
  await e.advance(4000);
  const ids = e.ids();
  assert.ok(ids.includes(`claude-p-${other}/${C2}/h2`), ids.join(','));
  // Daten des alten Chats landen trotzdem korrekt unter seinem eigenen Projekt
  assert.ok(ids.filter((x) => x.includes(C1)).every((x) => x.startsWith(`claude-p-${PROJ}/`)));
});
