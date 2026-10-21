// Chat-Abbild-Weg (Extension 0.4.0): die Extension holt das rohe
// Unterhaltungs-JSON und reicht es unveraendert an die Bridge; Turns,
// Projekt und "fertig" entscheidet nur die Bridge (bridge/polaris/snapshot.py).

import test from 'node:test';
import assert from 'node:assert/strict';
import { createPage, fakeServer, conversationJson } from './harness.mjs';

const ABC = '/g/g-p-abc/c/convabc';
const PROJECTS = { 'g-p-abc': true, 'g-p-lau': true };

async function apiPage(conversations, { turns = [{ id: 'b1' }, { id: 'b2' }], path = ABC, projects = PROJECTS } = {}) {
  const server = fakeServer(conversations);
  const page = createPage({ path, turns, projects, chatgptApi: true, fetchImpl: server.fetch });
  await page.start();
  await page.advance(6000);
  return { page, server };
}

test('Abbild wird roh uebergeben, mit Adress-Hinweis; keine Einzel-Events', async () => {
  const conv = conversationJson([{ id: 'b1' }, { id: 'b2', role: 'assistant' }], { gizmoId: 'g-p-abc', id: 'convabc' });
  const { page } = await apiPage({ convabc: conv });
  assert.equal(page.snapshots.length, 1);
  const s = page.snapshots[0];
  assert.equal(s.polaris_snapshot, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(s.hint)), { project_id: 'g-p-abc', conversation_id: 'convabc' });
  assert.deepEqual(Object.keys(s.conversation.mapping).sort(), Object.keys(conv.mapping).sort());
  assert.deepEqual(page.takeExported(), []);
});

test('Unveraendert (update_time) wird nicht erneut gesendet, Aenderung schon', async () => {
  const convs = { convabc: conversationJson([{ id: 'b1' }], { updateTime: 1 }) };
  const { page } = await apiPage(convs);
  assert.equal(page.snapshots.length, 1);
  await page.advance(25000); // Abfrage ohne DOM-Aenderung (Poll)
  assert.equal(page.snapshots.length, 1);
  convs.convabc = conversationJson([{ id: 'b1' }, { id: 'b2', role: 'assistant' }], { updateTime: 2 });
  await page.advance(25000);
  assert.equal(page.snapshots.length, 2);
});

test('Haengender Stop-Knopf blockiert den Abbild-Weg nicht', async () => {
  const server = fakeServer({ convabc: conversationJson([{ id: 'b1' }]) });
  const page = createPage({ path: ABC, turns: [{ id: 'b1' }], projects: PROJECTS, chatgptApi: true, fetchImpl: server.fetch, streaming: true });
  await page.start();
  await page.advance(6000);
  assert.equal(page.snapshots.length, 1);
});

test('Unbekanntes Antwortformat: Rueckfall auf den DOM-Weg, Status ans Popup', async () => {
  const { page } = await apiPage({ convabc: {} }); // Antwort ohne mapping
  assert.deepEqual(page.takeExported(), ['g-p-abc/convabc/b1', 'g-p-abc/convabc/b2']);
  const st = page.messages.filter((m) => m.type === 'api-status').map((m) => m.status).filter(Boolean);
  assert.equal(st[st.length - 1].ok, false);
});

test('HTTP 429/5xx (0.4.1): kein Auslesen der Seite, spaeter erneuter Abruf', async () => {
  const convs = { convabc: 429 };
  const { page, server } = await apiPage(convs);
  assert.deepEqual(page.takeExported(), []);            // nicht aus der Seite
  assert.equal(page.snapshots.length, 0);
  const calls = server.calls.filter((u) => u.includes('/conversation/')).length;
  await page.advance(10000);                             // innerhalb der Wartezeit: kein Abruf
  assert.equal(server.calls.filter((u) => u.includes('/conversation/')).length, calls);
  convs.convabc = conversationJson([{ id: 'b1' }], { gizmoId: 'g-p-abc', id: 'convabc' });
  await page.advance(40000);                             // nach der Wartezeit: klappt
  assert.equal(page.snapshots.length, 1);
  assert.deepEqual(page.takeExported(), []);
});

test('Schnelles Durchklicken (0.4.1): kurz besuchter Chat wird nicht abgerufen', async () => {
  const convs = {
    convabc: conversationJson([{ id: 'b1' }], { id: 'convabc' }),
    convlau: conversationJson([{ id: 'l1' }], { id: 'convlau' }),
  };
  const { page, server } = await apiPage(convs);
  await page.navigate('/g/g-p-lau/c/convlau');
  await page.advance(2000);                              // nur 2 s auf Falke
  await page.navigate(ABC);
  await page.advance(20000);
  assert.equal(server.calls.filter((u) => u.endsWith('/convlau')).length, 0);
  assert.deepEqual(page.takeExported(), []);
});

test('Antwort fuer einen anderen Chat wird nicht als dieser uebergeben', async () => {
  const { page } = await apiPage({ convabc: conversationJson([{ id: 'x1' }], { id: 'andererchat' }) });
  assert.equal(page.snapshots.length, 0);
});

test('Chatwechsel: Abbild des neuen Chats kommt sofort, trotz altem DOM', async () => {
  const convs = {
    convabc: conversationJson([{ id: 'b1' }], { id: 'convabc' }),
    convlau: conversationJson([{ id: 'l1' }], { id: 'convlau' }),
  };
  const { page } = await apiPage(convs);
  await page.navigate('/g/g-p-lau/c/convlau'); // DOM zeigt noch ABC
  await page.advance(9000); // 0.4.1: erst nach kurzem Verweilen
  const last = page.snapshots[page.snapshots.length - 1];
  assert.equal(last.hint.conversation_id, 'convlau');
  assert.equal(last.conversation.conversation_id, 'convlau');
  assert.deepEqual(page.takeExported(), []);
});

test('Navigation waehrend des Abrufs: Abbild und Hinweis gehoeren zusammen', async () => {
  let release;
  const convs = {
    convabc: conversationJson([{ id: 'b1' }], { id: 'convabc' }),
    convlau: conversationJson([{ id: 'l1' }], { id: 'convlau' }),
  };
  const server = fakeServer(convs);
  const page = createPage({ path: ABC, turns: [{ id: 'b1' }], projects: PROJECTS, chatgptApi: true, fetchImpl: server.fetch });
  server.delay = new Promise((r) => { release = r; });
  await page.start();
  await page.navigate('/g/g-p-lau/c/convlau');
  server.delay = null;
  release();
  await page.advance(12000);
  for (const s of page.snapshots) assert.equal(s.hint.conversation_id, s.conversation.conversation_id);
});

test('Sync now holt sofort und meldet ehrlich', async () => {
  const { page } = await apiPage({ convabc: conversationJson([{ id: 'b1' }]) });
  const before = page.snapshots.length;
  await page.syncNow();
  assert.equal(page.snapshots.length, before + 1); // erzwungen, trotz gleicher Version
});

test('Abgelaufenes Token wird einmal erneuert', async () => {
  const convs = { convabc: conversationJson([{ id: 'b1' }], { updateTime: 1 }) };
  const { page, server } = await apiPage(convs);
  server.token = 'neu';
  convs.convabc = conversationJson([{ id: 'b1' }, { id: 'b2' }], { updateTime: 2 });
  await page.advance(25000);
  assert.equal(page.snapshots.length, 2);
});

test('Schalter aus: Schnittstelle wird nie abgefragt', async () => {
  const server = fakeServer({ convabc: conversationJson([{ id: 'b1' }]) });
  const page = createPage({ path: ABC, turns: [{ id: 'b1' }], projects: PROJECTS, fetchImpl: server.fetch });
  await page.start();
  await page.advance(6000);
  assert.deepEqual(server.calls, []);
  assert.deepEqual(page.takeExported(), ['g-p-abc/convabc/b1']);
});

test('Sync aus fuer das Projekt: kein Abruf', async () => {
  const { page, server } = await apiPage({ convabc: conversationJson([{ id: 'b1' }]) }, { projects: { 'g-p-abc': false } });
  assert.equal(page.snapshots.length, 0);
  assert.deepEqual(server.calls, []);
});

test('DOM-Rueckfall schickt alle IDs einer Such-Einheit als member_ids', async () => {
  const { page } = await apiPage({ convabc: {} }, { turns: [{ id: 'b1' }, { id: 'a1', role: 'user', memberIds: ['a1', 'a2'] }] });
  const events = page.takeEvents();
  const e = events.find((x) => x.message.id === 'a1');
  assert.deepEqual(JSON.parse(JSON.stringify(e.message.member_ids)), ['a1', 'a2']);
});

test('Ruhender Chat wird einmal erneut gesendet (letzte Antwort freigeben)', async () => {
  const now = 1000; // Harness-Uhr startet bei 1 000 000 ms
  const convs = { convabc: conversationJson([{ id: 'b1' }], { updateTime: now - 30 }) };
  const { page } = await apiPage(convs);
  assert.equal(page.snapshots.length, 1);
  await page.advance(200000); // > 190 s ohne Aenderung
  assert.equal(page.snapshots.length, 2);
  await page.advance(100000);
  assert.equal(page.snapshots.length, 2); // nur einmal
});
