#!/bin/bash
# Remakes the pictures of the README from the invented record in ./record, offline, through the test fake.
# Not part of the plugin and not run by the tests. Needs Python 3, Node 22 or newer and Google Chrome.
# Nothing here reads or writes the real ~/.cache, ~/.local/state or ~/.config: HOME and the XDG folders are scratch.
# usage: docs/pictures/make.sh [work folder]   (default: a new folder under .tmp/ of the repository)
set -e
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd); R=$(cd "$HERE/../.." && pwd); S=$R/skills/recortex/scripts
REPO=${PICTURE_REPO:-octo-org/sample-project}   # the invented record's repository name; the drafts hold the same text
mkdir -p "$R/.tmp"; D=$(mktemp -d "${1:-$R/.tmp}/pictures-XXXX")
mkdir "$D/project" "$D/home"; cp "$HERE/state.json" "$D/state.json"; cp -r "$HERE/record" "$D/drafts"
printf '%s octocat\n' "$REPO" > "$D/trusted"
printf '{"repo": "%s", "hub": 4, "profiles": ["software", "research"]}\n' "$REPO" > "$D/project/.recortex.json"
# every command runs in a clean environment: no variable of the plugin or of the real account reaches it
run() {
  env -i PATH="$PATH" HOME="$D/home" RECORTEX_GH="python3 $R/tests/fake_gh.py" FAKE_GH_STATE="$D/state.json" \
    RECORTEX_TRUSTED_FILE="$D/trusted" XDG_CACHE_HOME="$D/cache" XDG_STATE_HOME="$D/state" XDG_CONFIG_HOME="$D/config" \
    CLAUDE_PROJECT_DIR="$D/project" PYTHONDONTWRITEBYTECODE=1 "$@"
}
cd "$D/drafts"
run python3 "$S/setup_labels.py" --repo "$REPO" --profile software,research --topic dates --topic order >/dev/null
run python3 "$S/create_issues.py" --manifest manifest.json
cd "$D/project"
for pair in 1-idea:idea 2-failure:failure 3-decision:decision 4-question:question 5-decision:decision; do
  run python3 "$S/post_verified.py" comment --issue 5 --kind "${pair#*:}" --body-file "$D/drafts/chain/${pair%:*}.md" | tail -2
done
run python3 "$S/index.py" sync && run python3 "$S/mind.py"
PAGE="$D/cache/recortex/private/$(echo "$REPO" | sed 's#/#__#')/mind.html"
cp "$PAGE" "$D/mind.html"
# the story pictures: the page in a 1240 by 1418 window scrolled to the thread's title, 1100 wide of it kept, at 0.8 so that each file
# stays about 200 KB: the side bar, the title, "Where it stands" and the story from the first decision to the open question, light and dark
run node "$HERE/shots.mjs" "$D/mind.html" "$D/shots" mind-story 1240 thread-5 1418 0.8 1100 10
cp "$D/shots/mind-story-light-thread-5.png" "$R/assets/mind-story-light.png"
cp "$D/shots/mind-story-dark-thread-5.png" "$R/assets/mind-story-dark.png"
# the two drawings
python3 "$HERE/make_assets.py"
echo "work folder: $D"
