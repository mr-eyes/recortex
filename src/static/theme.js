// Runs before the page is drawn, so that a saved light or dark choice shows at once.
// The one value this site keeps in the browser is that choice, under a key of its own, because other Pages sites
// of the same account share this origin.
try {
  var t = localStorage.getItem('recortex-theme');
  if (t === 'light' || t === 'dark') { document.documentElement.setAttribute('data-theme', t); }
} catch (e) { /* no stored choice: the system setting decides */ }
