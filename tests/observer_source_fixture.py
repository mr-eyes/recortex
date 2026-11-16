"""Explicit filesystem metadata model for synthetic native-source fixtures.

Some execution sandboxes cannot represent the owners of / and /tmp. Tests may
model those two system ancestors as root-owned for one private temporary source
tree. This does not change the production policy, touch permissions, certify a
real native source, or affect subprocesses. Reports must label the simulation.
"""

import contextlib
import os
import stat
from types import SimpleNamespace
from unittest import mock

import observer_sources


@contextlib.contextmanager
def fixture_trusted_ancestors(private_root):
    root = os.path.abspath(os.fspath(private_root))
    info = os.lstat(root)
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or
            info.st_uid != os.getuid() or info.st_mode & 0o077 or os.path.realpath(root) != root):
        raise ValueError("the source fixture must have a real private current-user directory")
    ancestors = set()
    current = os.path.dirname(root)
    while True:
        if current in (os.path.sep, "/tmp"):
            ancestors.add(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    changed = [path for path in sorted(ancestors) if os.lstat(path).st_uid != 0]
    original = observer_sources._component_problem

    def modeled(path, metadata, folder, source_file=None):
        if path in ancestors and (folder == root or folder.startswith(root + os.sep)):
            metadata = SimpleNamespace(st_mode=metadata.st_mode, st_uid=0)
        return original(path, metadata, folder, source_file)

    with mock.patch.object(observer_sources, "_component_problem", side_effect=modeled):
        yield {"simulated": True, "changed_boundaries": changed,
               "scope": "known system ancestors of this private fixture only"}
