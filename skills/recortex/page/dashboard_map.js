/* Loaded only in the authenticated loopback editor. */
(function () {
  'use strict';
  const byId = id => document.getElementById(id);
  const form = byId('map-change'), fields = byId('fields'), action = byId('action');
  const status = byId('status'), panel = byId('preview'), inputs = {};
  let state, anchorById = {}, chosen = '', previewId = null, busy = false;
  function el(tag, text) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    return element;
  }
  const messages = {map_changed: 'The map changed. Read the current map and review a new preview.',
    unauthorized: 'Open the private address printed by the running dashboard.',
    allowed_account_required: 'Sign in with an allowed project account. For an organization-owned repository, add your GitHub login beside the repository name in its trusted-repos entry.',
    account_changed: 'The active account changed. Start a new preview.',
    held: 'A publication hold blocks the edit.',
    preview_expired_or_changed: 'This preview expired. Read the current map and preview again.'};
  async function api(path, data) {
    const options = {method: data ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store'};
    if (data) { options.headers = {'Content-Type': 'application/json'}; options.body = JSON.stringify(data); }
    const response = await fetch(path, options), result = await response.json();
    if (!response.ok) throw new Error(messages[result.code] || result.message || 'The shared record could not be verified. Read the current map to check it.');
    return result;
  }
  async function run(work) {
    if (busy) return;
    busy = true;
    const controls = [...form.elements, byId('confirm'), byId('cancel'), byId('refresh-map')];
    controls.forEach(control => { control.disabled = true; });
    try { await work(); } catch (error) { status.textContent = error.message; }
    finally { controls.forEach(control => { control.disabled = false; }); busy = false;
      if (previewId && !panel.hidden) byId('confirm').focus();
    }
  }
  function closePreview() { panel.hidden = true; previewId = null; }
  function option(value, label) { const o = el('option', label); o.value = value; return o; }
  function input(key, label, type, value, options, host) {
    const wrapper = el('div'), caption = el('label', label), field = el(type);
    field.id = 'edit-' + key; caption.htmlFor = field.id; wrapper.appendChild(caption);
    if (type === 'select') options.forEach(o => field.appendChild(option(o[0], o[1])));
    if (type === 'textarea') field.rows = 3;
    field.value = value || '';
    wrapper.appendChild(field); (host || fields).appendChild(wrapper);
    inputs[key] = field;
    return field;
  }
  function nodeChoices(deleted) {
    const items = Object.values(state.nodes).filter(n => n.deleted === deleted);
    items.sort((a, b) => a.title.localeCompare(b.title) || a.id.localeCompare(b.id));
    return items.map(n => [n.id, n.title + (n.parent && state.nodes[n.parent] ? ' · ' + state.nodes[n.parent].title : '')]);
  }
  function redraw() {
    fields.replaceChildren(); Object.keys(inputs).forEach(k => delete inputs[k]);
    closePreview();
    const op = action.value, active = nodeChoices(false), options = op === 'restore' ? nodeChoices(true) : active;
    let node = state.nodes[chosen];
    if (op !== 'add') {
      if (!options.some(o => o[0] === chosen)) chosen = options.length ? options[0][0] : '';
      node = state.nodes[chosen];
      const target = input('id', op === 'restore' ? 'Removed branch' : 'Branch', 'select', chosen, options);
      target.addEventListener('change', () => { chosen = target.value; redraw(); inputs.id.focus(); });
      if (!node) { fields.appendChild(el('p', op === 'restore' ? 'No removed branches.' : 'Add a branch to begin.')); return; }
    }
    if (op === 'add' || op === 'rename') input('title', 'Branch name', 'input', op === 'rename' && node ? node.title : '').maxLength = 160;
    if (op === 'add' || op === 'move') input('parent', 'Parent', 'select', op === 'move' ? node.parent : chosen,
      [['', 'Project root']].concat(active.filter(o => op === 'add' || o[0] !== chosen)));
    if (op === 'update' || op === 'add') {
      const existing = op === 'update' ? node : {};
      input('description', 'Description', 'textarea', existing.description).maxLength = 1600;
      input('evidence', 'Evidence references, one per line (issue:7, comment:9103, file:README.md)', 'textarea', (existing.evidence || []).join('\n'));
      const others = active.filter(o => op === 'add' || o[0] !== chosen);
      const links = input('links', 'Related branches', 'select', '', others);
      links.multiple = true;
      [...links.options].forEach(o => { o.selected = (existing.links || []).includes(o.value); });
    }
    if (op === 'merge') input('into', 'Merge into', 'select', '', active.filter(o => o[0] !== chosen)).selectedIndex = 0;
    if (op === 'delete') input('children', 'Descendants', 'select', 'keep', [
      ['keep', 'Keep children under the parent'], ['subtree', 'Remove the entire subtree']]);
    if (op === 'split') {
      const children = active.filter(o => state.nodes[o[0]].parent === chosen);
      for (let n = 1; n <= 2; n++) {
        const block = el('fieldset'); block.appendChild(el('legend', 'New branch ' + n)); fields.appendChild(block);
        input('title' + n, 'Name', 'input', '', null, block).maxLength = 160;
        if (children.length) {
          const select = input('kids' + n, 'Move existing children here', 'select', '', children, block);
          select.multiple = true;
        }
      }
    }
  }
  function selectedValues(field) { return field ? [...field.selectedOptions].map(o => o.value) : []; }
  function change() {
    const op = action.value, data = {action: op};
    if (op !== 'add') data.id = chosen;
    for (const key of ['title', 'parent', 'description', 'into', 'children']) {
      if (inputs[key]) data[key] = key === 'parent' ? inputs[key].value || null : inputs[key].value;
    }
    if (inputs.evidence) data.evidence = inputs.evidence.value.split('\n').map(s => s.trim()).filter(Boolean);
    if (inputs.links) data.links = selectedValues(inputs.links);
    if (op === 'split') data.branches = [1, 2].map(n => ({title: inputs['title' + n].value, children: selectedValues(inputs['kids' + n])}));
    return data;
  }
  function displayValue(map, key, value) {
    if (key === 'deleted') return value ? 'Removed' : 'Visible';
    if (key === 'parent') return map.nodes[value] ? map.nodes[value].title : 'Project root';
    if (key === 'links') return value.map(id => map.nodes[id] ? map.nodes[id].title : id).join(', ') || 'None';
    if (Array.isArray(value)) return value.join(', ') || 'None';
    return value || 'Empty';
  }
  function showDiff(after, changed) {
    const diff = byId('map-diff'); diff.replaceChildren();
    const labels = {title: 'Name', description: 'Description', parent: 'Parent', deleted: 'Visibility', evidence: 'Evidence', links: 'Related branches'};
    changed.forEach(id => {
      const previous = state.nodes[id], next = after.nodes[id];
      diff.appendChild(el('h3', previous ? previous.title : next.title + ' (new)'));
      const list = el('ul');
      Object.keys(labels).forEach(key => {
        if (previous && JSON.stringify(previous[key]) === JSON.stringify(next[key])) return;
        let text = labels[key] + ' · ';
        if (previous) text += displayValue(state, key, previous[key]) + ' → ';
        list.appendChild(el('li', text + displayValue(after, key, next[key])));
      });
      diff.appendChild(list);
    });
  }
  function setState(data, initial) {
    state = data.state; anchorById = data.anchors;
    if (initial) chosen = Object.keys(anchorById).find(id => '#' + anchorById[id] === location.hash) || '';
    if (initial && chosen) action.value = 'rename';
    const back = chosen && state.nodes[chosen] && !state.nodes[chosen].deleted ? anchorById[chosen] : null;
    byId('back').href = 'snapshot#' + (back || 'project-map');
    form.hidden = false; redraw();
  }
  async function read(initial) {
    setState(await api('api/map'), initial);
    status.textContent = 'Showing the shared project map.';
  }
  action.addEventListener('change', redraw);
  form.addEventListener('input', closePreview);
  form.addEventListener('submit', event => {
    event.preventDefault();
    run(async () => {
      closePreview();
      const checked = await api('api/map/preview', {revision: state.revision, change: change()});
      showDiff(checked.state, checked.affected); previewId = checked.id;
      panel.hidden = false; byId('confirm').focus(); status.textContent = 'Review each affected branch below.';
    });
  });
  byId('cancel').addEventListener('click', () => { closePreview(); status.textContent = 'Preview canceled.'; });
  byId('confirm').addEventListener('click', () => run(async () => {
    if (!previewId) return;
    const result = await api('api/map/commit', {id: previewId});
    closePreview(); setState(result, false);
    status.textContent = 'Map change saved in hub comment ' + result.comment + '.';
  }));
  byId('refresh-map').addEventListener('click', () => run(() => read(false)));
  run(() => read(true));
}());
