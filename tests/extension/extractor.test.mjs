// Tests fuer extension/extractor.js (bis 2026-09-29 ohne jede Abdeckung).

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { h } from './minidom.mjs';

const SRC = fs.readFileSync(new URL('../../extension/extractor.js', import.meta.url), 'utf8');
const plain = (x) => JSON.parse(JSON.stringify(x));

function extractor(pathname, body, title = 'Chat - ChatGPT') {
  const document = h('html', {}, [body]);
  document.title = title;
  const ctx = vm.createContext({ location: { pathname }, document });
  vm.runInContext(SRC + '\nglobalThis.X = PolarisExtractor;', ctx);
  return ctx.X;
}

function unit(role, ids, text, extra = []) {
  return h('div', { 'data-chatgpt-search-unit-key': `fallback-turn-1:2:${role}`, 'data-chatgpt-search-message-ids': ids }, [
    h('div', { 'data-chatgpt-selection-message-id': ids.split(' ')[0] }, [
      h('div', { 'data-markdown-text-style': `${role}-message` }, [], { text }),
      ...extra,
    ]),
  ]);
}

test('Projekt nur aus der Adresse; projektloser Chat bleibt ohne Projekt', () => {
  const sidebar = h('nav', {}, [h('a', { href: '/g/g-p-kunde-falke/project', 'aria-label': 'Open Falke project' })]);
  assert.equal(extractor('/c/abc', h('body', {}, [sidebar])).getProjectInfo(), null);
  const info = plain(extractor('/g/g-p-6878dc2b-falke/c/abc', h('body', {}, [sidebar])).getProjectInfo());
  assert.equal(info.id, 'g-p-6878dc2b');
});

test('Projektname nur von einem Link mit derselben Projekt-ID', () => {
  const body = h('body', {}, [
    h('a', { href: '/g/g-p-other-abc/project', 'aria-label': 'Open ABC project' }),
    h('a', { href: '/g/g-p-6878dc2b-falke/project', 'aria-label': 'Open Falke project' }),
  ]);
  assert.equal(extractor('/g/g-p-6878dc2b-falke/c/abc', body).getProjectInfo().name, 'Falke');
  // kein passender Link: Name aus dem Adress-Slug, nie der eines anderen Projekts
  const other = h('body', {}, [h('a', { href: '/g/g-p-other-abc/project', 'aria-label': 'Open ABC project' })]);
  assert.equal(extractor('/g/g-p-6878dc2b-falke/c/abc', other).getProjectInfo().name, 'falke');
});

test('Chat-ID aus der Adresse, auch bei Projekt-Chats', () => {
  const X = extractor('/g/g-p-1/c/6ab271de-770c-83ed', h('body'));
  assert.equal(X.getConversationId(), '6ab271de-770c-83ed');
  assert.equal(extractor('/g/g-p-1/project', h('body')).getConversationId(), null);
});

test('Aktuelles Markup: Rolle, erste ID, Text, keine doppelten verschachtelten Units', () => {
  const nested = unit('assistant', 'a1 a2', 'Antwort');
  nested.children[0].append(unit('assistant', 'a9', 'innen'));
  const body = h('body', {}, [unit('user', 'u1', 'Frage'), nested, unit('system', 's1', 'x')]);
  const X = extractor('/g/g-p-1/c/c1', body);
  const turns = X.getTurns();
  assert.deepEqual(plain(turns.map((t) => [t.role, t.nativeId])), [['user', 'u1'], ['assistant', 'a1']]);
  assert.equal(X.getTurnContent(turns[0].node), 'Frage');
});

test('Altes Markup hat Vorrang, wenn vorhanden', () => {
  const body = h('body', {}, [
    h('section', { 'data-testid': 'conversation-turn-1' }, [
      h('div', { 'data-message-id': 'm1', 'data-message-author-role': 'user' }, [], { text: 'alt' }),
    ]),
    unit('user', 'u1', 'neu'),
  ]);
  assert.deepEqual(plain(extractor('/g/g-p-1/c/c1', body).getTurns().map((t) => t.nativeId)), ['m1']);
});

test('Dateinamen hochgeladener Anhaenge, keine anderen Titel', () => {
  const files = [h('span', { title: 'Bericht.pdf' }), h('span', { title: 'Hilfe' }), h('span', { title: 'Tabelle.xlsx' })];
  const body = h('body', {}, [unit('user', 'u1', 'Anbei', files)]);
  const [t] = extractor('/g/g-p-1/c/c1', body).getTurns();
  assert.deepEqual(plain(t.attachments), ['Bericht.pdf', 'Tabelle.xlsx']);
});

test('Streaming nur bei sichtbarem Stop-Knopf', () => {
  const hidden = h('body', {}, [h('button', { 'data-testid': 'stop-button' }, [], { visible: false })]);
  assert.equal(extractor('/g/g-p-1/c/c1', hidden).isStreaming(), false);
  const shown = h('body', {}, [h('button', { 'aria-label': 'Stop generating' })]);
  assert.equal(extractor('/g/g-p-1/c/c1', shown).isStreaming(), true);
});

test('Titel aus document.title ohne " - ChatGPT"', () => {
  assert.equal(extractor('/g/g-p-1/c/c1', h('body'), 'Meilensteine prüfen - ChatGPT').getConversationTitle(), 'Meilensteine prüfen');
});
