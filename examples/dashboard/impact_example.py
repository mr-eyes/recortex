"""Exercise the production decision editor offline using an invented project's projection and edit plans."""
import base64
import hashlib
import json
from types import SimpleNamespace


def write_example(destination, checkout, name, model, fixture):
    from decision_review_io import Service

    project = dict(model["decision_impacts"], repo=model["repo"])
    service = Service(SimpleNamespace(repo=model["repo"], config=fixture.config, root=fixture.project))
    plans = {}
    for group in project["groups"]:
        for item in group["items"]:
            draft = item.get("edit_text") or item["draft"]
            action, detail, _ = service.plan(group, item, {"verdict": "needs_update", "draft": draft}, "a" * 32)
            plans[group["id"] + "-" + item["id"]] = {"action": action, "detail": detail, "draft": draft}
    sample = json.dumps({"project": project, "plans": plans}, ensure_ascii=True).replace("<", "\\u003c")
    assets = checkout / "skills/recortex/page"
    page = (assets / "dashboard_impact.html").read_text(encoding="utf-8")
    css = (assets / "dashboard_action.css").read_text(encoding="utf-8")
    # All operations below are ephemeral. Only the generator calls the production planner, against fake_gh.
    adapter = """
(() => {
  const {project, plans} = SAMPLE, staged = new Map();
  let seq = 0;
  window.fetch = async (path, options) => {
    const body = options.body ? JSON.parse(options.body) : {};
    const reply = value => ({ok: true, json: async () => value});
    if (path === 'api/impact' && options.method === 'GET') return reply(project);
    if (path === 'api/impact/preview') {
      const group = project.groups.find(g => g.id === body.group);
      const item = group.items.find(i => i.id === body.target);
      const plan = plans[group.id + '-' + item.id];
      let action = 'review', detail = {};
      if (body.verdict === 'needs_update') {
        action = body.draft === plan.draft ? plan.action : 'followup';
        detail = body.draft === plan.draft ? plan.detail : {request: body.draft + '\\n' + item.url};
      }
      const result = {id: String(++seq), action, detail, summary: 'Record ' + body.verdict.replaceAll('_',' ') + ' for ' + item.label + '.'};
      staged.set(result.id, {result, body, item});
      return reply(result);
    }
    if (path === 'api/impact/resume') {
      const saved = Array.from(staged.values()).find(op => op.body.target === body.target && op.result.review?.comment === body.previous);
      if (saved) return reply(saved.result);
    }
    const saved = staged.get(body.id);
    if (saved && (path === 'api/impact/commit' || path === 'api/impact/apply')) {
      const applied = path === 'api/impact/apply';
      saved.result.applied = applied;
      saved.result.review = {comment: 9800 + ++seq, verdict: applied ? 'applied' : saved.body.verdict,
        action: saved.result.action, reviewer: 'sample reviewer', note: saved.body.note, draft: saved.body.draft};
      saved.item.review = saved.result.review;
      return reply(saved.result);
    }
    throw new Error('This sample operation is unavailable.');
  };
})();
"""
    script = adapter.replace("SAMPLE", sample) + (assets / "dashboard_impact.js").read_text(encoding="utf-8")
    def digest(value):
        return base64.b64encode(hashlib.sha256(value.encode("utf-8")).digest()).decode("ascii")
    policy = "default-src 'none'; script-src 'sha256-%s'; style-src 'sha256-%s'; base-uri 'none'; form-action 'none'" % (
        digest(script), digest(css))
    page = page.replace('<link rel="stylesheet" href="action.css"><script src="impact.js" defer></script>',
                        '<meta http-equiv="Content-Security-Policy" content="%s"><style>%s</style>' % (policy, css))
    page = page.replace('href="snapshot#history"', 'href="%s.html#history"' % name)
    page = page.replace('<p id="project">', '<p>Sample project. Reviews and edits reset when you reload or leave this page.</p><p id="project">')
    page = page.replace('<script src="stop.js" defer></script>', '')  # there is no service to stop in a sample
    page = page.replace('</body>', '<script>%s</script></body>' % script)
    (destination / (name + "-review.html")).write_text(page, encoding="utf-8")
