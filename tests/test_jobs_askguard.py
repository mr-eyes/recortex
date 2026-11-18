"""The ask agent can only read (S5 of the 1.5.0 review): the PreToolUse check that stops every Bash command of the
`recortex:ask` agent but one `index.py query` read, in every project, whether or not the observer is on. Offline."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_scripts as ts  # noqa: E402

sys.path.insert(0, ts.SCRIPTS)
import pretool_guard  # noqa: E402

INDEX = os.path.join(ts.SCRIPTS, "index.py")
GUARD = os.path.join(ts.SCRIPTS, "run_pretool_guard.sh")
BASH = shutil.which("bash")
ASK = "recortex:ask"
ALLOWED = (
    'python3 %s query find --words "why strict quoting"' % INDEX,
    "python3 %s query recall --issue 5" % INDEX,
    "python3 %s query timeline --issue 5 --json" % INDEX,
    "python3 %s query tasks" % INDEX,
    "python3 %s query plan --json" % INDEX,
    "python3 %s query find --words 'reader speed' --issue 3" % INDEX,
)
DENIED = (
    "cd %s && python3 %s query find --words x" % (ts.SCRIPTS, INDEX),   # the needless cd the agent adds
    "python3 %s query find --words x; echo done" % INDEX,
    "python3 %s query find --words x | cat" % INDEX,
    "python3 %s query find --words x > out.txt" % INDEX,
    "python3 %s query find --words x && gh issue comment 5 --body hi" % INDEX,
    "python3 %s query find --words $(whoami)" % INDEX,
    "python3 %s query find --words `whoami`" % INDEX,
    "python3 %s query find --words x\necho second" % INDEX,
    "python3 %s query find --words 'a;b'" % INDEX,           # an operator inside quotes is refused as well
    "python3 %s query sync" % INDEX,                          # not one of the five reads
    "python3 %s sync" % INDEX,
    "python3 %s query find" % os.path.join(ts.SCRIPTS, "post_verified.py"),
    "python3 %s comment --issue 5 --body-file x.md" % os.path.join(ts.SCRIPTS, "post_verified.py"),
    "python3 %s enable --level everything --model sonnet --yes" % os.path.join(ts.SCRIPTS, "observer.py"),
    "gh issue comment 5 --body hi",
    "echo agent-probe",
    "PYTHONSTARTUP=x python3 %s query find --words x" % INDEX,
    "python3 /tmp/elsewhere/index.py query find --words x",
    "python3 -c 'import os' %s query find" % INDEX,
    "",
)


def call(command, agent=ASK, **extra):
    data = {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash", "tool_input": {"command": command}}
    if agent:
        data.update(agent_type=agent, agent_id="a1b2c3")
    data.update(extra)
    return data


def run_script(data, env=None, raw=None):
    """The hook as Claude Code starts it: the shell script, the call on its stdin. Returns (exit code, stdout)."""
    done = subprocess.run([BASH, GUARD], input=raw if raw is not None else json.dumps(data), capture_output=True,
                          text=True, env=env or dict(os.environ), timeout=20)
    return done.returncode, done.stdout


def reason(out):
    value = json.loads(out)["hookSpecificOutput"]
    assert value["hookEventName"] == "PreToolUse" and value["permissionDecision"] == "deny", value
    return value["permissionDecisionReason"]


class CommandTests(unittest.TestCase):
    def test_the_five_reads_pass(self):
        for command in ALLOWED:
            self.assertIsNone(pretool_guard.ask_decision(call(command)), command)

    def test_every_other_command_is_denied_with_the_same_fixed_words(self):
        for command in DENIED:
            out = pretool_guard.ask_decision(call(command))
            self.assertIsNotNone(out, command)
            self.assertEqual(pretool_guard.DENIAL % ts.SCRIPTS, out["hookSpecificOutput"]["permissionDecisionReason"], command)

    def test_a_shell_operator_is_refused_even_after_the_right_prefix(self):
        for operator in (";", "&&", "||", "|", ">", ">>", "<", "`x`", "$(x)", "${x}", "\n", "\\", "(x)", "&"):
            command = "python3 %s query find --words x %s y" % (INDEX, operator)
            self.assertFalse(pretool_guard.allowed_command(command), repr(operator))
        self.assertTrue(pretool_guard.allowed_command("python3 %s query find --words 'two words' --issue 4" % INDEX))

    def test_the_index_script_is_this_scripts_folders_by_its_real_path(self):
        with tempfile.TemporaryDirectory() as folder:
            link = os.path.join(folder, "index.py")
            os.symlink(INDEX, link)
            self.assertTrue(pretool_guard.allowed_command("python3 %s query find --words x" % link))
            elsewhere = os.path.join(folder, "other", "index.py")
            os.makedirs(os.path.dirname(elsewhere))
            with open(elsewhere, "w") as handle:
                handle.write("print(1)\n")
            self.assertFalse(pretool_guard.allowed_command("python3 %s query find --words x" % elsewhere))

    def test_the_denial_holds_no_word_of_the_command(self):
        out = pretool_guard.ask_decision(call("echo ZZMARK-HOSTILE <script>"))
        self.assertNotIn("ZZMARK", json.dumps(out))
        self.assertNotIn("script>", json.dumps(out))

    def test_another_agent_and_the_main_session_are_not_looked_at(self):
        for agent in ("recortex:record-auditor", "recortex:record-drafter", "general-purpose", None):
            self.assertIsNone(pretool_guard.decide(json.dumps(call("gh issue comment 5 --body hi", agent=agent))), agent)
        self.assertIsNone(pretool_guard.decide(json.dumps(call("echo hi that says recortex:ask in it", agent=None))))

    def test_a_tool_that_is_not_bash_is_denied_for_the_ask_agent(self):
        data = call(ALLOWED[0])
        data["tool_name"] = "Write"
        self.assertIsNotNone(pretool_guard.ask_decision(data))
        data = call(ALLOWED[0])
        data["tool_input"] = "not a mapping"
        self.assertIsNotNone(pretool_guard.ask_decision(data))


class ScriptTests(unittest.TestCase):
    """The hook as Claude Code runs it, in a shell, with a clean environment: no project, no approval, no observer."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.env = {"PATH": os.environ["PATH"], "HOME": folder.name, "XDG_STATE_HOME": os.path.join(folder.name, "state"),
                    "XDG_CACHE_HOME": os.path.join(folder.name, "cache"), "XDG_CONFIG_HOME": os.path.join(folder.name, "config")}
        self.folder = folder.name

    def test_the_allowed_reads_print_nothing_and_the_rest_are_denied_in_a_folder_with_no_observer(self):
        for command in ALLOWED:
            self.assertEqual((0, ""), run_script(call(command), self.env), command)
        for command in DENIED:
            code, out = run_script(call(command), self.env)
            self.assertEqual(0, code, command)
            self.assertEqual(pretool_guard.DENIAL % ts.SCRIPTS, reason(out), command)
        self.assertFalse(os.path.exists(os.path.join(self.folder, "state")))   # nothing was written for any of them

    def test_the_denial_holds_with_the_observer_on_and_with_it_off(self):
        project = os.path.join(self.folder, "project")
        os.makedirs(project)
        for config in ({"repo": "octo-org/octo-repo", "hub": 1, "observer": False}, {"repo": "octo-org/octo-repo", "hub": 1}):
            with open(os.path.join(project, ".recortex.json"), "w") as handle:
                json.dump(config, handle)
            env = dict(self.env, CLAUDE_PROJECT_DIR=project)
            code, out = run_script(call("cd x && python3 %s query find --words x" % INDEX), env)
            self.assertEqual(pretool_guard.DENIAL % ts.SCRIPTS, reason(out))

    def test_other_calls_are_untouched_and_cost_no_python(self):
        for agent in (None, "recortex:record-reviewer", "general-purpose"):
            self.assertEqual((0, ""), run_script(call("gh issue comment 5 --body hi", agent=agent), self.env), agent)
        self.assertEqual((0, ""), run_script(None, self.env, raw="not json at all"))
        self.assertEqual((0, ""), run_script(None, self.env, raw=""))

    def test_an_input_that_cannot_be_read_is_a_denial_for_the_ask_agent_only(self):
        code, out = run_script(None, self.env, raw='{"agent_type": "%s", "tool_input": {"command": ' % ASK)
        self.assertEqual((0, pretool_guard.UNREADABLE), (code, reason(out)))
        self.assertEqual((0, ""), run_script(None, self.env, raw='{"agent_type": "general-purpose", "tool_input": {"command"'))

    def test_without_python_the_ask_agent_is_stopped_and_nothing_else_is(self):
        with tempfile.TemporaryDirectory() as bin_folder:
            for name in ("cat", "dirname"):
                os.symlink(subprocess.run(["which", name], capture_output=True, text=True).stdout.strip(),
                           os.path.join(bin_folder, name))
            env = dict(self.env, PATH=bin_folder)
            code, out = run_script(call(ALLOWED[0]), env)
            self.assertEqual((0, pretool_guard.UNREADABLE), (code, reason(out)))   # the same words as Python's own
            self.assertEqual((0, ""), run_script(call("echo hi", agent=None), env))
            self.assertEqual((0, ""), run_script(call("echo hi", agent="general-purpose"), env))


class RegistrationTests(unittest.TestCase):
    def test_the_guard_is_registered_before_every_bash_call_in_the_plugin_and_in_the_snippet(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as handle:
            pre = json.load(handle)["hooks"]["PreToolUse"]
        self.assertEqual(["Bash"], [entry["matcher"] for entry in pre])
        commands = [h["command"] for h in pre[0]["hooks"]]
        self.assertIn('"${CLAUDE_PLUGIN_ROOT}"/skills/recortex/scripts/run_pretool_guard.sh', commands)
        self.assertTrue(all(h["timeout"] <= 5 for h in pre[0]["hooks"]))
        self.assertTrue(os.access(GUARD, os.X_OK))
        with open(os.path.join(ts.TEMPLATES, "settings-hook-snippet.json")) as handle:
            snippet = [h["command"] for h in json.load(handle)["hooks"]["PreToolUse"][0]["hooks"]]
        self.assertIn('"$HOME/.claude/skills/recortex/scripts/run_pretool_guard.sh"', snippet)

    def test_the_agent_says_the_hook_enforces_what_it_may_run(self):
        with open(os.path.join(ROOT, "agents", "ask.md")) as handle:
            text = " ".join(handle.read().split())
        self.assertIn("tools: Bash", text)
        self.assertIn("A hook stops any other command", text)


if __name__ == "__main__":
    unittest.main()
