(function () {
  'use strict';
  // The site's script: the light and dark switch, the menu on a narrow screen, the copy buttons and the search.
  // Fixed words only, text set through textContent, nothing loaded from anywhere. The one thing it keeps in this
  // browser is the reader's choice of light or dark.
  var root = document.documentElement;
  function $(s, el) { return (el || document).querySelector(s); }
  function $$(s, el) { return Array.prototype.slice.call((el || document).querySelectorAll(s)); }

  var theme = $('#theme');
  if (theme) {
    var systemDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)');
    var themeIcon = $('use', theme);
    function nextTheme() {
      var choice = root.getAttribute('data-theme');
      var dark = choice ? choice === 'dark' : systemDark && systemDark.matches;
      return dark ? 'light' : 'dark';
    }
    function updateThemeButton() {
      var next = nextTheme();
      var action = 'Switch to ' + next + ' mode';
      theme.setAttribute('aria-label', action);
      theme.title = action;
      themeIcon.setAttribute('href', '#theme-' + next + '-icon');
    }
    theme.addEventListener('click', function () {
      var next = nextTheme();
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('recortex-theme', next); } catch (e) { /* the choice then lasts for this page only */ }
      updateThemeButton();
    });
    if (systemDark && systemDark.addEventListener) {
      systemDark.addEventListener('change', updateThemeButton);
    } else if (systemDark && systemDark.addListener) {
      systemDark.addListener(updateThemeButton);
    }
    updateThemeButton();
    theme.disabled = false;
  }

  var menu = $('#menubtn');
  if (menu) {
    menu.addEventListener('click', function () {
      var open = !document.body.classList.contains('menuopen');
      document.body.classList.toggle('menuopen', open);
      menu.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) { window.scrollTo(0, 0); }
    });
  }

  $$('.cmd .copy').forEach(function (button) {
    var manual, reset;
    button.addEventListener('click', function () {
      var pre = $('pre', button.parentNode);
      var text = $$('.line', pre).map(function (l) { return l.textContent; }).join('\n') || pre.textContent;
      clearTimeout(reset);
      function done() {
        if (manual) { manual.remove(); manual = null; button.focus(); }
        button.textContent = 'Copied';
        reset = setTimeout(function () { button.textContent = 'Copy'; }, 1400);
      }
      function copySelection() {
        if (!manual) {
          manual = document.createElement('textarea');
          manual.readOnly = true;
          manual.setAttribute('aria-label', 'Text to copy manually');
          manual.value = text;
          button.parentNode.appendChild(manual);
        }
        manual.focus();
        manual.select();
        try { if (document.execCommand('copy')) { done(); return; } } catch (e) { /* keep the text selected */ }
        button.textContent = 'Copy manually';
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, copySelection);
      } else {
        copySelection();
      }
    });
  });

  function revealFragment(hash) {
    if (!hash) { return null; }
    var id;
    try { id = decodeURIComponent(hash.slice(1)); } catch (e) { return null; }
    var target = document.getElementById(id);
    if (target && target.tagName === 'DETAILS') {
      target.open = true;
      return $('summary', target) || target;
    }
    return target;
  }
  function revealCurrentFragment(event) {
    var target = revealFragment(window.location.hash);
    // A fragment on <details> can leave focus on the body. Keep any focus the browser restored.
    if (target && target.tagName === 'SUMMARY' && !event.persisted && document.activeElement === document.body) {
      target.focus({preventScroll: true});
    }
  }
  window.addEventListener('pageshow', revealCurrentFragment);
  window.addEventListener('hashchange', revealCurrentFragment);

  // search: one entry per section, made when the site is built
  var box = $('#searchbox'), input = $('#searchinput'), hits = $('#searchhits');
  function words(s) { return s.toLowerCase().split(/[^a-z0-9.:\/_-]+/).filter(Boolean); }
  function search() {
    var index = window.SITE_INDEX || [], q = words(input.value);
    hits.textContent = '';
    var found = index.map(function (entry) {
      var head = (entry.h || '').toLowerCase(), page = (entry.p || '').toLowerCase();
      var rest = ((entry.x || '') + ' ' + (entry.d || '')).toLowerCase();
      var score = 0;
      q.forEach(function (w) {
        if (head.indexOf(w) >= 0) { score += 4; } else if (page.indexOf(w) >= 0) { score += 2; } else if (rest.indexOf(w) >= 0) { score += 1; } else { score -= 99; }
      });
      return { entry: entry, score: q.length ? score : 0 };
    }).filter(function (r) { return q.length ? r.score > 0 : r.entry.h === ''; })
      .sort(function (a, b) { return b.score - a.score; }).slice(0, 8);
    if (!found.length) {
      var none = document.createElement('p'); none.className = 'nothing'; none.textContent = 'Nothing on the site says that yet.';
      hits.appendChild(none); return;
    }
    found.forEach(function (r) {
      var a = document.createElement('a'), b = document.createElement('b'), s = document.createElement('span');
      a.className = 'hit'; a.setAttribute('href', r.entry.u);
      b.textContent = r.entry.h || r.entry.p;
      s.textContent = (r.entry.h ? r.entry.p + ' · ' : '') + (r.entry.x || r.entry.d || '').slice(0, 110);
      a.appendChild(b); a.appendChild(s); hits.appendChild(a);
    });
  }
  function openSearch() { if (!box.open) { box.showModal(); } input.value = ''; search(); input.focus(); }
  function modified(event) { return event.altKey || event.ctrlKey || event.metaKey || event.shiftKey; }
  if (box && typeof box.showModal === 'function') {
    $('#searchopen').addEventListener('click', openSearch);
    $('#searchclose').addEventListener('click', function () { box.close(); });
    input.addEventListener('input', search);
    input.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' && !event.isComposing && !modified(event)) {
        var first = $('.hit', hits);
        if (first) { event.preventDefault(); first.click(); }
      }
    });
    hits.addEventListener('click', function (event) {
      var link = event.target.closest('a.hit');
      if (!link || event.defaultPrevented || event.button !== 0 || modified(event)) { return; }
      box.close();
      var here = window.location;
      if (link.origin !== here.origin || link.pathname !== here.pathname || link.search !== here.search) { return; }
      var target = revealFragment(link.hash);
      if (!target) { return; }
      event.preventDefault();
      if (here.hash !== link.hash) { here.hash = link.hash; }
      if (target.tabIndex < 0) { target.tabIndex = -1; }
      target.focus({preventScroll: true});
      target.scrollIntoView();
    });
    box.addEventListener('click', function (event) { if (event.target === box) { box.close(); } });
    document.addEventListener('keydown', function (event) {
      var typing = /^(INPUT|TEXTAREA)$/.test((event.target.tagName || ''));
      if (event.key === '/' && !typing && !box.open) { event.preventDefault(); openSearch(); }
    });
  } else if (box) {
    var opener = $('#searchopen'); if (opener) { opener.hidden = true; }
  }
}());
