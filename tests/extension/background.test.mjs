// Tests fuer extension/background.js: Uebergabe an den Native Host,
// Download-Rueckfall und die "schon exportiert"-Liste.

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const SRC = fs.readFileSync(new URL('../../extension/background.js', import.meta.url), 'utf8');

function ev(conv, id) {
  return { schema_version: 1, source: 'chatgpt', project: { id: 'g-p-1', name: 'P' }, conversation: { id: conv }, message: { id, role: 'user', content: 'x' }, captured_at: '2026-09-29T10:00:00Z' };
}

function env({ nativeAccepts = (events) => events.map((e) => e.message.id), nativeAvailable = true, snapshotReply = { ok: true } } = {}) {
  const storage = {};
  const posted = [];
  const downloads = [];
  let messageListener = null;
  const dlListeners = [];
  const sandbox = {
    console: { debug() {}, log() {}, warn() {}, error() {} },
    setTimeout: (fn, ms) => setTimeout(fn, Math.min(ms, 5)), clearTimeout,
    setInterval: () => 0, clearInterval() {},
    Blob: class { constructor(parts) { this.parts = parts; } },
    URL: { createObjectURL: () => 'blob:x', revokeObjectURL() {} },
    chrome: {
      runtime: {
        onMessage: { addListener: (fn) => { messageListener = fn; } },
        onInstalled: { addListener() {} }, onStartup: { addListener() {} },
        lastError: null,
        connectNative() {
          if (!nativeAvailable) throw new Error('kein Host');
          const onMsg = [];
          return {
            onMessage: { addListener: (fn) => onMsg.push(fn) },
            onDisconnect: { addListener() {} },
            postMessage(msg) {
              posted.push(msg);
              const accepted = msg.type === 'export-events' ? nativeAccepts(msg.events) : [];
              const reply = msg.type === 'export-snapshot' ? { id: msg.id, ...snapshotReply } : { id: msg.id, ok: true, accepted, counts: {} };
              setTimeout(() => onMsg.forEach((fn) => fn(reply)), 1);
            },
            disconnect() {},
          };
        },
      },
      storage: {
        local: {
          async get(d) {
            const out = typeof d === 'object' && !Array.isArray(d) ? { ...d } : {};
            for (const k of Object.keys(out)) if (k in storage) out[k] = structuredClone(storage[k]);
            return out;
          },
          async set(p) {
            await new Promise((r) => setTimeout(r, 2)); // Schreiben dauert (Wettlauf sichtbar machen)
            Object.assign(storage, structuredClone(p));
          },
        },
        onChanged: { addListener() {} },
      },
      downloads: {
        async download(opts) {
          const id = downloads.length + 1;
          downloads.push(opts.filename);
          setTimeout(() => dlListeners.forEach((fn) => fn({ id, state: { current: 'complete' } })), 1);
          return id;
        },
        onChanged: { addListener: (fn) => dlListeners.push(fn), removeListener() {} },
        erase: async () => {},
      },
      tabs: { onRemoved: { addListener() {} }, sendMessage: async () => ({}) },
      permissions: { contains: async () => true },
      action: { setBadgeText() {}, setBadgeBackgroundColor() {} },
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox);
  const wait = (ms = 30) => new Promise((r) => setTimeout(r, ms));
  return {
    posted, downloads, storage,
    async exportSnapshot(snapshot) {
      const res = await messageListener({ type: 'export-snapshot', snapshot }, { tab: { id: 1 } });
      await wait();
      return res;
    },
    async exportEvents(events) {
      const res = await messageListener({ type: 'export-events', events }, { tab: { id: 1 } });
      await wait();
      return res;
    },
    wait,
  };
}

test('Vom Native Host angenommene Nachrichten werden nicht erneut geschickt', async () => {
  const e = env();
  const r1 = await e.exportEvents([ev('c1', 'm1'), ev('c1', 'm2')]);
  assert.equal(r1.ok, true);
  assert.equal(r1.native, 2);
  const r2 = await e.exportEvents([ev('c1', 'm1'), ev('c1', 'm2')]);
  assert.equal(r2.downloaded, 0);
  assert.equal(e.posted.filter((m) => m.type === 'export-events').length, 1);
});

test('Teilweise angenommen: der Rest geht per Download, alles gilt danach als exportiert', async () => {
  const e = env({ nativeAccepts: (events) => [events[0].message.id] });
  await e.exportEvents([ev('c1', 'm1'), ev('c1', 'm2')]);
  assert.deepEqual(e.downloads, ['polaris-inbox/g-p-1__c1__m2.json']);
  const again = await e.exportEvents([ev('c1', 'm1'), ev('c1', 'm2')]);
  assert.equal(again.downloaded, 0);
});

test('Ohne Native Host: Download-Rueckfall', async () => {
  const e = env({ nativeAvailable: false });
  await e.exportEvents([ev('c1', 'm1')]);
  assert.deepEqual(e.downloads, ['polaris-inbox/g-p-1__c1__m1.json']);
});

test('Gleichzeitige Exporte verlieren keine "exportiert"-Eintraege', async () => {
  const e = env();
  await Promise.all([e.exportEvents([ev('c1', 'a')]), e.exportEvents([ev('c2', 'b')]), e.exportEvents([ev('c1', 'c')])]);
  await e.wait(50);
  const record = e.storage.exportedMessages || {};
  assert.deepEqual([...(record.c1?.ids || [])].sort(), ['a', 'c']);
  assert.deepEqual([...(record.c2?.ids || [])], ['b']);
});

test('Ablehnung eines Abbilds durch die Bridge wird gemeldet, nicht heruntergeladen', async () => {
  const e = env({ snapshotReply: { ok: false, error: 'snapshot-shape: ...' } });
  const res = await e.exportSnapshot({ polaris_snapshot: 1, conversation: { conversation_id: 'c1' }, hint: {} });
  assert.equal(res.ok, false);
  assert.match(res.error, /snapshot-shape/);
  assert.deepEqual(e.downloads, []);
});
