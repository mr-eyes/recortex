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
  function route() {
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
      unfold(el);
    } else { show('now'); }
    function settle() { if (el && view && el !== view) { el.scrollIntoView(); } else { window.scrollTo(0, 0); } }
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
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') { if (box.open) { return; } if (opened) { closeMem(); } else if (!tour.hidden) { endTour(); } }
    if (event.key === '/' && document.activeElement !== q) { event.preventDefault(); location.hash = '#ask'; q.focus(); }
  });
}());
