// Runs before the page is drawn, so that a saved light or dark choice shows at once.
// The one value this site keeps in the browser is that choice.
try {
  var t = localStorage.getItem('theme');
  if (t === 'light' || t === 'dark') { document.documentElement.setAttribute('data-theme', t); }
} catch (e) { /* no stored choice: the system setting decides */ }
