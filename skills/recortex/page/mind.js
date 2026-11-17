(function () {
  'use strict';
  // Fixed text. No record text lives here: everything shown is read from the page with textContent or copied with
  // cloneNode, and new nodes get their words through textContent. An id read from the page is checked before it
  // goes into a selector or a hash.
  var MEMORY = /^[0-9]+$/, WORD = /^w[0-9]+$/, ISSUE = /^issue-[0-9]+$/, THREAD = /^thread-[0-9]+$/,
    PLACE = /^#[A-Za-z][A-Za-z0-9-]*$/, ID = /^[A-Za-z][A-Za-z0-9-]{0,80}$/, KEY = /^[a-z][a-z0-9-]{0,40}$/;
  var root = document.documentElement;
  root.classList.add('js');
  function $(s, el) { return (el || document).querySelector(s); }
  function $$(s, el) { return Array.prototype.slice.call((el || document).querySelectorAll(s)); }
  var views = $$('.view'), threads = $$('.thread'), mems = $$('.mem'), panel = $('#panel');
  var opened = null, lastFocus = null;

  function show(id) {
    views.forEach(function (v) { v.classList.toggle('on', v.id === id); });
    root.setAttribute('data-view', id);
    $$('[data-nav]').forEach(function (a) {
      var on = a.getAttribute('data-nav') === id;
      a.classList.toggle('on', on);
      if (on) { a.setAttribute('aria-current', 'page'); } else { a.removeAttribute('aria-current'); }
    });
  }
  function showThread(id) {
    threads.forEach(function (t) { t.classList.toggle('on', t.id === id); });
  }
  // a fold: a branch of the tree, a task group of a story, the ways in on a phone. The page is built open; only this
  // script shuts what starts shut, so without it everything is open.
  function setShut(host, shut) {
    host.classList.toggle('shut', shut);
    var button = $('[data-fold]', host);
    if (button) { button.setAttribute('aria-expanded', shut ? 'false' : 'true'); }
  }
  function unfold(el) {
    for (var n = el; n && n.classList; n = n.parentElement) {
      if (n.classList.contains('fold') && n.classList.contains('shut')) { setShut(n, false); }
    }
  }
  // the two pictures of the map: only one shows at a time
  function showPane(name) {
    if (!KEY.test(name || '')) { return; }
    $$('.mappane').forEach(function (pane) { pane.classList.toggle('on', pane.id === 'map-' + name); });
    $$('[data-fold^="pane-"]').forEach(function (b) { b.classList.toggle('on', b.getAttribute('data-fold') === 'pane-' + name); });
  }
  function narrow() { return !!(window.matchMedia && window.matchMedia('(max-width: 860px)').matches); }
  function mark(id) {
    $$('.cur').forEach(function (el) { el.classList.remove('cur'); });
    $$('.hot').forEach(function (el) { el.classList.remove('hot'); });
    if (!id || !MEMORY.test(id)) { return; }
    $$('[data-m="' + id + '"]').forEach(function (el) {
      var host = el.closest('.station, .answer') || el;
      host.classList.add('cur');
    });
    $$('.link[data-a="' + id + '"], .link[data-b="' + id + '"]').forEach(function (el) { el.classList.add('hot'); });
  }
  function openMem(id, keep) {
    if (!MEMORY.test(id || '')) { return; }
    var target = document.getElementById('m-' + id);
    if (!target) { return; }
    if (!keep) { lastFocus = document.activeElement; }
    mems.forEach(function (m) { m.classList.toggle('on', m === target); });
    panel.classList.add('on');
    panel.scrollTop = 0;
    opened = id;
    mark(id);
    target.focus({ preventScroll: true });
  }
  function closeMem() {
    if (!opened) { return; }
    panel.classList.remove('on');
    opened = null;
    mark(null);
    if (lastFocus && document.contains(lastFocus)) { lastFocus.focus({ preventScroll: true }); }
  }
  var kept = {};
  function route() {
    var was = root.getAttribute('data-view');
    if (was) { kept[was] = window.scrollY; }
    var h = location.hash.slice(1) || 'now';
    if (/^m-[0-9]+$/.test(h)) {
      if (!views.some(function (v) { return v.classList.contains('on'); })) { show('now'); }
      openMem(h.slice(2), true);
      return;
    }
    closeMem();
    // a hash that names anything inside a view shows that view, and inside a thread that thread's article
    var el = ID.test(h) ? document.getElementById(h) : null;
    var view = el && el.closest ? el.closest('.view') : null;
    if (view) {
      show(view.id);
      var thread = el.closest('.thread');
      if (thread) { showThread(thread.id); }
      var pane = el.closest('.mappane');
      if (pane) { showPane(pane.id.slice(4)); }
      unfold(el);
    } else { show('now'); }
    if (el && el.classList && el.classList.contains('histbox') && el.hidden) { toggleBox(el.id); }
    function settle() {
      if (el && view && el !== view) { el.scrollIntoView(); } else { window.scrollTo(0, (view && kept[view.id]) || 0); }
    }
    settle();
    setTimeout(settle, 0);
    var side = $('.side');
    if (narrow() && side) { setShut(side, true); }
  }
  // a project word: point at it for one line, choose it for the whole explanation in a box. The text is a copy of
  // its hidden entry; there is no page of words.
  var tip = $('#wordtip'), box = $('#wordbox'), boxBody = $('#wordbody');
  function entryOf(term) {
    var id = term.getAttribute('data-term') || '';
    return WORD.test(id) ? document.getElementById(id) : null;
  }
  function hideTip() { tip.hidden = true; }
  function showTip(term) {
    var entry = entryOf(term), def = entry && $('.def', entry);
    if (!def || box.open) { return; }
    while (tip.firstChild) { tip.removeChild(tip.firstChild); }
    tip.appendChild(def.cloneNode(true));
    var more = document.createElement('p');
    more.className = 'quiet'; more.textContent = 'Choose the word for more.';
    tip.appendChild(more);
    tip.hidden = false;
    var r = term.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    var top = r.bottom + 8;
    if (top + h > window.innerHeight - 8) { top = Math.max(8, r.top - h - 8); }
    tip.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) + 'px';
    tip.style.top = top + 'px';
  }
  function explain(term) { openBox(entryOf(term), 'A word from this project'); }
  // a state, a kind or a mark: one fixed explanation, kept hidden in the page
  function explainKey(key) {
    if (KEY.test(key || '')) {
      openBox(document.getElementById('x-' + key), key === 'how' ? 'About this page' : 'What this means');
    }
  }
  function openBox(entry, kind) {
    if (!entry) { return; }
    $('#wordkind').textContent = kind;
    hideTip();
    while (boxBody.firstChild) { boxBody.removeChild(boxBody.firstChild); }
    $('#wordhead').textContent = $('.wt', entry).textContent;
    Array.prototype.forEach.call(entry.children, function (child) {
      if (!child.classList.contains('wt')) { boxBody.appendChild(child.cloneNode(true)); }
    });
    if (!box.open) { box.showModal(); }
    boxBody.scrollTop = 0;
  }
  function termAt(event) { return event.target.closest ? event.target.closest('.term') : null; }
  document.addEventListener('mouseover', function (event) { var t = termAt(event); if (t) { showTip(t); } });
  document.addEventListener('mouseout', function (event) {
    var t = termAt(event);
    if (t && !(event.relatedTarget && t.contains(event.relatedTarget))) { hideTip(); }
  });
  document.addEventListener('focusin', function (event) { var t = termAt(event); if (t) { showTip(t); } });
  document.addEventListener('focusout', function (event) { if (termAt(event)) { hideTip(); } });
  window.addEventListener('scroll', hideTip, true);
  $('#wordclose').addEventListener('click', function () { box.close(); });
  box.addEventListener('click', function (event) {
    // a click on the dimmed page around the box, or on a link inside it, closes it
    var a = event.target.closest('a');
    if (event.target === box || (a && !event.target.closest('.term') && (a.getAttribute('href') || '').charAt(0) === '#')) {
      box.close();
    }
  });

  if (threads.length) { showThread(threads[0].id); }
  $$('.fold.closed').forEach(function (host) { setShut(host, true); });
  window.addEventListener('hashchange', route);
  route();
  // the panel slides only after the first frame, so that it does not slide off the page while it loads
  window.requestAnimationFrame(function () { root.classList.add('ready'); });

  document.addEventListener('click', function (event) {
    var term = event.target.closest && event.target.closest('.term');
    if (term) { event.preventDefault(); explain(term); return; }
    var why = event.target.closest && event.target.closest('[data-explain]');
    if (why) { event.preventDefault(); explainKey(why.getAttribute('data-explain')); return; }
    var fold = event.target.closest && event.target.closest('[data-fold]');
    if (fold) {
      event.preventDefault();
      var key = fold.getAttribute('data-fold') || '';
      if (key.indexOf('pane-') === 0) { showPane(key.slice(5)); return; }
      if (key === 'open-all' || key === 'close-all') {
        $$('.tree .fold').forEach(function (branch) { setShut(branch, key === 'close-all'); });
      } else {
        var host = fold.closest('.fold');
        if (host) { setShut(host, !host.classList.contains('shut')); }
      }
      return;
    }
    var cite = event.target.closest && event.target.closest('[data-issue]');
    if (cite) {
      event.preventDefault();
      var issue = cite.getAttribute('data-issue') || '';
      if (ISSUE.test(issue)) { openBox(document.getElementById(issue), 'An issue on GitHub'); }
      return;
    }
    var el = event.target.closest && event.target.closest('[data-m]');
    if (el) { event.preventDefault(); openMem(el.getAttribute('data-m'), !!el.closest('#panel')); return; }
    if (opened && !event.target.closest('#panel') && !event.target.closest('#tour') &&
        !event.target.closest('#wordbox')) { closeMem(); }
  });
  $('#close').addEventListener('click', closeMem);

  // the map: reading line and lit links
  var reading = $('#reading'), restText = reading ? reading.textContent : '';
  $$('.dot').forEach(function (dot) {
    function on() {
      var title = $('title', dot);
      reading.textContent = title ? title.textContent : '';
      var id = dot.getAttribute('data-m') || '';
      if (!MEMORY.test(id)) { return; }
      $$('.link[data-a="' + id + '"], .link[data-b="' + id + '"]').forEach(function (el) { el.classList.add('hot'); });
    }
    function off() {
      reading.textContent = restText;
      $$('.link.hot').forEach(function (el) { el.classList.remove('hot'); });
      if (opened) { mark(opened); }
    }
    dot.addEventListener('mouseenter', on); dot.addEventListener('focus', on);
    dot.addEventListener('mouseleave', off); dot.addEventListener('blur', off);
  });

  // ask: answers are sentences already in the page
  var q = $('#q'), out = $('#answers');
  var corpus = mems.filter(function (m) { return /^m-[0-9]+$/.test(m.id); }).map(function (m) {
    return { el: m, id: m.id.slice(2), text: m.textContent.toLowerCase(), kind: m.getAttribute('data-kind'),
             group: m.getAttribute('data-group') };
  }).reverse();
  var HEAD = [['stands', 'What stands'], ['open', 'Still open'], ['done', 'Answered, tried or taken up'],
              ['wrong', 'What went wrong'], ['changed', 'Replaced or set aside'], ['pause', 'Where we paused']];
  var SKIP = ' what did we the a an of in on to is are was were about do does have has had how why when where which who it that this and or for with our us any there ';
  function line(tag, cls, text) { var el = document.createElement(tag); el.className = cls; el.textContent = text; return el; }
  function answerNode(c) {
    var box = document.createElement('div');
    box.className = 'answer k-' + c.kind;
    box.appendChild($('.meta', c.el).cloneNode(true));
    var own = $('.big', c.el).textContent, passage = $('.ans', c.el).textContent;
    var a = line('a', 'said', own);
    a.setAttribute('href', '#m-' + c.id); a.setAttribute('data-m', c.id);
    box.appendChild(a);
    if (passage && passage !== own) { box.appendChild(line('p', 'quiet', passage)); }
    return box;
  }
  function render(list, lead, grouped) {
    while (out.firstChild) { out.removeChild(out.firstChild); }
    out.appendChild(line('p', 'found', lead));
    if (!grouped) { list.forEach(function (c) { out.appendChild(answerNode(c)); }); return; }
    HEAD.forEach(function (pair) {
      var part = list.filter(function (c) { return c.group === pair[0]; });
      if (!part.length) { return; }
      out.appendChild(line('h3', '', pair[1]));
      part.forEach(function (c) { out.appendChild(answerNode(c)); });
    });
  }
  function count(n) { return n === 1 ? 'One memory mentions ' : n + ' memories mention '; }
  function ask() {
    var raw = q.value.trim();
    var words = raw.toLowerCase().split(/[^a-z0-9]+/).filter(function (w) {
      return w.length > 1 && SKIP.indexOf(' ' + w + ' ') < 0;
    }).map(function (w) { return w.length > 4 ? w.replace(/(ing|ed|es|s|e)$/, '') : w; });
    if (!words.length) { while (out.firstChild) { out.removeChild(out.firstChild); } return; }
    var hits = corpus.filter(function (c) { return words.every(function (w) { return c.text.indexOf(w) >= 0; }); });
    if (!hits.length) {
      render([], 'Nothing in this mind mentions “' + raw + '”. Try one plain word, or one of the questions above.', false);
      return;
    }
    render(hits, count(hits.length) + '“' + raw + '”.', true);
  }
  var ASK = {
    decided: [function (c) { return c.kind === 'decision' && c.group === 'stands'; }, 'These decisions still stand, newest first.'],
    wrong: [function (c) { return c.kind === 'failure'; }, 'Everything that went wrong, newest first, with what it led to.'],
    open: [function (c) { return c.group === 'open'; }, 'These thoughts are still open.'],
    changed: [function (c) { return c.group === 'changed'; }, 'These were replaced or set aside. Each is told with what took its place.']
  };
  if (q) {
    q.addEventListener('input', ask);
    $$('[data-ask]').forEach(function (b) {
      b.addEventListener('click', function () {
        var rule = ASK[b.getAttribute('data-ask')];
        q.value = '';
        render(corpus.filter(rule[0]), rule[1], false);
      });
    });
  }

  // the manuscript read as a paper: which text the sheet shows (the changes since a version, or a clean copy), which version, the
  // next change, the boxes of history and the copy buttons. Everything it shows is already in the page; the script holds no record
  // text, and a version number is read as a whole number before it is used.
  var paper = $('#paper'), VERSION = /^[0-9]{1,6}$/, MODES = ['changes', 'clean'];
  function tokens(el, name) { return (el.getAttribute(name) || '').split(' ').filter(function (t) { return VERSION.test(t); }); }
  function atTop(fn) {
    // keep what the reader is looking at where it is while the text around it changes
    var host = null, top = 0;
    $$('.partrow, .rw, .sechead', paper).some(function (el) {
      var r = el.getBoundingClientRect();
      if (r.bottom > 90 && el.offsetParent !== null) { host = el; top = r.top; return true; }
      return false;
    });
    fn();
    if (host) { window.scrollBy(0, host.getBoundingClientRect().top - top); }
  }
  function pickVersion(k) {
    if (!paper || !VERSION.test(k || '')) { return; }
    atTop(function () {
      $$('[data-for]', paper).forEach(function (el) { el.classList.toggle('on', tokens(el, 'data-for').indexOf(k) >= 0); });
      $$('.kv', paper).forEach(function (el) { el.textContent = k; });
      $$('.rw', paper).forEach(function (row) {
        var found = null;
        ['added', 'reworded', 'changed'].forEach(function (kind) {
          var has = tokens(row, 'data-' + kind).indexOf(k) >= 0;
          row.classList.toggle('r-' + kind, false);
          if (has && !found) { found = kind; }
        });
        if (found) { row.classList.add('r-' + found); }
      });
      sideNotes();
    });
  }
  function setMode(mode) {
    if (!paper || MODES.indexOf(mode) < 0) { return; }
    atTop(function () { paper.setAttribute('data-mode', mode); sideNotes(); });
  }
  function changes() {
    return $$('.rw.r-added, .rw.r-reworded, .rw.r-changed, .r-removed.on', paper).filter(function (el) { return el.offsetParent !== null; });
  }
  function goChange(step) {
    var bar = $('.sbar', paper), offset = (bar ? bar.offsetHeight : 0) + 16, list = changes(), target = null;
    if (step > 0) {
      target = list.filter(function (el) { return el.getBoundingClientRect().top > offset + 1; })[0];
    } else {
      list = list.filter(function (el) { return el.getBoundingClientRect().top < offset - 1; });
      target = list[list.length - 1];
    }
    if (!target) { say(step > 0 ? 'No later change' : 'No earlier change'); return; }
    var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    window.scrollBy({ top: target.getBoundingClientRect().top - offset, behavior: calm ? 'auto' : 'smooth' });
    say('Change ' + (changes().indexOf(target) + 1) + ' of ' + changes().length);
  }
  // The notes of the margin sit beside their rows. A note taller than its row would leave a hole in the text, so each note
  // takes no room of its own (its bottom margin is as negative as it is tall) and a note that would overlap the one before it
  // is moved down, with no change to the text. Without the script, or in a narrow window, the layout is the style's.
  var laid = '', soon = 0;
  function sideNotes() {
    var host = $('#paper-text', paper), wide = window.matchMedia && window.matchMedia('(min-width: 1181px)').matches;
    if (!host) { return; }
    var units = $$('.row > .mn, .row > .mnotes', host);
    units.forEach(function (u) { u.style.position = ''; u.style.top = ''; u.style.marginBottom = ''; });
    host.style.paddingBottom = '';
    if (!wide) { return; }
    var shown = units.filter(function (u) { return u.offsetHeight > 0; });
    var heights = shown.map(function (u) { return u.offsetHeight; });
    shown.forEach(function (u, i) { u.style.marginBottom = (-heights[i]) + 'px'; });
    var bottom = -1e9, base = window.pageYOffset;
    shown.forEach(function (u, i) {
      var top = u.getBoundingClientRect().top + base, push = Math.max(0, bottom + 10 - top);
      if (push > 0) { u.style.position = 'relative'; u.style.top = push + 'px'; }
      bottom = top + push + heights[i];
    });
    var end = host.getBoundingClientRect().bottom + base;
    if (bottom > end) { host.style.paddingBottom = Math.ceil(bottom - end + 10) + 'px'; }
    laid = host.offsetWidth + 'x' + host.offsetHeight;
  }
  function toggleBox(id) {
    if (!ID.test(id || '')) { return; }
    var box = document.getElementById(id);
    if (!box) { return; }
    box.hidden = !box.hidden;
    var opener = $('[aria-controls="' + id + '"]');
    if (opener) { opener.setAttribute('aria-expanded', box.hidden ? 'false' : 'true'); }
    if (!box.hidden) { box.focus(); } else if (opener) { opener.focus(); }
  }
  var told = $('#copystatus');
  function say(words) {
    // the text is cleared first, so that the same words said twice are announced twice
    if (!told) { return; }
    told.textContent = '';
    setTimeout(function () { told.textContent = words; }, 50);
  }
  function copyRequest(button) {
    var req = button.previousElementSibling;
    if (!req || !req.classList.contains('req')) { return; }
    var text = req.textContent, shown = req.nextElementSibling;
    function failed() {
      var box = document.createElement('textarea');
      box.className = 'copybox'; box.readOnly = true; box.value = text; box.rows = 2;
      box.setAttribute('aria-label', 'The request, to copy by hand');
      if (shown && shown.classList.contains('copybox')) { shown.parentNode.replaceChild(box, shown); }
      else { req.parentNode.insertBefore(box, req.nextSibling); }
      box.focus(); box.select();
      say('Copy did not work; press Ctrl+C.');
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { say('Request copied. Paste it in the chat.'); }, failed);
      return;
    }
    var area = document.createElement('textarea');
    area.value = text; area.setAttribute('readonly', 'readonly'); area.className = 'copyaside';
    document.body.appendChild(area); area.select();
    var done = false;
    try { done = document.execCommand('copy'); } catch (error) { done = false; }
    document.body.removeChild(area);
    if (done) { say('Request copied. Paste it in the chat.'); } else { failed(); }
  }
  if (paper) {
    $$('.histbox', paper).forEach(function (box) { box.hidden = true; });
    if (narrow()) { $$('.legend', paper).forEach(function (d) { d.open = false; }); }
    var pick = $('#pick');
    if (pick) {
      pick.addEventListener('change', function () {
        var changes = $('input[name=pmode][value=changes]', paper);
        if (paper.getAttribute('data-mode') === 'clean' && changes) { changes.checked = true; setMode('changes'); }
        pickVersion(pick.value);
      });
    }
    $$('input[name=pmode]', paper).forEach(function (radio) {
      radio.addEventListener('change', function () { if (radio.checked) { setMode(radio.value); } });
    });
    var prev = $('#chgprev'), next = $('#chgnext');
    if (prev) { prev.addEventListener('click', function () { goChange(-1); }); }
    if (next) { next.addEventListener('click', function () { goChange(1); }); }
    document.addEventListener('click', function (event) {
      var t = event.target && event.target.closest ? event.target.closest('[data-toggle]') : null;
      if (t) { event.preventDefault(); toggleBox(t.getAttribute('data-toggle')); return; }
      var c = event.target && event.target.closest ? event.target.closest('[data-copy]') : null;
      if (c) { event.preventDefault(); copyRequest(c); }
    });
    // the sections in the side bar, marked as the paper is read
    if ('IntersectionObserver' in window) {
      var spy = new IntersectionObserver(function (found) {
        found.forEach(function (entry) {
          if (!entry.isIntersecting) { return; }
          var head = $('.sech', entry.target), id = head ? head.id : '';
          if (!ID.test(id)) { return; }
          $$('.subnav a').forEach(function (a) {
            var here = a.getAttribute('href') === '#' + id;
            a.classList.toggle('here', here);
            if (here) { a.setAttribute('aria-current', 'location'); } else { a.removeAttribute('aria-current'); }
          });
        });
      }, { rootMargin: '-120px 0px -70% 0px' });
      $$('.psec', paper).forEach(function (section) { spy.observe(section); });
    }
    // the notes of the margin, laid out beside the text (see sideNotes)
    var side = window.matchMedia ? window.matchMedia('(min-width: 1181px)') : null;
    if (side) { if (side.addEventListener) { side.addEventListener('change', sideNotes); } else if (side.addListener) { side.addListener(sideNotes); } }
    // what a jump leaves clear at the top is the height of the bar, which grows with a long line on a narrow window
    var bar = $('.sbar', paper);
    function barPad() { if (bar) { root.style.setProperty('--barpad', (bar.offsetHeight + 24) + 'px'); } }
    if ('ResizeObserver' in window && $('#paper-text', paper)) {
      var watch = new ResizeObserver(function () {
        barPad();
        var host = $('#paper-text', paper), now = host.offsetWidth + 'x' + host.offsetHeight;
        if (now === laid || soon) { return; }
        soon = window.requestAnimationFrame(function () { soon = 0; sideNotes(); });
      });
      watch.observe($('#paper-text', paper));
      if (bar) { watch.observe(bar); }
    }
    barPad();
    sideNotes();
    // a link to a history box opens it (the boxes were shut above, after the first route)
    if (/^#hist-[0-9]+-[0-9]+$/.test(location.hash)) { route(); }
  }
  // a window that was narrow and is wide again shows the ways in
  var wide = window.matchMedia ? window.matchMedia('(max-width: 860px)') : null;
  function widened(event) { var side = $('.side'); if (side && !event.matches) { setShut(side, false); } }
  if (wide) { if (wide.addEventListener) { wide.addEventListener('change', widened); } else if (wide.addListener) { wide.addListener(widened); } }


  // first visit
  var tour = $('#tour'), steps = $$('.step', tour), at = 0;
  function step(i) {
    at = Math.max(0, Math.min(steps.length - 1, i));
    steps.forEach(function (s, n) { s.hidden = n !== at; });
    $('#tourn').textContent = (at + 1) + ' of ' + steps.length;
    $('#tourback').disabled = at === 0;
    $('#tournext').textContent = at === steps.length - 1 ? 'Done' : 'Next';
    var go = steps[at].getAttribute('data-go') || '';
    if (PLACE.test(go) && location.hash !== go) { location.hash = go; }
  }
  function endTour() { tour.hidden = true; }
  $('#tourstart').addEventListener('click', function () { tour.hidden = false; step(0); });
  $('#tourback').addEventListener('click', function () { step(at - 1); });
  $('#tournext').addEventListener('click', function () { if (at === steps.length - 1) { endTour(); } else { step(at + 1); } });
  $('#tourend').addEventListener('click', endTour);

  $('#theme').addEventListener('click', function () {
    var dark = root.getAttribute('data-theme') === 'dark' ||
      (!root.getAttribute('data-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
    root.setAttribute('data-theme', dark ? 'light' : 'dark');
  });
  document.addEventListener('click', function (event) {
    var button = event.target && event.target.closest ? event.target.closest('[data-print]') : null;
    if (button && window.print) { window.print(); }
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      if (box.open) { return; }
      var shownBox = paper && document.activeElement && document.activeElement.closest ? document.activeElement.closest('.histbox') : null;
      if (shownBox && !shownBox.hidden) { toggleBox(shownBox.id); return; }
      if (opened) { closeMem(); } else if (!tour.hidden) { endTour(); }
    }
    if (event.key === '/' && document.activeElement !== q) { event.preventDefault(); location.hash = '#ask'; q.focus(); }
  });
}());
