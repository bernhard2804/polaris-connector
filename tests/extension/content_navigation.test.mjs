// Regressionstests fuer extension/content.js (Vorfall 2026-09-29).
// Ausfuehren: node --test tests/extension/   (oder ueber tests/test_extension_js.py)

import test from 'node:test';
import assert from 'node:assert/strict';
import { createPage } from './harness.mjs';

const ABC = '/g/g-p-abc/c/convabc';
const FALKE = '/g/g-p-lau/c/convlau';
const PROJECTS = { 'g-p-abc': true, 'g-p-lau': true };

async function abcPage() {
  const page = createPage({ path: ABC, turns: [{ id: 'b1' }, { id: 'b2' }], projects: PROJECTS });
  await page.start();
  await page.advance(6000);
  assert.deepEqual(page.takeExported(), ['g-p-abc/convabc/b1', 'g-p-abc/convabc/b2']);
  return page;
}

test('Chatwechsel: alter DOM wird nicht unter der neuen Adresse exportiert', async () => {
  const page = await abcPage();
  await page.navigate(FALKE); // DOM zeigt noch ABC
  await page.advance(6000);
  assert.deepEqual(page.takeExported(), []);

  await page.setTurns([{ id: 'l1' }, { id: 'l2' }]);
  await page.advance(6000);
  assert.deepEqual(page.takeExported(), ['g-p-lau/convlau/l1', 'g-p-lau/convlau/l2']);
});

test('Projektseite ohne Chat-ID exportiert nichts, danach geht es normal weiter', async () => {
  const page = await abcPage();
  await page.navigate('/g/g-p-abc/project');
  await page.advance(6000);
  await page.setTurns([{ id: 'b1' }, { id: 'x9' }]);
  await page.advance(6000);
  assert.deepEqual(page.takeExported(), []);
  await page.navigate(FALKE);
  await page.advance(1000); // Wechsel erkannt, DOM zeigt noch das Alte
  await page.setTurns([{ id: 'l1' }]);
  await page.advance(6000);
  assert.deepEqual(page.takeExported(), ['g-p-lau/convlau/l1']);
});

test('DOM wechselt im selben Moment wie die Adresse: verzoegert, aber nicht verloren', async () => {
  const page = await abcPage();
  await page.navigate(FALKE);
  await page.setTurns([{ id: 'l1' }]); // vor dem Erkennen des Wechsels
  await page.advance(6000);
  assert.deepEqual(page.takeExported(), []); // gilt vorerst als "stand schon da"
  await page.advance(20000);
  assert.deepEqual(page.takeExported(), ['g-p-lau/convlau/l1']);
});

test('Umweg ueber die Projektseite: IDs des ersten Chats bleiben gesperrt', async () => {
  const page = await abcPage();
  await page.navigate('/g/g-p-abc/project');
  await page.advance(6000);
  await page.navigate(FALKE); // DOM zeigt weiter ABC
  await page.advance(30000);
  assert.deepEqual(page.takeExported(), []);
  await page.setTurns([{ id: 'l1' }, { id: 'l2' }]);
  await page.advance(6000);
  assert.deepEqual(page.takeExported(), ['g-p-lau/convlau/l1', 'g-p-lau/convlau/l2']);
});

test('Nicht synchronisierter Chat: seine IDs werden trotzdem gemerkt', async () => {
  const page = createPage({
    path: '/g/g-p-priv/c/convpriv',
    turns: [{ id: 'p1' }],
    projects: { 'g-p-priv': false, 'g-p-lau': true },
  });
  await page.start();
  await page.advance(6000);
  assert.deepEqual(page.takeExported(), []);
  await page.navigate(FALKE); // DOM zeigt noch den privaten Chat
  await page.advance(30000);
  assert.deepEqual(page.takeExported(), []);
});

test('Nach der Frist werden fremde IDs einzeln aussortiert, neue exportiert', async () => {
  const page = await abcPage();
  await page.navigate('/g/g-p-abc/c/convabc2');
  await page.advance(25000); // DOM haengt ueber die Frist hinaus
  assert.deepEqual(page.takeExported(), []);
  await page.setTurns([{ id: 'b1' }, { id: 'n1' }]);
  await page.advance(6000);
  assert.deepEqual(page.takeExported(), ['g-p-abc/convabc2/n1']);
  assert.ok(page.logs.some(([lvl, msg]) => lvl === 'warn' && String(msg).includes('anderen Chat')));
});

test('Normaler Verlauf: neue Nachrichten im selben Chat werden exportiert', async () => {
  const page = await abcPage();
  await page.setTurns([{ id: 'b1' }, { id: 'b2' }, { id: 'b3', role: 'user' }]);
  await page.advance(6000);
  assert.deepEqual(page.takeExported(), ['g-p-abc/convabc/b3']);
});

test('Kurzer Besuch: Chat A vor dem ersten Scan verlassen, A landet nicht unter B', async () => {
  // Review 2026-09-29: X -> A -> B innerhalb der 1,5-s-Entprellung.
  const page = await abcPage();
  await page.navigate('/g/g-p-lau/c/conva');
  await page.setTurns([{ id: 'a1' }, { id: 'a2' }]); // A erscheint ...
  await page.navigate('/g/g-p-lau/c/convb'); // ... und sofort weiter zu B
  await page.advance(6000);
  assert.deepEqual(page.takeExported(), []);
  await page.setTurns([{ id: 'x1' }]);
  await page.advance(6000);
  assert.deepEqual(page.takeExported(), ['g-p-lau/convb/x1']);
});

test('Veraltete Nachrichten ohne native ID werden am Text erkannt', async () => {
  const page = createPage({ path: ABC, turns: [{ id: null, text: 'Geheim ABC' }], projects: PROJECTS });
  await page.start();
  await page.advance(6000);
  assert.equal(page.takeExported().length, 1);
  await page.navigate(FALKE); // DOM zeigt noch den ABC-Text
  await page.advance(30000);
  assert.deepEqual(page.takeExported(), []);
  await page.setTurns([{ id: null, text: 'Falke neu' }]);
  await page.advance(6000);
  const events = page.takeEvents();
  assert.equal(events.length, 1);
  assert.equal(events[0].conversation.id, 'convlau');
  assert.equal(events[0].message.content, 'Falke neu');
});

test('Wachsende Antwort: erst der fertige Text wird exportiert', async () => {
  const page = await abcPage();
  await page.setTurns([{ id: 'b1' }, { id: 'b2' }, { id: 'u3' }, { id: 'a3', role: 'assistant', text: 'Hal' }]);
  await page.advance(3000);
  await page.setTurns([{ id: 'b1' }, { id: 'b2' }, { id: 'u3' }, { id: 'a3', role: 'assistant', text: 'Hallo Welt' }]);
  await page.advance(3000);
  const early = page.takeEvents();
  assert.deepEqual(early.map((e) => e.message.id), ['u3']);
  await page.advance(10000);
  const late = page.takeEvents();
  assert.deepEqual(late.map((e) => [e.message.id, e.message.content]), [['a3', 'Hallo Welt']]);
});

test('Sync now waehrend einer laufenden Antwort exportiert sie nicht halb', async () => {
  const page = await abcPage();
  page.setStreaming(true);
  await page.setTurns([{ id: 'b1' }, { id: 'b2' }, { id: 'a3', role: 'assistant', text: 'Teil' }]);
  await page.syncNow();
  assert.deepEqual(page.takeExported(), []);
  page.setStreaming(false);
  await page.setTurns([{ id: 'b1' }, { id: 'b2' }, { id: 'a3', role: 'assistant', text: 'Teil und Rest' }]);
  await page.advance(12000);
  const events = page.takeEvents();
  assert.deepEqual(events.map((e) => e.message.content), ['Teil und Rest']);
});
