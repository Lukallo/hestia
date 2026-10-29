"""Tests for the hestia engine. Nothing mocked: real git repos in temp dirs.

The one stand-in is the Windows python3 placeholder in TestLauncher, which
Linux and macOS have no real copy of.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "plugins" / "hestia" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import hestia  # noqa: E402


class TempHome(unittest.TestCase):
    """Every test gets its own HESTIA_HOME and log dir."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.home = self.tmp / "home"
        self.logs = self.tmp / "logs"
        self.logs.mkdir(parents=True)
        self._env = dict(os.environ)
        os.environ["HESTIA_HOME"] = str(self.home)
        os.environ["HESTIA_LOG_DIR"] = str(self.logs)
        # A person is at the session unless a test says otherwise, so the
        # handover answers the same inside a terminal, CI or claude -p.
        os.environ["HESTIA_SESSION_KIND"] = "interactive"
        os.environ["HESTIA_CLAUDE_DIR"] = str(self.tmp / "claude")
        os.environ["HESTIA_CLAUDE_JSON"] = str(self.tmp / "claude.json")
        os.environ["HESTIA_MEMORY_ROOT"] = str(self.tmp / "memory-root")
        os.environ.pop("CLAUDE_CODE_DISABLE_AUTO_MEMORY", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self._dir.cleanup()

    def make_repo(self, name="repo"):
        repo = self.tmp / name
        repo.mkdir()
        env = dict(os.environ)
        env.update(
            {
                "GIT_AUTHOR_NAME": "test",
                "GIT_COMMITTER_NAME": "test",
                "GIT_AUTHOR_EMAIL": "test@hestia.local",
                "GIT_COMMITTER_EMAIL": "test@hestia.local",
            }
        )
        run = lambda args: subprocess.run(
            ["git"] + args, cwd=str(repo), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        run(["init", "-q"])
        (repo / "a.txt").write_text("one\n")
        run(["add", "."])
        run(["commit", "-q", "-m", "first"])
        return repo


class TestResumeTime(TempHome):
    def test_skips_the_weekend(self):
        cfg = {"resume_hour": 8, "working_days": [0, 1, 2, 3, 4]}
        friday = datetime(2026, 10, 30, 21, 0).astimezone()  # a Friday evening
        self.assertEqual(friday.weekday(), 4)
        resume = hestia.next_resume(friday, cfg)
        self.assertEqual(resume.weekday(), 0, "should land on Monday")
        self.assertEqual(resume.hour, 8)

    def test_plain_weeknight_is_tomorrow(self):
        cfg = {"resume_hour": 8, "working_days": [0, 1, 2, 3, 4]}
        tuesday = datetime(2026, 10, 27, 22, 30).astimezone()
        resume = hestia.next_resume(tuesday, cfg)
        self.assertEqual(resume.date(), (tuesday + timedelta(days=1)).date())

    def test_until_accepts_a_clock_time(self):
        now = datetime(2026, 10, 27, 22, 0).astimezone()
        until = hestia.parse_until("06:30", now, {})
        self.assertEqual((until.hour, until.minute), (6, 30))
        self.assertGreater(until, now, "a time already past means tomorrow")

    def test_after_midnight_this_morning_counts(self):
        cfg = {"resume_hour": 8, "working_days": [0, 1, 2, 3, 4]}
        tuesday = datetime(2026, 10, 27, 1, 0).astimezone()
        resume = hestia.next_resume(tuesday, cfg)
        self.assertEqual(resume.date(), tuesday.date(), "should not skip Tuesday")
        self.assertEqual(resume.hour, 8)
        saturday = datetime(2026, 10, 31, 1, 0).astimezone()
        self.assertEqual(hestia.next_resume(saturday, cfg).weekday(), 0, "still waits for Monday")

    def test_until_refuses_an_impossible_time(self):
        now = datetime(2026, 10, 27, 22, 0).astimezone()
        for value in ("25:00", "12:60"):
            with self.assertRaises(SystemExit):
                hestia.parse_until(value, now, {})

    def test_a_clock_change_does_not_shift_the_morning(self):
        if not hasattr(time, "tzset"):
            self.skipTest("time.tzset is POSIX only")
        os.environ["TZ"] = "Europe/Dublin"  # tearDown restores the environment
        time.tzset()
        self.addCleanup(time.tzset)
        before = datetime(2026, 10, 23, 12, 0).astimezone().utcoffset()
        after = datetime(2026, 10, 26, 12, 0).astimezone().utcoffset()
        if before == after:
            self.skipTest("no zone data for Europe/Dublin")
        cfg = {"resume_hour": 8, "working_days": [0, 1, 2, 3, 4]}
        # Clocks go back on Sunday 25 Oct 2026, between Friday and Monday.
        friday = datetime(2026, 10, 23, 19, 0).astimezone()
        resume = hestia.next_resume(friday, cfg).astimezone()
        self.assertEqual((resume.weekday(), resume.hour), (0, 8))
        saturday = datetime(2026, 10, 24, 23, 0).astimezone()
        until = hestia.parse_until("08:00", saturday, cfg).astimezone()
        self.assertEqual((until.day, until.hour), (25, 8))


class TestPromptParsing(TempHome):
    def test_only_typed_prompts_count(self):
        self.assertEqual(hestia._plain_text("fix the login bug"), "fix the login bug")
        # Tool results arrive as a list of blocks, not a string.
        self.assertIsNone(hestia._plain_text([{"type": "tool_result", "content": "ok"}]))
        # Slash-command plumbing is wrapped in tags.
        self.assertIsNone(hestia._plain_text("<command-name>/clear</command-name>"))
        self.assertIsNone(hestia._plain_text("   "))

    def test_reads_real_log_entries_and_skips_sidechains(self):
        project = self.logs / "-Users-someone-code"
        project.mkdir()
        now = datetime.now().astimezone()
        entries = [
            {"type": "user", "timestamp": now.isoformat(), "cwd": "/Users/someone/code",
             "message": {"content": "first thing"}},
            {"type": "user", "timestamp": now.isoformat(), "cwd": "/Users/someone/code",
             "isSidechain": True, "message": {"content": "subagent chatter"}},
            {"type": "assistant", "timestamp": now.isoformat(),
             "message": {"content": "not a prompt"}},
            {"type": "user", "timestamp": now.isoformat(), "cwd": "/Users/someone/code",
             "message": {"content": [{"type": "tool_result", "content": "x"}]}},
        ]
        with (project / "session.jsonl").open("w") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")

        prompts = hestia.read_prompts(now - timedelta(hours=1))
        self.assertEqual([p["text"] for p in prompts], ["first thing"])

    def test_break_detection(self):
        base = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0)
        prompts = [
            {"at": base, "cwd": "/a", "text": "x", "branch": ""},
            {"at": base + timedelta(minutes=5), "cwd": "/a", "text": "x", "branch": ""},
            {"at": base + timedelta(minutes=95), "cwd": "/a", "text": "x", "branch": ""},
        ]
        load = hestia.day_load(prompts)
        self.assertEqual(load["prompts"], 3)
        self.assertEqual(load["breaks"], 1)
        self.assertEqual(load["last_break_minutes"], 90)
        self.assertAlmostEqual(load["span_hours"], 1.6, places=1)

    def test_no_break_when_nothing_long_enough(self):
        base = datetime.now().astimezone()
        prompts = [
            {"at": base + timedelta(minutes=i * 5), "cwd": "/a", "text": "x", "branch": ""}
            for i in range(6)
        ]
        self.assertEqual(hestia.day_load(prompts)["breaks"], 0)


class TestSnapshot(TempHome):
    def test_snapshot_leaves_the_repo_byte_identical(self):
        repo = self.make_repo()
        (repo / "a.txt").write_text("one, edited\n")
        (repo / "new.txt").write_text("never committed\n")

        head_before = hestia.git(repo, ["rev-parse", "HEAD"]).out
        status_before = hestia.git(repo, ["status", "--porcelain"]).out
        index_before = hestia.index_fingerprint(repo)

        info = hestia.snapshot_repo(repo, "refs/hestia/test", "snapshot")

        self.assertIsNone(info["error"])
        self.assertEqual(info["dirty_files"], 2)
        self.assertTrue(info["untouched"])
        self.assertEqual(hestia.git(repo, ["rev-parse", "HEAD"]).out, head_before)
        self.assertEqual(hestia.git(repo, ["status", "--porcelain"]).out, status_before)
        self.assertEqual(hestia.index_fingerprint(repo), index_before)

    def test_snapshot_can_be_recovered(self):
        repo = self.make_repo()
        (repo / "new.txt").write_text("never committed\n")
        hestia.snapshot_repo(repo, "refs/hestia/test", "snapshot")
        shown = hestia.git(repo, ["show", "refs/hestia/test:new.txt"]).out
        self.assertEqual(shown, "never committed")

    def test_clean_repo_makes_no_ref(self):
        repo = self.make_repo()
        info = hestia.snapshot_repo(repo, "refs/hestia/test", "snapshot")
        self.assertTrue(info["clean"])
        self.assertIsNone(info["ref"])
        self.assertFalse(hestia.git(repo, ["rev-parse", "refs/hestia/test"]).ok)


class TestPauseAndHook(TempHome):
    def hook(self, payload):
        script = SCRIPTS / "hestia.py"
        proc = subprocess.run(
            [sys.executable, str(script), "hook"],
            input=json.dumps(payload).encode(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        out = proc.stdout.decode().strip()
        if not out:
            return "allow"
        return json.loads(out)["hookSpecificOutput"]["permissionDecision"]

    def pause_until(self, when):
        hestia.write_json(hestia.pause_path(), {"until": when.isoformat()})

    def test_silent_when_not_paused(self):
        self.assertEqual(self.hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}), "allow")

    def test_blocks_while_paused(self):
        self.pause_until(datetime.now().astimezone() + timedelta(hours=8))
        self.assertEqual(self.hook({"tool_name": "Bash", "tool_input": {"command": "npm test"}}), "deny")
        self.assertEqual(self.hook({"tool_name": "Edit", "tool_input": {"file_path": "a.py"}}), "deny")

    def test_never_blocks_the_way_back_in(self):
        self.pause_until(datetime.now().astimezone() + timedelta(hours=8))
        for command in [
            "python3 hestia.py back",
            'python3 "/plugins/hestia/scripts/hestia.py" back',
            "python hestia.py today",
            "python3 hestia.py resume",  # old alias must still get through
            "python hestia.py status",
            "python3 hestia.py dashboard --no-open",  # read-only, so allowed too
            'sh "/plugins/hestia/scripts/hestia" back',  # the launcher
            "sh '/plugins/hestia/scripts/hestia' today",
            'sh "${CLAUDE_PLUGIN_ROOT}/scripts/hestia" back',  # as back.md writes it
            'bash "/plugins/hestia/scripts/hestia" today',
            'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hestia.py" back',  # without the launcher
            "python3 hestia.py today --json 2>&1",
            "py -3 hestia.py back",
            "./hestia.py back",
            'sh "/plugins/hestia/scripts/hestia" dream restore',  # undo works while paused
        ]:
            self.assertEqual(
                self.hook({"tool_name": "Bash", "tool_input": {"command": command}}),
                "allow",
                "pause must not block: %s" % command,
            )

    def test_way_back_in_from_powershell(self):
        # Claude Code uses a PowerShell tool on Windows without Git Bash.
        self.pause_until(datetime.now().astimezone() + timedelta(hours=8))
        for command in [
            'python "$env:CLAUDE_PLUGIN_ROOT\\scripts\\hestia.py" back',
            '& python "C:\\Users\\me\\.claude\\plugins\\hestia\\scripts\\hestia.py" back',
            '& "C:\\Python314\\python.exe" "C:\\hestia\\hestia.py" today',
            'python "C:\\Users\\me\\.claude\\plugins\\hestia\\scripts\\hestia.py" back',
            "& python 'C:\\Program Files\\hestia\\scripts\\hestia.py' today",
        ]:
            self.assertEqual(
                self.hook({"tool_name": "PowerShell", "tool_input": {"command": command}}),
                "allow",
                "pause must not block: %s" % command,
            )
        self.assertEqual(
            self.hook({"tool_name": "PowerShell", "tool_input": {"command": "Remove-Item build"}}),
            "deny",
        )
        self.assertEqual(
            self.hook({"tool_name": "PowerShell", "tool_input": {"command": "npm test"}}),
            "deny",
        )

    def test_only_the_whole_command_gets_through(self):
        self.pause_until(datetime.now().astimezone() + timedelta(hours=8))
        for command in [
            "python3 hestia.py today && git push",
            "python3 hestia.py back; npm publish",
            "python3 hestia.py status | sh",
            "python3 hestia.py back || npm test",
            "python3 hestia.py back & npm test",
            "echo $(python3 hestia.py status; npm publish)",
            "echo `python3 hestia.py back`",
            "python3 hestia.py back\nrm -rf build",
            "python3 hestia.py today > notes.txt",
            'git commit -m "hestia back online" && git push',
            'git commit -m "hestia back online"',
            'python3 -c "import os" hestia.py back',
            "python3 hestia.py done --keep-agents",  # not a way back in
            "python3 hestia.py break 5m",
            'sh "/plugins/hestia/scripts/hestia" back && git push',
            'sh -c "git push" hestia back',
            "sh deploy.sh hestia back",
        ]:
            self.assertEqual(
                self.hook({"tool_name": "Bash", "tool_input": {"command": command}}),
                "deny",
                "pause must block: %s" % command,
            )

    def test_blocks_connectors_subagents_and_other_plugins(self):
        self.pause_until(datetime.now().astimezone() + timedelta(hours=8))
        for tool in [
            "mcp__claude_ai_Slack__slack_send_message",  # a claude.ai connector
            "mcp__plugin_linear_linear__create_issue",  # a plugin's own MCP server
            "Agent",  # starting a subagent
            "Skill",
            "WebFetch",
        ]:
            self.assertEqual(
                self.hook({"tool_name": tool, "tool_input": {}}),
                "deny",
                "pause must block: %s" % tool,
            )

    def test_expired_pause_clears_itself(self):
        self.pause_until(datetime.now().astimezone() - timedelta(minutes=1))
        self.assertEqual(self.hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}), "allow")
        self.assertFalse(hestia.pause_path().exists())


class TestAgentDetection(TempHome):
    def test_recognises_claude_processes(self):
        self.assertTrue(hestia._is_claude("claude --print 'hi'"))
        self.assertTrue(hestia._is_claude("/usr/local/bin/claude"))
        self.assertTrue(hestia._is_claude("node /opt/claude/cli.js"))
        self.assertFalse(hestia._is_claude("vim notes.md"))
        # The desktop app's own path contains 'Claude' but isn't an agent.
        self.assertFalse(
            hestia._is_claude("/Applications/Claude.app/Contents/MacOS/Electron")
        )
        self.assertFalse(hestia._is_claude(""))

    def test_never_lists_our_own_process(self):
        pids = [agent["pid"] for agent in hestia.running_agents()]
        self.assertNotIn(os.getpid(), pids)

    def test_headless_runs_are_told_apart_from_sessions(self):
        self.assertTrue(any(m in "claude --print 'x' " for m in hestia.HEADLESS_MARKERS))
        self.assertFalse(any(m in "claude " for m in hestia.HEADLESS_MARKERS))


class TestMorningHandover(TempHome):
    def session_start(self, event="session-start", kind="interactive"):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "hestia.py"), event],
            input=b'{"session_id": "test", "cwd": "/tmp"}',
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=dict(os.environ, HESTIA_SESSION_KIND=kind),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        out = proc.stdout.decode().strip()
        if not out:
            return ""
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]

    def write_list(self, text="# Tomorrow\n\nstart with the parser\n"):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "TOMORROW.md").write_text(text)

    def test_nothing_to_hand_over(self):
        self.assertEqual(self.session_start(), "")

    def test_hands_over_once_then_stays_quiet(self):
        self.write_list()
        first = self.session_start()
        self.assertIn("start with the parser", first)
        self.assertEqual(self.session_start(), "", "a second session must get nothing")

    def test_stays_quiet_while_still_paused(self):
        self.write_list()
        hestia.write_json(
            hestia.pause_path(),
            {"until": (datetime.now().astimezone() + timedelta(hours=8)).isoformat()},
        )
        self.assertEqual(self.session_start(), "")

    def test_a_new_list_re_arms_the_handover(self):
        self.write_list()
        self.assertIn("parser", self.session_start())
        self.assertEqual(self.session_start(), "")
        self.write_list("# Tomorrow\n\nstart with the importer\n")
        self.assertIn("importer", self.session_start(), "a fresh shutdown re-arms it")


    def test_a_window_left_open_gets_it_on_its_next_prompt(self):
        self.write_list()
        self.assertIn("start with the parser", self.session_start("prompt-submit"))
        self.assertEqual(self.session_start("prompt-submit"), "", "once, not on every prompt")
        self.assertEqual(self.session_start(), "", "and not again in a new session")

    def test_scripted_runs_never_take_it(self):
        self.write_list()
        self.assertEqual(self.session_start(kind="headless"), "")
        self.assertEqual(self.session_start("prompt-submit", kind="headless"), "")
        self.assertIn("parser", self.session_start(), "still there for the person")

    def test_headless_is_read_from_what_claude_code_tells_hooks(self):
        for name in ("HESTIA_SESSION_KIND", "CLAUDE_CODE_SESSION_ATTENDED",
                     "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PID"):
            os.environ.pop(name, None)
        os.environ["CLAUDE_CODE_SESSION_ATTENDED"] = "0"
        self.assertTrue(hestia.session_is_headless())
        os.environ["CLAUDE_CODE_SESSION_ATTENDED"] = "1"
        self.assertFalse(hestia.session_is_headless())
        os.environ.pop("CLAUDE_CODE_SESSION_ATTENDED")
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "sdk-cli"
        self.assertTrue(hestia.session_is_headless())
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "cli"
        self.assertFalse(hestia.session_is_headless())
        os.environ.pop("CLAUDE_CODE_ENTRYPOINT")
        self.assertFalse(hestia.session_is_headless(), "unknown means a person is there")

    def test_sessions_starting_together_get_it_once(self):
        self.write_list()
        racers = [
            subprocess.Popen(
                [sys.executable, str(SCRIPTS / "hestia.py"), "session-start" if i % 2 else "prompt-submit"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=dict(os.environ),
            )
            for i in range(12)
        ]
        outputs = [racer.communicate()[0].decode().strip() for racer in racers]
        self.assertEqual(len([o for o in outputs if o]), 1)

    def test_a_list_from_before_claims_existed_stays_handed_over(self):
        self.write_list()
        tomorrow = self.home / "TOMORROW.md"
        hestia.write_json(self.home / "handover.json",
                          {"for": tomorrow.stat().st_mtime, "consumed": True})
        self.assertEqual(self.session_start(), "")

    def test_again_re_arms_it(self):
        self.write_list()
        self.assertIn("parser", self.session_start())
        hestia.main(["handover", "--again"])
        self.assertIn("parser", self.session_start())

    def test_normal_day_costs_nothing(self):
        self.assertIsNone(hestia.handover_due())
        self.assertEqual(self.session_start("prompt-submit"), "")


class TestOvernightFact(TempHome):
    def log(self, entries):
        project = self.logs / "-tmp-proj"
        project.mkdir(exist_ok=True)
        with (project / "night.jsonl").open("a") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")

    def tool_use(self, when, use_id, command="npm test"):
        return {"type": "assistant", "timestamp": when.isoformat(), "cwd": "/tmp/proj",
                "message": {"content": [{"type": "tool_use", "id": use_id, "name": "Bash",
                                         "input": {"command": command}}]}}

    def denied(self, when, use_id):
        return {"type": "user", "timestamp": when.isoformat(),
                "message": {"content": [{"type": "tool_result", "tool_use_id": use_id, "is_error": True,
                                         "content": "Clocked out until Fri 30 Oct 08:00."}]}}

    def setUp(self):
        super().setUp()
        self.since = datetime.now().astimezone() - timedelta(hours=10)
        self.due = {"since": self.since.isoformat()}

    def test_nothing_ran(self):
        fact = hestia.overnight_fact(self.due)
        self.assertTrue(fact.startswith("Overnight: nothing ran between"), fact)

    def test_blocked_calls_are_the_pause_working(self):
        at = self.since + timedelta(hours=3)
        self.log([self.tool_use(at, "a"), self.denied(at, "a")])
        fact = hestia.overnight_fact(self.due)
        self.assertIn("nothing ran", fact)
        self.assertIn("The pause blocked 1 tool call.", fact)

    def test_work_that_ran_is_reported_plainly(self):
        at = self.since + timedelta(hours=4)
        self.log([self.tool_use(at, "b")])
        fact = hestia.overnight_fact(self.due)
        self.assertIn("1 tool call ran", fact)
        self.assertIn(at.strftime("%H:%M"), fact)

    def test_coming_back_is_not_work(self):
        at = self.since + timedelta(hours=1)
        self.log([self.tool_use(at, "c", command='python3 "/x/hestia.py" back')])
        self.assertIn("nothing ran", hestia.overnight_fact(self.due))

    def test_before_shutdown_does_not_count(self):
        self.log([self.tool_use(self.since - timedelta(minutes=5), "d")])
        self.assertIn("nothing ran", hestia.overnight_fact(self.due))


class TestTheListCarriesTheThread(TempHome):
    def test_start_here_sessions_and_last_commit(self):
        repo = self.make_repo()
        (repo / "wip.txt").write_text("half done\n")
        now = datetime.now().astimezone()
        snap = hestia.snapshot_repo(repo, "refs/hestia/t", "t")
        prompts = [
            {"at": now - timedelta(hours=2), "cwd": str(repo), "branch": "main",
             "text": "add the parser", "session": "sess-old"},
            {"at": now - timedelta(minutes=5), "cwd": str(repo), "branch": "main",
             "text": "make the importer accept semicolons", "session": "sess-new"},
        ]
        load = hestia.day_load(prompts)
        path = hestia.write_tomorrow(now, "for the ops team", [snap], load, prompts,
                                     next_step="finish the semicolon importer test")
        text = path.read_text()
        self.assertEqual(hestia.start_here(text), "finish the semicolon importer test")
        self.assertLess(text.index("## Start here"), text.index("## Where you left off"))
        self.assertIn("last commit: first", text)
        self.assertIn("claude --resume sess-new", text)
        self.assertLess(text.index("sess-new"), text.index("sess-old"), "newest session first")

    def test_back_takes_the_handover_and_says_where_to_start(self):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "TOMORROW.md").write_text("# Tomorrow\n\n## Start here\n\n- rerun the importer\n")
        an_hour_ago = datetime.now().astimezone() - timedelta(hours=1)
        hestia.arm_handover(self.home / "TOMORROW.md", since=an_hour_ago)
        hestia.write_json(hestia.pause_path(), {
            "kind": "done", "since": an_hour_ago.isoformat(),
            "until": (datetime.now().astimezone() + timedelta(hours=8)).isoformat()})
        proc = subprocess.run([sys.executable, str(SCRIPTS / "hestia.py"), "back"],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=dict(os.environ))
        out = proc.stdout.decode()
        self.assertIn("Start here: rerun the importer", out)
        self.assertIn("Overnight: nothing ran", out)
        self.assertIsNone(hestia.handover_due(), "the next prompt must not get it again")


class TestShutdownEndToEnd(TempHome):
    def test_shutdown_saves_pauses_and_writes_the_list(self):
        repo = self.make_repo()
        (repo / "wip.txt").write_text("half done\n")
        cwd = os.getcwd()
        os.chdir(str(repo))
        try:
            args = hestia.argparse.Namespace(
                note="keeping the lights on",
                until="",
                json=True,
                keep_agents=True,  # never touch real processes from a test
                stop_all=False,
            )
            self.assertEqual(hestia.cmd_shutdown(args), 0)
        finally:
            os.chdir(cwd)

        pause = hestia.read_pause()
        self.assertIsNotNone(pause, "shutdown must leave a pause in place")
        tomorrow = self.home / "TOMORROW.md"
        self.assertTrue(tomorrow.exists())
        text = tomorrow.read_text()
        self.assertIn("keeping the lights on", text)
        self.assertIn("Where you left off", text)
        self.assertIn("refs/hestia/", text)


class TestBreak(TempHome):
    """A break is the same pause with kind=break: no list, lifts itself."""

    def take_break(self, repo, length="30m"):
        cwd = os.getcwd()
        os.chdir(str(repo))
        try:
            # break never signals anything unless --stop-agents is passed;
            # it is left off here so a test can't touch real processes.
            args = hestia.argparse.Namespace(length=length, json=True, stop_agents=False)
            return hestia.cmd_break(args)
        finally:
            os.chdir(cwd)

    def hook_reason(self, payload):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "hestia.py"), "hook"],
            input=json.dumps(payload).encode(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ),
        )
        out = proc.stdout.decode().strip()
        return json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"] if out else ""

    def test_lengths_it_understands(self):
        now = datetime(2026, 10, 29, 12, 0).astimezone()
        self.assertEqual(hestia.parse_break("", now), now + timedelta(minutes=45))
        self.assertEqual(hestia.parse_break("45m", now), now + timedelta(minutes=45))
        self.assertEqual(hestia.parse_break("1h", now), now + timedelta(hours=1))
        self.assertEqual(hestia.parse_break("1h30m", now), now + timedelta(minutes=90))
        self.assertEqual(hestia.parse_break("90", now), now + timedelta(minutes=90))
        self.assertEqual(hestia.parse_break("13:50", now).strftime("%H:%M"), "13:50")

    def test_refuses_lengths_that_are_not_a_break(self):
        now = datetime(2026, 10, 29, 12, 0).astimezone()
        for bad in ["soon", "0m", "11:00", "5h", "25:00"]:
            with self.assertRaises(SystemExit, msg=bad):
                hestia.parse_break(bad, now)

    def test_break_saves_pauses_and_leaves_the_repo_alone(self):
        repo = self.make_repo()
        (repo / "wip.txt").write_text("mid-thought\n")
        index_before = hestia.index_fingerprint(repo)
        status_before = hestia.git(repo, ["status", "--porcelain"]).out

        self.assertEqual(self.take_break(repo), 0)

        pause = hestia.read_pause()
        self.assertEqual(pause["kind"], "break")
        self.assertEqual(len(pause["saved"]), 1)
        self.assertIn("refs/hestia/", pause["saved"][0]["restore"])
        self.assertEqual(hestia.index_fingerprint(repo), index_before)
        self.assertEqual(hestia.git(repo, ["status", "--porcelain"]).out, status_before)

    def test_break_writes_no_list_and_leaves_the_handover_alone(self):
        repo = self.make_repo()
        self.home.mkdir(parents=True, exist_ok=True)
        handover = self.home / "handover.json"
        handover.write_text('{"for": 1, "consumed": false}\n')
        self.take_break(repo)
        self.assertFalse((self.home / "TOMORROW.md").exists())
        self.assertEqual(handover.read_text(), '{"for": 1, "consumed": false}\n')

    def test_hook_names_the_break_and_still_lets_back_through(self):
        self.take_break(self.make_repo())
        reason = self.hook_reason({"tool_name": "Edit", "tool_input": {"file_path": "a.py"}})
        self.assertIn("On a break until", reason)
        self.assertEqual(
            self.hook_reason({"tool_name": "Bash", "tool_input": {"command": "python3 hestia.py back"}}),
            "",
        )

    def test_back_gives_back_where_you_were(self):
        repo = self.make_repo()
        (repo / "wip.txt").write_text("mid-thought\n")
        self.take_break(repo)
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "hestia.py"), "back"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ),
        )
        out = proc.stdout.decode()
        self.assertIn("On a break since", out)
        self.assertIn("Before the break: git -C", out)
        self.assertNotIn("Yesterday's list", out)
        self.assertIsNone(hestia.read_pause())

    def test_a_break_lifts_itself(self):
        hestia.write_json(
            hestia.pause_path(),
            {"kind": "break", "until": (datetime.now().astimezone() - timedelta(minutes=1)).isoformat()},
        )
        self.assertIsNone(hestia.read_pause())
        self.assertFalse(hestia.pause_path().exists())

    def test_a_break_cannot_shorten_the_night(self):
        night = datetime.now().astimezone() + timedelta(hours=10)
        hestia.write_json(hestia.pause_path(), {"kind": "done", "until": night.isoformat()})
        self.take_break(self.make_repo(), "15m")
        pause = hestia.read_pause()
        self.assertEqual(pause["kind"], "done")
        self.assertEqual(pause["_until"].replace(microsecond=0), night.replace(microsecond=0))

    def test_old_pause_files_still_count_as_done(self):
        hestia.write_json(
            hestia.pause_path(),
            {"until": (datetime.now().astimezone() + timedelta(hours=8)).isoformat()},
        )
        self.assertEqual(hestia.read_pause()["kind"], "done")


class TestWithoutPs(TempHome):
    """Windows has no `ps`. Finding agents comes back empty instead of crashing."""

    def setUp(self):
        super().setUp()
        git_path = shutil.which("git")
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        git_dir = os.path.dirname(git_path) if git_path else ""
        if os.name == "nt" and git_dir and not shutil.which("ps", path=git_dir):
            # git.exe is a launcher that finds the rest of Git from its own
            # folder, so a symlink to it can't work. Git's folder has no ps.
            bin_dir = Path(git_dir)
        else:
            try:
                os.symlink(git_path, str(bin_dir / os.path.basename(git_path)))
            except (OSError, TypeError):
                self.tearDown()  # a skip from setUp never reaches tearDown
                self.skipTest("needs git and symlinks")
        # git and nothing else, so `ps` is missing however this machine is set up.
        os.environ["PATH"] = str(bin_dir)

    def run_in(self, repo, command, args):
        cwd = os.getcwd()
        os.chdir(str(repo))
        try:
            return command(hestia.argparse.Namespace(**args))
        finally:
            os.chdir(cwd)

    def test_no_ps_means_no_agents(self):
        self.assertEqual(hestia.running_agents(), [])

    def test_done_still_saves_and_pauses(self):
        repo = self.make_repo()
        (repo / "wip.txt").write_text("half done\n")
        code = self.run_in(repo, hestia.cmd_shutdown, {
            "note": "", "until": "", "json": True,
            "keep_agents": True,  # never touch real processes from a test
            "stop_all": False,
        })
        self.assertEqual(code, 0)
        self.assertEqual(hestia.read_pause()["kind"], "done")
        self.assertIn("refs/hestia/", (self.home / "TOMORROW.md").read_text())

    def test_break_still_saves_and_pauses(self):
        repo = self.make_repo()
        (repo / "wip.txt").write_text("mid-thought\n")
        code = self.run_in(repo, hestia.cmd_break, {
            "length": "30m", "json": True, "stop_agents": False,
        })
        self.assertEqual(code, 0)
        pause = hestia.read_pause()
        self.assertEqual(pause["kind"], "break")
        self.assertEqual(len(pause["saved"]), 1)


@unittest.skipIf(os.name == "nt" or not shutil.which("sh"), "the launcher runs under sh")
class TestLauncher(TempHome):
    """scripts/hestia picks a Python that runs. CI also runs it in Git Bash on Windows."""

    def setUp(self):
        super().setUp()
        self.sh = shutil.which("sh")
        # The Windows python3 placeholder: on PATH, but it only prints where to
        # get Python. It is the one stand-in here, since Linux and macOS have none.
        self.store = self.tmp / "WindowsApps"
        self.store.mkdir()
        self.write_exe(self.store / "python3", (
            "#!/bin/sh\n"
            "echo 'Python was not found; run without arguments to install"
            " from the Microsoft Store.' >&2\n"
            "exit 49\n"
        ))
        self.real = self.tmp / "bin"
        self.real.mkdir()
        try:
            os.symlink(sys.executable, str(self.real / "python"))
        except OSError:
            self.tearDown()  # a skip from setUp never reaches tearDown
            self.skipTest("needs symlinks")

    def write_exe(self, path, text):
        path.write_text(text)
        path.chmod(0o755)

    def launch(self, args, path, payload=None):
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join(str(p) for p in path)
        return subprocess.run(
            [self.sh, str(SCRIPTS / "hestia")] + args,
            input=json.dumps(payload).encode() if payload else b"",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )

    def test_runs_past_the_store_placeholder(self):
        proc = self.launch(["today", "--json"], [self.store, self.real])
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertIn("paused", json.loads(proc.stdout.decode()))

    def test_the_hook_still_reads_its_input(self):
        hestia.write_json(
            hestia.pause_path(),
            {"until": (datetime.now().astimezone() + timedelta(hours=8)).isoformat()},
        )
        path = [self.store, self.real]
        blocked = self.launch(["hook"], path, {"tool_name": "Edit", "tool_input": {}})
        self.assertIn('"deny"', blocked.stdout.decode())
        way_back = {"tool_name": "Bash", "tool_input": {
            "command": 'sh "%s" back' % (SCRIPTS / "hestia")}}
        self.assertEqual(self.launch(["hook"], path, way_back).stdout, b"")

    def test_a_python3_that_runs_is_used(self):
        # The Store's own Python also lives in WindowsApps, and works.
        os.unlink(str(self.store / "python3"))
        os.symlink(sys.executable, str(self.store / "python3"))
        proc = self.launch(["today", "--json"], [self.store])
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())

    def test_says_so_when_there_is_no_python(self):
        proc = self.launch(["hook"], [self.store])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("needs Python 3", proc.stderr.decode())
        self.assertEqual(proc.stdout, b"")


class TestWhy(TempHome):
    def setUp(self):
        super().setUp()
        # why reports only your own commits; make_repo commits as this person.
        os.environ["GIT_AUTHOR_NAME"] = "test"
        os.environ["GIT_AUTHOR_EMAIL"] = "test@hestia.local"

    def mine(self, facts, repo):
        return [r for r in facts["repos"] if Path(r["path"]).resolve() == Path(repo).resolve()]

    def test_leaves_out_other_peoples_commits(self):
        repo = self.make_repo()
        env = dict(os.environ, GIT_AUTHOR_NAME="colleague", GIT_AUTHOR_EMAIL="them@elsewhere",
                   GIT_COMMITTER_NAME="colleague", GIT_COMMITTER_EMAIL="them@elsewhere")
        (repo / "theirs.txt").write_text("x\n")
        subprocess.run(["git", "add", "."], cwd=str(repo), env=env)
        subprocess.run(["git", "commit", "-q", "-m", "their change"], cwd=str(repo), env=env)
        facts = hestia.gather_why(extra_repos=[str(repo)])
        self.assertEqual([c["subject"] for c in self.mine(facts, repo)[0]["commits"]], ["first"])

    def test_reads_todays_commits_but_never_the_snapshots(self):
        repo = self.make_repo()
        (repo / "wip.txt").write_text("not committed\n")
        hestia.snapshot_repo(repo, "refs/hestia/test", "hestia snapshot")
        facts = hestia.gather_why(extra_repos=[str(repo)])
        mine = self.mine(facts, repo)
        self.assertEqual(len(mine), 1)
        self.assertEqual([c["subject"] for c in mine[0]["commits"]], ["first"])
        self.assertEqual(mine[0]["uncommitted_files"], 1)

    def test_carries_the_last_shutdown_note(self):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "TOMORROW.md").write_text("# Tomorrow\n\n> unblocking the ops import\n")
        self.assertEqual(hestia.gather_why()["note"], "unblocking the ops import")


class TestNew(TempHome):
    CHANGELOG = (
        "# Changelog\n\n## 2.0.10\n\n- Added a thing\n  that wraps\n- Fixed another\n\n"
        "## 2.0.9\n\n- Middle\n\n## 2.0.1\n\n- Old\n"
    )

    def serve(self, text):
        path = self.tmp / "CHANGELOG.md"
        path.write_text(text)
        os.environ["HESTIA_CHANGELOG_URL"] = path.resolve().as_uri()

    def test_parses_releases_and_wrapped_lines(self):
        releases = hestia.parse_changelog(self.CHANGELOG)
        self.assertEqual([v for v, _ in releases], ["2.0.10", "2.0.9", "2.0.1"])
        self.assertEqual(releases[0][1], ["Added a thing that wraps", "Fixed another"])

    def test_versions_compare_as_numbers(self):
        self.assertGreater(hestia.version_key("2.0.10"), hestia.version_key("2.0.9"))
        self.assertEqual(hestia.version_key("2.1.312 (Claude Code)"), (2, 1, 312))

    def test_only_what_changed_since(self):
        self.serve(self.CHANGELOG)
        facts = hestia.gather_new(since="2.0.1")
        self.assertTrue(facts["ok"])
        self.assertEqual([r["version"] for r in facts["releases"]], ["2.0.10", "2.0.9"])
        self.assertEqual(hestia.read_json(self.home / "digest.json")["seen"], "2.0.10")

    def test_nothing_when_nothing_changed(self):
        self.serve(self.CHANGELOG)
        self.assertEqual(hestia.gather_new(since="2.0.10")["releases"], [])

    def test_offline_is_not_an_error(self):
        os.environ["HESTIA_CHANGELOG_URL"] = (self.tmp / "missing.md").resolve().as_uri()
        facts = hestia.gather_new(since="1.0.0")
        self.assertFalse(facts["ok"])
        self.assertFalse((self.home / "digest.json").exists(), "a failed check records nothing")

    def test_bounded_however_far_behind(self):
        many = "# Changelog\n\n" + "".join(
            "## 1.0.%d\n\n%s\n" % (n, "\n".join("- change %d" % i for i in range(40)))
            for n in range(50, 0, -1)
        )
        self.serve(many)
        facts = hestia.gather_new(since="0.1.0")
        total = sum(len(r["changes"]) for r in facts["releases"])
        self.assertLessEqual(len(facts["releases"]), hestia.NEW_MAX_RELEASES)
        self.assertLessEqual(total, hestia.NEW_MAX_CHANGES)
        self.assertGreater(facts["left_out"], 0)
        # A release cut short by the budget still reports its real size.
        self.assertTrue(all(r["total_changes"] == 40 for r in facts["releases"]))
class TestClaudeMdSection(TempHome):
    def setUp(self):
        super().setUp()
        self.md = self.tmp / "claude" / "CLAUDE.md"
        self.md.parent.mkdir()

    def block(self, word):
        return "%s\n%s\n%s" % (hestia.SECTION_START, word, hestia.SECTION_END)

    def test_insert_then_remove_is_byte_identical(self):
        for original in (b"# mine\n", b"no trailing newline", b"crlf\r\nlines\r\n"):
            self.md.write_bytes(original)
            hestia.upsert_marked_section(self.md, self.block("one"))
            self.assertTrue(self.md.read_bytes().startswith(original))
            self.assertTrue(hestia.remove_marked_section(self.md))
            self.assertEqual(self.md.read_bytes(), original)

    def test_replace_only_touches_the_block(self):
        self.md.write_bytes(b"before\n")
        hestia.upsert_marked_section(self.md, self.block("one"))
        with self.md.open("ab") as handle:
            handle.write(b"after\n")
        hestia.upsert_marked_section(self.md, self.block("two"))
        text = self.md.read_bytes().decode()
        self.assertTrue(text.startswith("before\n") and text.endswith("after\n"))
        self.assertIn("two", text)
        self.assertNotIn("one", text)
        self.assertEqual(text.count(hestia.SECTION_START), 1)

    def test_upsert_is_idempotent(self):
        hestia.upsert_marked_section(self.md, self.block("one"))
        self.assertFalse(hestia.upsert_marked_section(self.md, self.block("one")))

    def test_a_file_we_created_is_removed_again(self):
        hestia.upsert_marked_section(self.md, self.block("one"))
        hestia.remove_marked_section(self.md)
        self.assertFalse(self.md.exists())

    def test_changes_are_backed_up(self):
        self.md.write_bytes(b"mine\n")
        hestia.upsert_marked_section(self.md, self.block("one"))
        backups = list((self.home / "backups").glob("CLAUDE.md.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b"mine\n")

    def test_half_a_section_is_refused(self):
        self.md.write_bytes(("x\n%s\nbroken\n" % hestia.SECTION_START).encode())
        with self.assertRaises(ValueError):
            hestia.upsert_marked_section(self.md, self.block("one"))


class TestConnectorDetection(TempHome):
    def test_kinds_and_keys(self):
        self.assertEqual(hestia.classify_connector("claude_ai_Google_Calendar"), "calendar")
        self.assertEqual(hestia.classify_connector("slack"), "chat")
        self.assertEqual(hestia.classify_connector("github"), "code")
        self.assertEqual(hestia.classify_connector("plugin:productivity:linear"), "tasks")
        self.assertEqual(hestia.classify_connector("claude.ai Reclaim.ai"), "calendar")
        self.assertEqual(hestia.classify_connector("claude.ai Microsoft 365"), "calendar")
        self.assertEqual(hestia.classify_connector("claude.ai Todoist"), "tasks")
        self.assertEqual(hestia.classify_connector("weather"), "other")
        self.assertEqual(hestia.connector_key("claude.ai Slack"), "slack")
        self.assertEqual(hestia.display_name("claude_ai_Google_Calendar"), "Google Calendar")
        self.assertEqual(hestia.tool_prefix("claude.ai Slack"), "mcp__claude_ai_Slack__")
        self.assertEqual(hestia.connector_key("claude_ai_Google_Calendar"), "google-calendar")
        self.assertEqual(hestia.tool_prefix("claude_ai_Google_Calendar"), "mcp__claude_ai_Google_Calendar__")

    def test_reads_claude_code_config_and_seen_servers(self):
        project = self.tmp / "proj"
        project.mkdir()
        hestia.write_json(self.tmp / "claude.json", {
            "mcpServers": {"slack": {}},
            "projects": {str(project): {"mcpServers": {"github": {}}}, "/elsewhere": {"mcpServers": {"jira": {}}}},
        })
        hestia.write_json(project / ".mcp.json", {"mcpServers": {"notion": {}}})
        found = hestia.detect_connectors(str(project), ["claude_ai_Gmail"])
        self.assertEqual(sorted(found), ["github", "gmail", "notion", "slack"])
        self.assertEqual(found["gmail"]["source"], "claude.ai")
        self.assertEqual(found["github"]["source"], "local")
        self.assertNotIn("jira", found, "other projects' servers aren't this user's here")


class TestInitWrite(TempHome):
    def write(self, answers, seen=("claude_ai_Google_Calendar", "slack")):
        return hestia.init_write(answers, str(self.tmp), list(seen))

    def test_writes_everything_from_the_answers(self):
        hestia.write_json(self.home / "config.json", {"repos": ["/keep/me"]})
        self.write({
            "why": "making things people use", "helps": "the food bank volunteers", "tone": "warm",
            "resume_hour": 9, "working_days": [0, 1, 2, 3],
            "connectors": {"google-calendar": "allowed", "slack": "declined"},
        })
        cfg = hestia.load_config()
        self.assertEqual((cfg["resume_hour"], cfg["working_days"], cfg["repos"]), (9, [0, 1, 2, 3], ["/keep/me"]))
        state = hestia.read_json(self.home / "connectors.json")
        self.assertEqual(state["connectors"]["google-calendar"]["consent"], "allowed")
        self.assertEqual(state["connectors"]["slack"]["consent"], "declined")
        self.assertNotIn("calendar", state["missing_kinds"])
        self.assertIn("email", state["missing_kinds"])
        text = (self.tmp / "claude" / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("the food bank volunteers", text)
        self.assertIn("Mon-Thu", text)
        self.assertIn("resumes at 09:00", text)
        self.assertIn("Connectors Hestia may use: Google Calendar.", text)

    def test_bad_answers_fall_back_to_defaults(self):
        self.write({"tone": "shouty", "resume_hour": 99, "working_days": ["mon"]}, seen=())
        cfg = hestia.load_config()
        self.assertEqual(cfg["resume_hour"], hestia.DEFAULT_RESUME_HOUR)
        self.assertEqual(hestia.read_json(self.home / "profile.json")["tone"], "plain")

    def test_rerun_keeps_earlier_answers(self):
        self.write({"why": "the craft itself"})
        self.write({"tone": "detail"})
        profile = hestia.read_json(self.home / "profile.json")
        self.assertEqual((profile["why"], profile["tone"]), ("the craft itself", "detail"))

    def test_the_screen_answer_is_kept_until_changed(self):
        self.assertIsNone(hestia.load_config()["screen"], "unanswered until init asks")
        self.write({"screen": False})
        self.write({"why": "the craft itself"})  # skipped this time
        self.write({"screen": "no"})  # not a yes or no, so ignored
        self.assertIs(hestia.load_config()["screen"], False)
        self.write({"screen": True})
        self.assertIs(hestia.load_config()["screen"], True)


class TestConnectorGate(TempHome):
    def gate(self, name, present=False, set_to=None):
        args = hestia.argparse.Namespace(name=name, present=present, set=set_to)
        return hestia.cmd_connector(args)

    def test_asks_once_then_never_again(self):
        hestia.init_write({}, str(self.tmp), ["slack"])
        self.assertEqual(self.gate("slack"), 10, "never asked: ask once")
        self.gate("slack", set_to="declined")
        for _ in range(3):
            self.assertEqual(self.gate("slack"), 1, "declined stays declined")

    def test_allowed_goes_ahead(self):
        hestia.init_write({"connectors": {"slack": "allowed"}}, str(self.tmp), ["slack"])
        self.assertEqual(self.gate("slack"), 0)

    def test_a_connector_added_later_gets_one_question(self):
        hestia.init_write({}, str(self.tmp), [])
        self.assertEqual(self.gate("claude_ai_Gmail"), 1, "unknown and not present: skip silently")
        self.assertEqual(self.gate("claude_ai_Gmail", present=True), 10)
        self.gate("claude_ai_Gmail", set_to="allowed")
        self.assertEqual(self.gate("claude_ai_Gmail"), 0)

    def test_rerunning_init_is_how_a_no_becomes_a_yes(self):
        hestia.init_write({"connectors": {"slack": "declined"}}, str(self.tmp), ["slack"])
        self.assertEqual(self.gate("slack"), 1)
        hestia.init_write({"connectors": {"slack": "allowed"}}, str(self.tmp), ["slack"])
        self.assertEqual(self.gate("slack"), 0)


class TestDashboard(TempHome):
    def render(self):
        out = self.tmp / "dashboard.html"
        args = hestia.argparse.Namespace(out=str(out), no_open=True)
        self.assertEqual(hestia.cmd_dashboard(args), 0)
        return out.read_text()

    def write_prompts(self, times):
        project = self.logs / "-tmp-proj"
        project.mkdir(exist_ok=True)
        with (project / "session.jsonl").open("w") as handle:
            for when in times:
                handle.write(json.dumps({
                    "type": "user", "timestamp": when.isoformat(), "cwd": "/tmp/proj",
                    "message": {"content": "please fix <b>this</b>"},
                }) + "\n")

    def test_empty_day_still_renders(self):
        page = self.render()
        self.assertIn("Working.", page)
        self.assertIn("No prompts recorded today.", page)
        self.assertIn("No list yet.", page)

    def test_shows_load_breaks_and_pause(self):
        now = datetime.now().astimezone()
        base = now.replace(hour=0, minute=5, second=0, microsecond=0)
        self.write_prompts([base, base + timedelta(minutes=5), base + timedelta(minutes=50)])
        hestia.write_json(hestia.pause_path(), {
            "until": (now + timedelta(hours=8)).isoformat(), "note": "<script>x</script>",
        })
        page = self.render()
        self.assertIn("Clocked out until", page)
        self.assertIn("45 min", page, "the gap is marked as a break")
        self.assertIn('class="tick"', page)
        self.assertNotIn("<script>", page, "everything from logs and notes is escaped")
        self.assertIn("&lt;script&gt;", page)

    def test_a_break_reads_as_a_break(self):
        hestia.write_json(hestia.pause_path(), {
            "kind": "break",
            "until": (datetime.now().astimezone() + timedelta(minutes=30)).isoformat(),
        })
        page = self.render()
        self.assertIn("On a break until", page)
        self.assertNotIn("Clocked out", page)

    def test_lists_snapshots_with_the_restore_line(self):
        repo = self.make_repo()
        (repo / "wip.txt").write_text("half done\n")
        hestia.snapshot_repo(repo, "refs/hestia/2026-10-29-180000", "test")
        hestia.write_json(self.home / "config.json", {"repos": [str(repo)]})
        page = self.render()
        self.assertIn("refs/hestia/2026-10-29-180000", page)
        self.assertIn("checkout refs/hestia/2026-10-29-180000 -- .", page)

    def on_stage(self, page):
        self.assertEqual(page.count('class="s on"'), 1, "exactly one stage is lit")
        return re.search(r'<div class="s on">.*?<b>(\w+)</b>', page).group(1)

    def test_the_highlighted_stage_follows_the_pause(self):
        self.assertEqual(self.on_stage(self.render()), "Tend")
        hestia.write_json(hestia.pause_path(), {
            "kind": "break",
            "until": (datetime.now().astimezone() + timedelta(minutes=30)).isoformat(),
        })
        self.assertEqual(self.on_stage(self.render()), "Rest")
        hestia.write_json(hestia.pause_path(), {
            "kind": "done",
            "until": (datetime.now().astimezone() + timedelta(hours=8)).isoformat(),
        })
        page = self.render()
        self.assertEqual(self.on_stage(page), "Keep")
        self.assertIn("/hestia:back", page)
        self.assertNotIn("<script", page, "the page stays static")

    def test_writes_nothing_but_the_page(self):
        self.render()
        self.assertFalse(hestia.pause_path().exists())
        self.assertFalse((self.home / "TOMORROW.md").exists())

    def dashboard(self, **env_changes):
        """Run the real CLI with a stand-in browser that only leaves a marker."""
        browser = self.tmp / "browser.sh"
        browser.write_text('#!/bin/sh\necho "$1" > "%s"\n' % (self.tmp / "opened"))
        browser.chmod(0o755)
        env = dict(os.environ, BROWSER=str(browser))
        for name in ("DISPLAY", "WAYLAND_DISPLAY", "SSH_CONNECTION", "SSH_TTY"):
            env.pop(name, None)
        env.update(env_changes)
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "hestia.py"), "dashboard"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        marker = self.tmp / "opened"
        opened = marker.exists()
        if opened:
            marker.unlink()
        return proc.stdout.decode(), opened

    def test_guesses_the_screen_from_the_display(self):
        for name in ("DISPLAY", "WAYLAND_DISPLAY", "SSH_CONNECTION", "SSH_TTY"):
            os.environ.pop(name, None)
        os.environ["SSH_CONNECTION"] = "10.0.0.1 50000 10.0.0.2 22"
        self.assertFalse(hestia.has_screen(), "a remote login opens nothing you can see")
        os.environ["DISPLAY"] = ":0"
        self.assertTrue(hestia.has_screen(), "a forwarded display can show a page")

    @unittest.skipIf(os.name == "nt", "the stand-in browser is a shell script")
    def test_opens_a_browser_only_where_there_is_a_screen(self):
        out, opened = self.dashboard(SSH_CONNECTION="10.0.0.1 50000 10.0.0.2 22")
        self.assertIn("Dashboard:", out)
        self.assertFalse(opened, "no screen: write the page, open nothing")
        out, opened = self.dashboard(DISPLAY=":0")
        self.assertTrue(opened)
        hestia.write_json(self.home / "config.json", {"screen": True})
        out, opened = self.dashboard(SSH_CONNECTION="10.0.0.1 50000 10.0.0.2 22")
        self.assertTrue(opened, "the answer from init beats the guess")

    @unittest.skipIf(os.name == "nt", "the stand-in browser is a shell script")
    def test_off_when_init_says_there_is_no_screen(self):
        hestia.write_json(self.home / "config.json", {"screen": False})
        out, opened = self.dashboard(DISPLAY=":0")
        self.assertIn("No dashboard on this computer", out)
        self.assertIn("/hestia:today", out)
        self.assertFalse(opened)
        self.assertFalse((self.home / "dashboard.html").exists(), "writes nothing at all")


class TestDream(TempHome):
    """Tidying Claude's auto memory at done: facts, backup, check, restore."""

    def setUp(self):
        super().setUp()
        self.project = self.tmp / "proj"
        self.project.mkdir()
        self.project = self.project.resolve()  # macOS temp paths sit behind /var -> /private/var
        self.memory = self.tmp / "memory-root" / hestia.memory_slug(self.project) / "memory"
        self.memory.mkdir(parents=True)
        self._cwd = os.getcwd()
        os.chdir(str(self.project))

    def tearDown(self):
        os.chdir(self._cwd)
        super().tearDown()

    def note(self, name, description, body="text"):
        (self.memory / (name + ".md")).write_bytes(
            ("---\nname: %s\ndescription: %s\n---\n%s\n" % (name, description, body)).encode())

    def index(self, *names):
        (self.memory / "MEMORY.md").write_bytes(
            "".join("- [%s](%s.md) - x\n" % (n, n) for n in names).encode())

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.memory.iterdir()}

    def test_slug_matches_claude_codes_folder_names(self):
        self.assertEqual(hestia.memory_slug(r"C:\Users\arunn\Downloads\claude_hackathon"),
                         "C--Users-arunn-Downloads-claude-hackathon")
        self.assertEqual(hestia.memory_slug("/home/a/my.repo"), "-home-a-my-repo")

    def test_findings(self):
        self.note("tabs", "indent with tabs")
        self.note("tabs-again", "indent with tabs please")
        self.note("orphan", "not in the index")
        (self.memory / "broken.md").write_bytes(b"no frontmatter\n")
        self.index("tabs", "tabs-again", "gone")
        facts = hestia.memory_findings(self.memory)
        self.assertEqual(facts["index_points_to_missing"], ["gone.md"])
        self.assertEqual(facts["missing_from_index"], ["broken.md", "orphan.md"])
        self.assertEqual(facts["broken"], ["broken.md"])
        self.assertIn(["tabs-again.md", "tabs.md"], [sorted(d) for d in facts["duplicates"]])
        self.assertFalse(facts["over_load_limit"])

    def test_flags_an_index_too_long_to_load(self):
        self.note("a", "b")
        self.index(*["a"] * (hestia.MEMORY_LOAD_LINES + 1))
        self.assertTrue(hestia.memory_findings(self.memory)["over_load_limit"])

    def test_plan_backs_up_and_restore_is_byte_identical(self):
        self.note("tabs", "indent with tabs")
        self.index("tabs")
        before = self.snapshot()
        plan = hestia.dream_plan()
        self.assertTrue(plan["backup"])
        self.assertEqual(len(plan["projects"]), 1)
        (self.memory / "tabs.md").write_bytes(b"changed")
        (self.memory / "MEMORY.md").unlink()
        self.assertEqual(hestia.dream_restore(), [str(self.memory)])
        self.assertEqual(self.snapshot(), before)

    def test_check_counts_a_good_tidy(self):
        self.note("tabs", "indent with tabs")
        self.note("tabs-again", "indent with tabs please")
        self.index("tabs", "tabs-again")
        plan = hestia.dream_plan()
        (self.memory / "tabs-again.md").unlink()
        self.index("tabs")
        result = hestia.dream_check(plan["backup"])[0]
        self.assertEqual((result["removed"], result["rolled_back"]), (["tabs-again.md"], False))
        self.assertTrue(result["index_changed"])

    def test_check_rolls_back_a_broken_or_inventive_tidy(self):
        self.note("tabs", "indent with tabs")
        self.index("tabs")
        before = self.snapshot()
        for damage in ("broken", "invented", "dangling"):
            plan = hestia.dream_plan()
            if damage == "broken":
                (self.memory / "tabs.md").write_bytes(b"lost the frontmatter")
            elif damage == "invented":
                self.note("new-fact", "hestia must never add memories")
            else:
                self.index("tabs", "nowhere")
            result = hestia.dream_check(plan["backup"])[0]
            self.assertTrue(result["rolled_back"], damage)
            self.assertEqual(self.snapshot(), before, damage)

    def test_nothing_to_do_when_switched_off(self):
        self.note("tabs", "indent with tabs")
        os.environ["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        self.assertIn("CLAUDE_CODE_DISABLE_AUTO_MEMORY", hestia.dream_plan()["skipped"])
        del os.environ["CLAUDE_CODE_DISABLE_AUTO_MEMORY"]
        hestia.write_json(self.tmp / "claude" / "settings.json", {"autoMemoryEnabled": False})
        self.assertIn("settings", hestia.dream_plan()["skipped"])
        (self.tmp / "claude" / "settings.json").unlink()
        hestia.init_write({"dream": False}, str(self.tmp), [])
        self.assertIn("/hestia:init", hestia.dream_plan()["skipped"])
        self.assertFalse((self.home / "backups" / "memory").exists(), "nothing backed up when off")

    def test_on_by_default_after_init(self):
        hestia.init_write({}, str(self.tmp), [])
        self.assertTrue(hestia.read_json(self.home / "profile.json")["dream"])

    def test_no_memory_means_nothing_to_tidy(self):
        self.assertEqual(hestia.dream_plan()["skipped"], "no memory to tidy")


class TestEverythingUsesTheLauncher(unittest.TestCase):
    """A hook that calls python3 directly fails silently on Windows, and the pause isn't enforced."""

    LAUNCHER = 'sh "${CLAUDE_PLUGIN_ROOT}/scripts/hestia"'
    PLUGIN = ROOT / "plugins" / "hestia"

    def test_every_hook_goes_through_the_launcher(self):
        hooks = json.loads((self.PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        commands = [h["command"] for groups in hooks.values() for group in groups for h in group["hooks"]]
        self.assertTrue(commands)
        for command in commands:
            self.assertTrue(command.startswith(self.LAUNCHER + " "), command)

    def test_no_command_calls_python_directly(self):
        for md in sorted((self.PLUGIN / "commands").glob("*.md")):
            text = md.read_text(encoding="utf-8")
            self.assertNotIn("scripts/hestia.py", text, md.name)
            self.assertIn(self.LAUNCHER, text, md.name)


class TestClaudeConfigDir(TempHome):
    """People with more than one Claude Code account set CLAUDE_CONFIG_DIR."""

    def setUp(self):
        super().setUp()
        for name in ("HESTIA_LOG_DIR", "HESTIA_CLAUDE_DIR", "HESTIA_CLAUDE_JSON",
                     "HESTIA_MEMORY_ROOT", "CLAUDE_CONFIG_DIR"):
            os.environ.pop(name, None)
        self.config = self.tmp / "claude-work"

    def test_defaults_to_the_home_folder(self):
        self.assertEqual(hestia.log_dir(), Path.home() / ".claude" / "projects")
        self.assertEqual(hestia.claude_dir(), Path.home() / ".claude")
        self.assertEqual(hestia.claude_json_path(), Path.home() / ".claude.json")
        self.assertEqual(hestia.memory_root(), Path.home() / ".claude" / "projects")

    def test_follows_claude_config_dir(self):
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.config)
        self.assertEqual(hestia.log_dir(), self.config / "projects")
        self.assertEqual(hestia.claude_dir(), self.config)
        self.assertEqual(hestia.claude_json_path(), self.config / ".claude.json")
        self.assertEqual(hestia.memory_root(), self.config / "projects")

    def test_reads_prompts_from_that_account_only(self):
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.config)
        project = self.config / "projects" / "-work"
        project.mkdir(parents=True)
        now = datetime.now().astimezone()
        with (project / "s.jsonl").open("w") as handle:
            handle.write(json.dumps({"type": "user", "timestamp": now.isoformat(), "cwd": "/work",
                                     "message": {"content": "the work account"}}) + "\n")
        prompts = hestia.read_prompts(now - timedelta(hours=1))
        self.assertEqual([p["text"] for p in prompts], ["the work account"])

    def test_explicit_overrides_still_win(self):
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.config)
        os.environ["HESTIA_LOG_DIR"] = str(self.logs)
        os.environ["HESTIA_CLAUDE_DIR"] = str(self.tmp / "c")
        os.environ["HESTIA_CLAUDE_JSON"] = str(self.tmp / "c.json")
        os.environ["HESTIA_MEMORY_ROOT"] = str(self.tmp / "m")
        self.assertEqual(hestia.memory_root(), self.tmp / "m")
        self.assertEqual(hestia.log_dir(), self.logs)
        self.assertEqual(hestia.claude_dir(), self.tmp / "c")
        self.assertEqual(hestia.claude_json_path(), self.tmp / "c.json")


if __name__ == "__main__":
    unittest.main()
