// Tests fuer extension/popup.js: Meldung, wenn auf der Seite kein Projekt erkannt ist.
//
// Anlass: Auf der Claude-Projektuebersicht (/project/<id>) meldete das Popup
// "Kein ... Projekt erkannt", obwohl ein Projekt offen ist. Claude liefert das
// Projekt erst aus einer Unterhaltung (/chat/<id>).

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const SRC = fs.readFileSync(new URL('../../extension/popup.js', import.meta.url), 'utf8');
const PROJ = '22222222-2222-2222-2222-222222222222';
const C1 = 'aaaaaaaa-0000-0000-0000-000000000001';

const ALLGEMEIN = 'Kein ChatGPT- oder Claude-Projekt auf dieser Seite erkannt.';
const UEBERSICHT = 'Projektübersicht – Polaris erfasst erst, wenn ein Chat in diesem Projekt geöffnet ist.';
const OHNE_PROJEKT = 'Dieser Chat gehört zu keinem Projekt und wird nicht archiviert.';

// Laedt popup.js mit nachgebautem DOM und Browser-API. tab = aktiver Tab
// (null: keiner), context = Antwort auf get-context.
function loadPopup({ tab = null, context = null } = {}) {
  const elements = {};
  const element = (id) => {
    if (!elements[id]) {
      const classes = new Set(['hidden']);
      elements[id] = {
        textContent: '',
        checked: false,
        addEventListener() {},
        classList: {
          add: (c) => classes.add(c),
          remove: (c) => classes.delete(c),
          toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)),
          contains: (c) => classes.has(c),
        },
      };
    }
    return elements[id];
  };
  const browser = {
    tabs: { query: async () => (tab ? [tab] : []) },
    runtime: { sendMessage: (_msg, cb) => cb(context) },
    storage: { local: { get: (defaults, cb) => cb(defaults), set: (_p, cb) => cb() } },
    permissions: { contains: async () => true, request: async () => true },
  };
  const sandbox = { browser, document: { getElementById: element }, console, URL };
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox);
  return { sandbox, element };
}

test('noProjectText: Claude-Projektuebersicht', () => {
  const { sandbox } = loadPopup();
  assert.equal(sandbox.noProjectText(`https://claude.ai/project/${PROJ}`, null), UEBERSICHT);
});

test('noProjectText: Claude-Chat ohne Projekt', () => {
  const { sandbox } = loadPopup();
  assert.equal(sandbox.noProjectText(`https://claude.ai/chat/${C1}`, { conversationId: C1 }), OHNE_PROJEKT);
});

test('noProjectText: sonst die allgemeine Meldung', () => {
  const { sandbox } = loadPopup();
  assert.equal(sandbox.noProjectText('https://example.org/', null), ALLGEMEIN);
  // Ohne Host-Berechtigung fehlt tab.url.
  assert.equal(sandbox.noProjectText(undefined, null), ALLGEMEIN);
  // Projektpfad auf fremdem Host zaehlt nicht.
  assert.equal(sandbox.noProjectText(`https://example.org/project/${PROJ}`, null), ALLGEMEIN);
});

test('Popup zeigt auf der Projektuebersicht den Hinweis an', async () => {
  const { element } = loadPopup({ tab: { id: 1, url: `https://claude.ai/project/${PROJ}` }, context: {} });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(element('no-project').classList.contains('hidden'), false);
  assert.equal(element('no-project-text').textContent, UEBERSICHT);
});
