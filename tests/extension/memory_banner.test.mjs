// Tests fuer extension/memory_banner.js (seit 0.4.4): Hinweisleiste bei
// hohem Speicherbedarf, gesteuert durch {type: 'tab-memory'} aus background.js.

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const SRC = fs.readFileSync(new URL('../../extension/memory_banner.js', import.meta.url), 'utf8');
const GB = 1024 ** 3;

function el(tag) {
  const node = {
    tagName: tag, id: '', className: '', textContent: '', title: '', type: '',
    style: { cssText: '' }, dataset: {}, children: [], parent: null, listeners: {},
    setAttribute() {},
    addEventListener(type, fn) { node.listeners[type] = fn; },
    append(...kids) { for (const k of kids) { k.parent = node; node.children.push(k); } },
    remove() { if (node.parent) node.parent.children = node.parent.children.filter((c) => c !== node); node.parent = null; },
    querySelector(sel) { return node.children.find((c) => '.' + c.className === sel) || null; },
  };
  return node;
}

function page(hostname = 'chatgpt.com') {
  const body = el('body');
  let listener = null;
  const find = (n, id) => (n.id === id ? n : n.children.map((c) => find(c, id)).find(Boolean) || null);
  const sandbox = {
    document: { body, createElement: el, getElementById: (id) => find(body, id) },
    location: { hostname },
    chrome: { runtime: { onMessage: { addListener: (fn) => { listener = fn; } } } },
  };
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox);
  return {
    send: (level, bytes) => listener({ type: 'tab-memory', level, bytes }),
    banner: () => sandbox.document.getElementById('polaris-memory-banner'),
  };
}

test('Warnung zeigt Groesse und F5-Hinweis, ok entfernt die Leiste', () => {
  const p = page();
  p.send('ok', 1 * GB);
  assert.equal(p.banner(), null);
  p.send('warn', 4.6 * GB);
  const text = p.banner().children[0].textContent;
  assert.match(text, /4,6 GB/);
  assert.match(text, /F5/);
  assert.equal(p.banner().dataset.level, 'warn');
  p.send('ok', 1 * GB);
  assert.equal(p.banner(), null);
});

test('Kritisch faerbt um und ergaenzt den Hinweis auf das Beenden', () => {
  const p = page();
  p.send('critical', 9 * GB);
  assert.equal(p.banner().dataset.level, 'critical');
  assert.match(p.banner().children[0].textContent, /beendet das System ganz Firefox/);
});

test('Ausgeblendet bleibt aus, bis die Stufe steigt', () => {
  const p = page();
  p.send('warn', 5 * GB);
  p.banner().children[1].listeners.click();
  assert.equal(p.banner(), null);
  p.send('warn', 6 * GB);
  assert.equal(p.banner(), null);
  p.send('critical', 8.5 * GB);
  assert.notEqual(p.banner(), null);
});

test('Nur eine Leiste, auch bei wiederholten Meldungen; fremde Nachrichten ignoriert', () => {
  const p = page();
  p.send('warn', 5 * GB);
  p.send('warn', 5.5 * GB);
  assert.match(p.banner().children[0].textContent, /5,5 GB/);
  p.send('unsinn', 50 * GB);
  assert.equal(p.banner(), null);
});

test('Seitenname passt zur Seite (ChatGPT oder Claude)', () => {
  const g = page('chatgpt.com');
  g.send('warn', 5 * GB);
  assert.match(g.banner().children[0].textContent, /dieser ChatGPT-Tab/);
  const c = page('claude.ai');
  c.send('warn', 5 * GB);
  assert.match(c.banner().children[0].textContent, /dieser Claude-Tab/);
});
