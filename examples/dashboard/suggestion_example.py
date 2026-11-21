"""Render the real observer suggestion view with two source records from the invented reader project."""
import base64
import hashlib
from pathlib import Path


def write_example(destination, checkout):
    """The example keeps its one preference in memory and links back into the software dashboard."""
    assets = Path(checkout) / "skills" / "recortex" / "page"
    page = (assets / "dashboard_suggestions.html").read_text(encoding="utf-8")
    css = (assets / "dashboard_action.css").read_text(encoding="utf-8")
    js = (assets / "dashboard_suggestions.js").read_text(encoding="utf-8")
    fixture = """
const sample = {id: 'a'.repeat(64), at: 1775845800, reason: 'decision_failure', scope_kind: 'task', scope_issue: 7,
  muted: false, available: true, sources: [
    {kind: 'decision', issue: 5, comment: 9105, state: 'stands', url: 'software.html#m-9105',
     summary: 'Use a state machine to read quoted fields across lines.'},
    {kind: 'failure', issue: 5, comment: 9104, state: 'stands', url: 'software.html#m-9104',
     summary: 'Carrying an open-quote flag between lines failed on four of six mixed-line-ending cases.'}]};
window.fetch = async (path, options) => {
  if (path === 'api/suggestions' && options.method === 'GET')
    return {ok: true, json: async () => ({repo: 'octo-org/octo-repo', mode: 'deliver', suggestions: [sample]})};
  if (path === 'api/suggestions/feedback' && options.method === 'POST') {
    const data = JSON.parse(options.body);
    if (data.id === sample.id && typeof data.muted === 'boolean') {
      sample.muted = data.muted;
      return {ok: true, json: async () => sample};
    }
  }
  throw new Error('This example works offline.');
};
"""
    script = fixture + js
    def digest(value):
        return base64.b64encode(hashlib.sha256(value.encode("utf-8")).digest()).decode("ascii")
    policy = "default-src 'none'; script-src 'sha256-%s'; style-src 'sha256-%s'; base-uri 'none'; form-action 'none'" % (
        digest(script), digest(css))
    page = page.replace('<link rel="stylesheet" href="action.css"><script src="suggestions.js" defer></script>',
                        '<meta http-equiv="Content-Security-Policy" content="%s"><style>%s</style>' % (policy, css))
    page = page.replace('href="snapshot#now"', 'href="software.html#now"')
    page = page.replace('<h1>Observer suggestions</h1>', '<h1>Observer suggestions</h1><p>Sample project. Reminder changes reset on reload.</p>')
    page = page.replace('</body>', '<script>%s</script></body>' % script)
    (Path(destination) / "suggestions.html").write_text(page, encoding="utf-8")
