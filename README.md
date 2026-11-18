# recortex.github.io

The documentation site for [recortex](https://github.com/recortex/recortex), a Claude Code plugin. It is served by
GitHub Pages at https://recortex.github.io.

Copyright 2026 Mohamed Abuelanin. All rights reserved. The plugin has its own license; see its repository.

## How it is made

Pages are HTML fragments in `src/pages/`. `build.py` puts them inside one shared header, navigation and footer and
writes the result to `_site/`. It uses the Python 3 standard library only, so there is nothing to install.

```bash
python3 build.py                    # writes _site/ and runs check.py on it
python3 build.py --plugin PATH      # check against a plugin checkout you already have
python3 -m http.server -d _site 8080
```

`check.py` fails the build on a command, script, flag or file name that the plugin does not have at the ref pinned in
`site.json`, on a banned word, on a name that is not on the list of invented names, on a broken internal link, on a
page with no title, and on anything the page loads from another server. Without `--plugin` it takes the ref from
`site.json` out of a clone of `plugin_repo` on GitHub.

## Tests and tools

```bash
python3 -m unittest discover -s tests       # every check in check.py must fail when its guard is removed
node tools/smoke.mjs http://127.0.0.1:8080  # search, theme switch, menu and "no outside request", in headless Chrome
node tools/shoot.mjs .tmp/shots "home-1440-light|http://127.0.0.1:8080/|1440|light"   # screenshots
```

`tools/social-preview.html` is the source of `src/static/assets/social-preview.png`. The build and the tests run on
Python 3.8 and newer.

## Publishing

`.github/workflows/pages.yml` builds and checks the site on every push to `main`, then deploys it with the official
Pages actions. Before the first deploy, set the repository's Pages source to "GitHub Actions" and pin `plugin_ref`
in `site.json` to the release tag the pages describe.

Corrections are welcome as issues.
