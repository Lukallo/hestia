"""Tests for the hestia engine. Nothing mocked: real git repos in temp dirs."""

import json
import os
import shutil
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


if __name__ == "__main__":
    unittest.main()
