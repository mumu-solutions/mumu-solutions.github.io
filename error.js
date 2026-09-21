/* error.js — theme and language for 404/401/403/500.
 *
 * External, not inline, for the same reason error.css is: index.html hashes
 * its inline blocks into the CSP and tools/check_csp_hashes.py regenerates
 * those hashes, but that tooling reads index.html only. An inline block here
 * would need a sha256 that nothing maintains. Served from 'self' under
 * script-src 'self', this needs no hash.
 *
 * Loaded from <head> WITHOUT defer or async, deliberately: it must set
 * data-theme before first paint, or the page flashes the wrong colours.
 *
 * The keys are index.html's — mumu-theme and mumu-lang — so a choice made on
 * the site is still in force here, and a choice made here survives the trip
 * back to the home page.
 *
 * Every localStorage access is wrapped: in Safari's private mode a read can
 * throw, and an uncaught throw here would take the language switch down with
 * it on a page whose entire job is to work when something else has failed.
 */
(function () {
  'use strict';

  var root = document.documentElement;

  function get(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }
  function set(key, value) {
    try { localStorage.setItem(key, value); } catch (e) {}
  }

  /* ----------------------------------------------------------- theme */
  /* No data-theme in the markup: without this script the stylesheet follows
     prefers-color-scheme. Only a STORED choice is applied here, so a visitor
     who has never pressed the button keeps following their OS. */
  var storedTheme = get('mumu-theme');
  if (storedTheme === 'light' || storedTheme === 'dark') {
    root.dataset.theme = storedTheme;
  }

  function currentTheme() {
    if (root.dataset.theme) return root.dataset.theme;
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }

  /* ---------------------------------------------------------- language */
  /* The markup ships data-language="pt", so Portuguese is what a visitor
     without JavaScript sees. Only override it from storage or ?lang=. */
  var q = null;
  try { q = new URLSearchParams(location.search).get('lang'); } catch (e) {}
  var storedLang = get('mumu-lang');
  var lang = (q === 'en' || q === 'pt') ? q
           : (storedLang === 'en' || storedLang === 'pt') ? storedLang
           : 'pt';

  /* Two frozen constants, swapped into the icon with innerHTML exactly as
     index.html does. No input of any kind reaches them, so there is nothing
     for a sanitiser to do here. */
  var MOON = '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>';
  var SUN = '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>';

  var LABEL = {
    pt: { theme: 'Alternar tema claro e escuro', group: 'Idioma' },
    en: { theme: 'Toggle light and dark theme', group: 'Language' }
  };

  function setLanguage(next, persist) {
    next = next === 'en' ? 'en' : 'pt';
    root.dataset.language = next;
    root.lang = next === 'en' ? 'en' : 'pt-BR';

    var ptBtn = document.getElementById('langPt');
    var enBtn = document.getElementById('langEn');
    if (ptBtn) ptBtn.setAttribute('aria-pressed', String(next === 'pt'));
    if (enBtn) enBtn.setAttribute('aria-pressed', String(next === 'en'));

    var group = document.getElementById('langGroup');
    if (group) group.setAttribute('aria-label', LABEL[next].group);
    var themeBtn = document.getElementById('themeBtn');
    if (themeBtn) themeBtn.setAttribute('aria-label', LABEL[next].theme);

    /* The <title> is the tab and the bookmark, so it follows too. Each page
       supplies both strings; no page-specific code lives in here. */
    var t = document.querySelector('meta[name="title-' + next + '"]');
    if (t) document.title = t.content;

    if (persist) set('mumu-lang', next);
  }

  function paintThemeIcon() {
    var icon = document.getElementById('themeIcon');
    if (icon) icon.innerHTML = currentTheme() === 'light' ? SUN : MOON;
  }

  /* The <html> element exists while this runs, but the buttons do not — this
     script is in <head> so it can beat first paint. Wiring waits for the DOM;
     the theme attribute above did not, which is the whole point. */
  document.addEventListener('DOMContentLoaded', function () {
    setLanguage(lang, false);
    paintThemeIcon();

    var ptBtn = document.getElementById('langPt');
    var enBtn = document.getElementById('langEn');
    if (ptBtn) ptBtn.addEventListener('click', function () { setLanguage('pt', true); });
    if (enBtn) enBtn.addEventListener('click', function () { setLanguage('en', true); });

    var themeBtn = document.getElementById('themeBtn');
    if (themeBtn) {
      themeBtn.addEventListener('click', function () {
        var next = currentTheme() === 'light' ? 'dark' : 'light';
        root.dataset.theme = next;
        set('mumu-theme', next);
        paintThemeIcon();
      });
    }
  });
})();
