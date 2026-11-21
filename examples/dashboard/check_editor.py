#!/usr/bin/env python3
"""Run the optional Firefox check against a fresh local dashboard and fake GitHub."""
import http.server
import os
import secrets
import subprocess
import sys
import threading
from pathlib import Path


def main():
    checkout = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(checkout / "tests"), str(checkout / "skills" / "recortex" / "scripts")]
    import dashboard_action
    from test_jobs_tasks import TaskBox
    from test_scripts import MindSandbox
    from preview import add_decision_change, add_project_map

    impact = sys.argv[1:] == ["--impact"]
    if sys.argv[1:] and not impact:
        raise SystemExit("Usage: check_editor.py [--impact]")
    fixture = MindSandbox() if impact else TaskBox()
    try:
        fixture.setUp()
        if impact:
            fixture.config["profiles"] = ["software"]
            fixture.write_config(fixture.config)
            fixture.add_chain()
            add_decision_change(fixture, "software")
            add_project_map(fixture, "software")
            fixture.ready()
        fixture.in_process(extra={"CLAUDE_PROJECT_DIR": fixture.project})
        with http.server.HTTPServer(("127.0.0.1", 0), dashboard_action.Handler) as server:
            server.actions = dashboard_action.Actions()
            server.host = "127.0.0.1:%d" % server.server_port
            server.mount = "/" + secrets.token_hex(16)
            server.token = secrets.token_urlsafe(32)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                address = "http://%s%s/#%s" % (server.host, server.mount, server.token)
                env = dict(os.environ, DASHBOARD_TEST_ADDRESS=address)
                flag = "--impact-fixture" if impact else "--editor-fixture"
                result = subprocess.run(["node", str(Path(__file__).with_name("check.cjs")), flag], env=env)
                return result.returncode
            finally:
                server.shutdown()
                thread.join()
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    raise SystemExit(main())
