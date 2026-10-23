/*
 * Polaris - Hinweisleiste bei hohem Speicherbedarf (seit 0.4.4)
 *
 * Anlass (2026-10-22/23): Ein ChatGPT-Tab wuchs auf 20-26 GB, systemd-oomd
 * beendete daraufhin ganz Firefox. Neu laden (F5) gibt den Speicher frei.
 *
 * Messung und Desktop-Meldung macht der Native Host (bridge/polaris/
 * tab_memory.py); background.js fragt ihn jede Minute und schickt das
 * Ergebnis als {type: 'tab-memory', level, bytes} an alle ChatGPT- und
 * Claude-Tabs.
 * Welcher Tab-Prozess zu welcher Seite gehoert, verraet Firefox nicht -
 * daher "vermutlich".
 */

(() => {
  const BANNER_ID = 'polaris-memory-banner';
  const LEVELS = ['ok', 'warn', 'critical'];
  let dismissedLevel = 'ok';

  function api() {
    return typeof browser !== 'undefined' ? browser : chrome;
  }

  function siteName() {
    return /(^|\.)claude\.ai$/.test(location.hostname) ? 'Claude' : 'ChatGPT';
  }

  function gb(bytes) {
    return (bytes / 1024 ** 3).toFixed(1).replace('.', ',');
  }

  function removeBanner() {
    const el = document.getElementById(BANNER_ID);
    if (el) el.remove();
  }

  function showBanner(level, bytes) {
    let el = document.getElementById(BANNER_ID);
    if (!el) {
      el = document.createElement('div');
      el.id = BANNER_ID;
      el.setAttribute('role', 'alert');
      el.style.cssText =
        'position:fixed;top:0;left:0;right:0;z-index:2147483647;padding:8px 40px 8px 16px;' +
        'font:14px/1.4 system-ui,sans-serif;color:#1a1a1a;box-shadow:0 2px 6px rgba(0,0,0,.25)';
      const text = document.createElement('span');
      text.className = 'polaris-memory-text';
      const close = document.createElement('button');
      close.type = 'button';
      close.textContent = '×';
      close.title = 'Ausblenden';
      close.style.cssText =
        'position:absolute;right:8px;top:4px;border:0;background:none;font-size:20px;cursor:pointer;color:inherit';
      close.addEventListener('click', () => {
        dismissedLevel = el.dataset.level || 'ok';
        removeBanner();
      });
      el.append(text, close);
      document.body.append(el);
    }
    el.dataset.level = level;
    el.style.background = level === 'critical' ? '#f8b4b4' : '#fde68a';
    el.querySelector('.polaris-memory-text').textContent =
      `Polaris: Ein Firefox-Tab belegt ${gb(bytes)} GB Arbeitsspeicher – vermutlich dieser ${siteName()}-Tab. ` +
      'F5 drücken (Neu laden) gibt den Speicher frei.' +
      (level === 'critical' ? ' Wächst er weiter, beendet das System ganz Firefox.' : '');
  }

  function onTabMemory(level, bytes) {
    if (!LEVELS.includes(level) || level === 'ok') {
      dismissedLevel = 'ok';
      removeBanner();
      return;
    }
    // Ausgeblendet bleibt ausgeblendet, bis es schlimmer wird.
    if (LEVELS.indexOf(level) <= LEVELS.indexOf(dismissedLevel)) return;
    showBanner(level, Number(bytes) || 0);
  }

  api().runtime.onMessage.addListener((msg) => {
    if (msg && msg.type === 'tab-memory') onTabMemory(msg.level, msg.bytes);
    return undefined;
  });
})();
