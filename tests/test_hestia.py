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
            "python3 hestia.py dashboard --no-open",  # read-only, so allowed too
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

    def test_lists_snapshots_with_the_restore_line(self):
        repo = self.make_repo()
        (repo / "wip.txt").write_text("half done\n")
        hestia.snapshot_repo(repo, "refs/hestia/2026-10-29-180000", "test")
        hestia.write_json(self.home / "config.json", {"repos": [str(repo)]})
        page = self.render()
        self.assertIn("refs/hestia/2026-10-29-180000", page)
        self.assertIn("checkout refs/hestia/2026-10-29-180000 -- .", page)

    def test_writes_nothing_but_the_page(self):
        self.render()
        self.assertFalse(hestia.pause_path().exists())
        self.assertFalse((self.home / "TOMORROW.md").exists())


if __name__ == "__main__":
    unittest.main()
