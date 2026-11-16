#!/usr/bin/env bash
# Local capture only. The detached worker has its own lock and lifetime limit.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || exit 0
command -v python3 >/dev/null 2>&1 || exit 0
cd "${CLAUDE_PROJECT_DIR:-$PWD}" 2>/dev/null || exit 0
if command -v timeout >/dev/null 2>&1; then
  timeout 4 python3 "$here/observer.py" hook "${1:-}" 2>/dev/null
else
  python3 "$here/observer.py" hook "${1:-}" 2>/dev/null
fi
exit 0
