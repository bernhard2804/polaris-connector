/*
 * Polaris ChatGPT Extractor
 *
 * Einziger Ort mit DOM-spezifischem Wissen ueber die ChatGPT-Weboberflaeche.
 * Wenn ChatGPT sein UI aendert, muss in der Regel nur diese Datei angepasst
 * werden (siehe README.md, Abschnitt "Extension reparieren").
 *
 * Struktur, VERIFIZIERT gegen echtes chatgpt.com-Markup (DevTools-Auszug
 * eines Assistant-Turns, eingebettet in ein ChatGPT-Project "Firma",
 * eingesammelt 2026-09-12):
 *
 *   - Das damalige Nachrichten-Markup (conversation-turn-*,
 *     data-message-author-role) gibt es seit 2026-09-25 nicht mehr; der
 *     Zweig dafuer ist seit 0.4.3 entfernt. Aktueller Aufbau: bei
 *     UNIT_SELECTOR unten.
 *   - Ist die Conversation Teil eines Projects, lautet die URL
 *     /g/g-p-<opaque-id>/c/<conversation-id> (NICHT nur /c/<id> wie bei
 *     projektlosen Chats). getConversationId() sucht /c/([...]) im
 *     gesamten Pfad (nicht verankert) und findet daher in beiden Faellen
 *     korrekt das richtige Segment.
 *   - Projekt-Link: <a href="/g/g-p-<opaque-id>-<name-slug>/project"
 *     aria-label="Open <Name> project"><span>...<span
 *     class="...">NAME</span></span></a>. Der Name-Slug im href ist
 *     NICHT Teil der extrahierten ID (Regex stoppt am ersten "-" nach dem
 *     alphanumerischen Token), die ID bleibt also stabil, falls ChatGPT
 *     spaeter den Slug/Namen aendert.
 *   - WICHTIG, zwei Bugs im ersten Live-Test gefunden + behoben
 *     (2026-09-12), beide betreffen die Projekt-Erkennung:
 *     1. Sidebar-Eintraege zu einzelnen Chats INNERHALB eines Projects
 *        tragen ebenfalls einen href der Form /g/g-p-<id>/c/<conv-id>.
 *        Ein ungefiltertes `a[href*="g-p-<id>"]` ueber das gesamte
 *        Dokument matcht daher faelschlich zuerst so einen Sidebar-Link
 *        (Text = der Conversation-Titel, nicht der Project-Name).
 *     2. Der Projekt-Link im Seiten-Header (<header id="page-header">)
 *        ist NICHT dauerhaft im DOM vorhanden - er verschwindet je nach
 *        Scroll-Position/Renderzustand des Threads ("kein Projekt
 *        erkannt" trat mitten im Chat auf). Eine Beschraenkung auf
 *        #page-header ist daher nicht robust genug.
 *     Fix fuer beides: PROJECT_LINK_SELECTOR sucht gezielt nach dem
 *     aria-label-Muster "Open ... project" (das traegt nur der echte
 *     Projekt-Link, u.a. der dauerhaft in der Sidebar sichtbare
 *     Projekt-Ordner-Eintrag - Conversation-Links haben dieses Muster
 *     nicht), unabhaengig davon, wo im Dokument er gerade sitzt.
 *   - Einzige NICHT verifizierte Annahme: STREAMING_SELECTOR (Button
 *     waehrend eine Antwort noch generiert wird). Kein Sample einer
 *     laufenden Generierung verfuegbar gewesen.
 */

const PolarisExtractor = (() => {
  const STREAMING_SELECTOR =
    'button[data-testid="stop-button"], button[aria-label*="stop generating" i], button[aria-label*="stop streaming" i]';
  // aria-label statt Ort im Dokument: der Header-Link ist nur beim
  // Scrollen nahe Threadanfang gerendert (Live-Test 2026-09-12 zeigte
  // "kein Projekt erkannt" sobald weiter unten im Chat). Die Sidebar hat
  // denselben Project-Link dauerhaft als "Open <Name> project" (das
  // gepinnte Projekt-Ordner-Icon links). Sidebar-Eintraege zu einzelnen
  // Chats haben dieses aria-label-Muster nicht, daher hier kein Konflikt
  // mit dem zuvor gefundenen Sidebar-Bug.
  const PROJECT_LINK_SELECTOR =
    'a[aria-label^="Open "][aria-label$=" project"][href*="/g/g-p-"]';

  function getConversationId() {
    const m = location.pathname.match(/\/c\/([a-zA-Z0-9-]+)/);
    return m ? m[1] : null;
  }

  function getConversationTitle() {
    const title = document.title || '';
    const cleaned = title.replace(/\s*[-–]\s*ChatGPT\s*$/i, '').trim();
    if (cleaned) return cleaned;
    const active = document.querySelector(
      'a[href^="/c/"][aria-current="page"], nav a[data-active="true"]'
    );
    return active ? active.textContent.trim() : null;
  }

  function extractProjectFromHref(href) {
    if (!href) return null;
    const m = href.match(/\/g\/(g-p-[a-zA-Z0-9]+)/);
    if (!m) return null;
    return m[1];
  }

  // 2026-09-25: Der "Open <Name> project"-Link existiert in ChatGPTs
  // aktuellem Markup nicht mehr (Popup meldete "Kein ChatGPT-Projekt
  // erkannt", seit 2026-09-14 keine Nachrichten archiviert). Robuster: die
  // Projekt-ID steht in der Adresse jeder Projekt-Unterhaltung
  // (/g/g-p-<id>[-<slug>]/c/<conv-id>) und auf der Projektseite
  // (/g/g-p-<id>[-<slug>]/project). Die Zuordnung zum lokalen Ordner laeuft
  // ueber diese ID (Bridge, projects.yaml); der Name dient nur der Anzeige.
  const PATH_PROJECT_RE = /^\/g\/(g-p-[a-zA-Z0-9]+)(?:-([^/]+))?\/(?:c\/|project)/;

  // Nur Links, deren href genau diese Projekt-ID traegt: der erste
  // "Open ... project"-Link im Dokument kann zu einem anderen Projekt in der
  // Seitenleiste gehoeren - dessen Name landete sonst in den Einstellungen und
  // im Popup, wo der Ablageordner gewaehlt wird (Review 2026-09-29).
  function getProjectNameFromDom(id) {
    const links = document.querySelectorAll('a[href*="/g/"]');
    for (const el of links) {
      if (extractProjectFromHref(el.getAttribute('href')) !== id) continue;
      if (!/\/project$/.test(el.getAttribute('href') || '') && !el.matches(PROJECT_LINK_SELECTOR)) continue;
      const text = (el.getAttribute('aria-label') || el.textContent || '').trim();
      if (text) return text.replace(/^Open\s+/, '').replace(/\s+project$/, '').trim();
    }
    return null;
  }

  function slugToName(slug) {
    if (!slug) return null;
    try {
      return decodeURIComponent(slug).replace(/-/g, ' ').trim() || null;
    } catch (_) {
      return slug;
    }
  }

  // Projekt NUR aus der Adresse. Der fruehere DOM-Rueckfall (erster
  // Projekt-Link im Dokument) haette einen projektlosen Chat (/c/<id>) dem
  // Projekt zugeordnet, das zufaellig in der Seitenleiste steht - privater
  // Inhalt im Kundenordner (Review 2026-09-29).
  function getProjectInfo() {
    const m = location.pathname.match(PATH_PROJECT_RE);
    if (!m) return null;
    const id = m[1];
    return { id, name: getProjectNameFromDom(id) || slugToName(m[2]) || id };
  }

  // Nur sichtbare Stop-Knoepfe zaehlen: 2026-09-25 blieb der automatische
  // Export komplett aus (nur "Sync now" ging) - vermutlich traf der Selektor
  // im neuen Markup dauerhaft ein verstecktes Element.
  function isStreaming() {
    return Array.from(document.querySelectorAll(STREAMING_SELECTOR)).some(
      (el) => el.getClientRects().length > 0
    );
  }

  // 2026-09-25: ChatGPT hat das Markup der Unterhaltung umgebaut - die
  // bisherigen Merkmale (conversation-turn-*, data-message-author-role,
  // data-message-id, <article>) existieren nicht mehr (per DevTools-Probe des
  // Nutzers auf einer Kunden-Unterhaltung bestaetigt: alle 0). Neuer Aufbau
  // (gleiche Probe, nur Struktur, keine Inhalte):
  //   div[data-turn-key=<uuid>]                              - ein Turn
  //     div[data-chatgpt-search-unit-key="fallback-turn-1:2:assistant"]
  //        [data-chatgpt-search-message-ids="<msg-id> ..."]    - eine Nachricht
  //       div[data-chatgpt-selection-message-id=<msg-id>]
  //         div[data-markdown-text-style=assistant-message]    - der Text
  // Die Rolle steht am Ende des unit-key, die ID in ...-message-ids.
  const UNIT_SELECTOR = '[data-chatgpt-search-unit-key], [data-content-search-unit-key]';
  const ROLES = new Set(['user', 'assistant']);

  function getUnitTurns() {
    const turns = [];
    document.querySelectorAll(UNIT_SELECTOR).forEach((unit) => {
      // Verschachtelte Units nicht doppelt zaehlen.
      if (unit.parentElement && unit.parentElement.closest(UNIT_SELECTOR)) return;
      const key =
        unit.getAttribute('data-chatgpt-search-unit-key') ||
        unit.getAttribute('data-content-search-unit-key') ||
        '';
      const role = key.split(':').pop();
      if (!ROLES.has(role)) return;
      const ids = (unit.getAttribute('data-chatgpt-search-message-ids') || '')
        .trim()
        .split(/\s+/)
        .filter(Boolean);
      const selEl = unit.querySelector('[data-chatgpt-selection-message-id]');
      const nativeId =
        ids[0] || (selEl && selEl.getAttribute('data-chatgpt-selection-message-id')) || null;
      const node = unit.querySelector('[data-markdown-text-style]') || unit;
      // Alle IDs der Einheit mitgeben: die Bridge erkennt so denselben Turn,
      // auch wenn Schnittstelle oder Export eine andere davon als ID nutzen.
      turns.push({ role, nativeId, memberIds: ids, node, attachments: role === 'user' ? getUploadNames(unit) : [] });
    });
    return turns;
  }

  // Hochgeladene Dateien in Nutzer-Nachrichten (2026-09-25 per DevTools-Probe
  // bestaetigt): je Datei ein <button aria-label=...> mit <span title=...>
  // innerhalb der Unit. Nur Namen, keine Inhalte.
  const FILE_NAME_RE = /\.[A-Za-z0-9]{2,5}$/;

  function getUploadNames(unit) {
    const names = new Set();
    unit.querySelectorAll('span[title]').forEach((el) => {
      const t = (el.getAttribute('title') || '').trim();
      if (FILE_NAME_RE.test(t)) names.add(t);
    });
    if (!names.size) {
      unit.querySelectorAll('button[aria-label]').forEach((el) => {
        const t = (el.getAttribute('aria-label') || '').trim();
        if (FILE_NAME_RE.test(t)) names.add(t);
      });
    }
    return [...names];
  }

  // Liefert nur billige Metadaten (Attribute, keine erzwungene
  // Layout-Berechnung) fuer JEDEN Turn. Der Text wird bewusst erst
  // on-demand via getTurnContent() gelesen - .innerText erzwingt ein
  // synchrones Layout und war bei jedem DOM-Mutation-Tick fuer ALLE Turns
  // spuerbar teuer (Tipp-Aussetzer, 2026-09-12 vom Nutzer beobachtet).
  function getTurns() {
    return getUnitTurns();
  }

  function getTurnContent(node) {
    return (node.innerText || '').trim();
  }

  return {
    STREAMING_SELECTOR,
    PROJECT_LINK_SELECTOR,
    getConversationId,
    getConversationTitle,
    getProjectInfo,
    isStreaming,
    getTurns,
    getTurnContent,
  };
})();
