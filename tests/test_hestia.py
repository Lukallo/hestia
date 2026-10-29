"""Tests for the hestia engine. Nothing mocked: real git repos in temp dirs."""

import json
import os
import subprocess
import sys
import tempfile
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
        os.environ["HESTIA_CLAUDE_DIR"] = str(self.tmp / "claude")
        os.environ["HESTIA_CLAUDE_JSON"] = str(self.tmp / "claude.json")

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
        ]:
            self.assertEqual(
                self.hook({"tool_name": "Bash", "tool_input": {"command": command}}),
                "allow",
                "pause must not block: %s" % command,
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
    def session_start(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "hestia.py"), "session-start"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ),
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


if __name__ == "__main__":
    unittest.main()
