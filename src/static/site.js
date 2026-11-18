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
    theme.addEventListener('click', function () {
      var dark = root.getAttribute('data-theme') === 'dark' ||
        (!root.getAttribute('data-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
      var next = dark ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('theme', next); } catch (e) { /* the choice then lasts for this page only */ }
    });
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
    button.addEventListener('click', function () {
      var pre = $('pre', button.parentNode);
      var text = $$('.line', pre).map(function (l) { return l.textContent; }).join('\n') || pre.textContent;
      function done() { button.textContent = 'Copied'; setTimeout(function () { button.textContent = 'Copy'; }, 1400); }
      if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(text).then(done, function () {}); }
    });
  });

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
  if (box && typeof box.showModal === 'function') {
    $('#searchopen').addEventListener('click', openSearch);
    $('#searchclose').addEventListener('click', function () { box.close(); });
    input.addEventListener('input', search);
    input.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') { var first = $('.hit', hits); if (first) { window.location.href = first.getAttribute('href'); } }
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
