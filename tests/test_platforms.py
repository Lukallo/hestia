"""What differs between Linux, macOS and Windows, tested on whichever this is.

CI runs this on all three. Where it can, each test also recreates the Windows
hazard it guards against, so a regression shows up on any machine.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_hestia import ROOT, SCRIPTS, TempHome, hestia  # noqa: E402

SCRIPT = SCRIPTS / "hestia.py"
PLUGIN = ROOT / "plugins" / "hestia"
WINDOWS = os.name == "nt"

# No single legacy code page holds all of this. "Á" is UTF-8 C3 81, and 0x81
# means nothing in Windows-1252, the code page Windows gives Python's pipes.
ANY_TEXT = "Łukasz’s café ☕ 修复登录 Á"


def windows_like(env):
    """Python's text defaults as Windows sets them, on any OS.

    Pipes use Windows-1252, as they do on Windows. On Python 3.10 and later,
    opening a text file without naming its encoding is an error, because on
    Windows that encoding is the ANSI code page rather than UTF-8.
    """
    env = dict(env, PYTHONIOENCODING="cp1252")
    if sys.version_info >= (3, 10):
        env.update(PYTHONWARNDEFAULTENCODING="1", PYTHONWARNINGS="error::EncodingWarning")
    return env


def git_bash():
    """Git for Windows' bash, found the way Claude Code finds it: next to git."""
    git = shutil.which("git")
    if not WINDOWS or not git:
        return None
    for folder in Path(git).resolve().parents:
        candidate = folder / "bin" / "bash.exe"
        if candidate.exists():
            return str(candidate)
    return None


def powershells():
    """PowerShell 7, then Windows PowerShell 5.1, wherever they exist."""
    names = ["pwsh", "powershell"] if WINDOWS else ["pwsh"]
    return [(name, [shutil.which(name), "-NoProfile", "-NonInteractive", "-Command"])
            for name in names if shutil.which(name)]


def pasting_shells():
    """Every shell a person or Claude might paste a restore line into."""
    if WINDOWS:
        found = [("Git Bash", [git_bash(), "-c"])] if git_bash() else []
    else:
        found = [("sh", ["/bin/sh", "-c"])]
        found += [(name, [shutil.which(name), "-c"]) for name in ("bash", "zsh") if shutil.which(name)]
    return found + powershells()


def hook_shells():
    """The shells Claude Code runs hook commands in, where Hestia's launcher works.

    `sh -c` on macOS and Linux, and Git Bash on Windows. Without Git Bash,
    Claude Code uses PowerShell, which has no `sh` for the launcher.
    """
    if not WINDOWS:
        return [("sh", ["/bin/sh", "-c"])]
    return [("Git Bash", [git_bash(), "-c"])] if git_bash() else []


class TestAnyText(TempHome):
    """Notes, prompts, names and paths in any language survive a whole day."""

    def setUp(self):
        super().setUp()
        for key in ("AUTHOR", "COMMITTER"):
            os.environ["GIT_%s_NAME" % key] = "test"
            os.environ["GIT_%s_EMAIL" % key] = "test@hestia.local"

    def hestia(self, *args, **kwargs):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT)] + list(args),
            input=kwargs.get("stdin", b""), cwd=str(kwargs.get("cwd", self.tmp)),
            env=windows_like(os.environ), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        error = proc.stderr.decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, error)
        self.assertNotIn("Traceback", error)
        return proc.stdout.decode("utf-8")  # strict: it must be real UTF-8

    def test_a_whole_day_in_any_language(self):
        repo = self.make_repo()
        (repo / "wip.txt").write_text("half done\n")
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", ANY_TEXT], cwd=str(repo))
        project = self.logs / "project"
        project.mkdir()
        entry = {"type": "user", "timestamp": datetime.now().astimezone().isoformat(),
                 "cwd": str(repo), "message": {"content": ANY_TEXT}}
        (project / "session.jsonl").write_bytes((json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))

        self.assertIn("Clocked out", self.hestia("done", "--note", ANY_TEXT, "--keep-agents", cwd=repo))
        written = (self.home / "TOMORROW.md").read_bytes().decode("utf-8")
        self.assertIn("> " + ANY_TEXT, written)
        self.assertIn("- " + ANY_TEXT, written, "the prompt is in the list too")

        self.hestia("today")
        why = self.hestia("why", cwd=repo)
        self.assertIn(ANY_TEXT, why.splitlines()[2], "the commit subject")
        self.assertIn("last shutdown note: " + ANY_TEXT, why)

        # Morning: the pause runs out, and the first session gets the list.
        pause = hestia.read_json(hestia.pause_path())
        pause["until"] = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat()
        hestia.write_json(hestia.pause_path(), pause)
        handed = json.loads(self.hestia("session-start"))
        self.assertIn("> " + ANY_TEXT, handed["hookSpecificOutput"]["additionalContext"])
        self.hestia("back")

    def test_the_hook_reads_what_claude_code_sends(self):
        # Claude Code sends raw UTF-8, not \u escapes.
        def hook(command):
            payload = {"tool_name": "Bash", "tool_input": {"command": command}}
            out = self.hestia("hook", stdin=json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out else "allow"

        self.assertEqual(hook("echo " + ANY_TEXT), "allow")
        hestia.write_json(hestia.pause_path(), {"until": (datetime.now().astimezone() + timedelta(hours=8)).isoformat()})
        self.assertEqual(hook("echo " + ANY_TEXT), "deny")
        self.assertEqual(hook('python3 "C:/Users/Łukasz Nowak/.claude/plugins/hestia/scripts/hestia.py" back'), "allow")

    def test_init_keeps_any_name_and_any_claude_md(self):
        md = self.tmp / "claude" / "CLAUDE.md"
        md.parent.mkdir()
        original = ("# Notatki, %s\r\n" % ANY_TEXT).encode("utf-8")
        md.write_bytes(original)
        answers = json.dumps({"name": "Łukasz", "helps": ANY_TEXT}, ensure_ascii=False).encode("utf-8")
        self.hestia("init", "write", "--answers", "-", stdin=answers)
        text = md.read_bytes()
        self.assertTrue(text.startswith(original))
        self.assertIn(("Right now my work helps: %s." % ANY_TEXT).encode("utf-8"), text)
        self.hestia("init", "remove")
        self.assertEqual(md.read_bytes(), original)

    def test_paths_with_spaces_and_accents(self):
        os.environ["HESTIA_HOME"] = str(self.tmp / "Łukasz Nowak" / ".hestia")
        repo = self.make_repo("mój projekt")
        (repo / "wip.txt").write_text("half done\n")
        out = self.hestia("done", "--keep-agents", cwd=repo)
        self.assertIn("in mój projekt -> refs/hestia/", out)
        self.assertIn(str(self.tmp / "Łukasz Nowak" / ".hestia" / "TOMORROW.md"), out)


class TestRestoreLine(TempHome):
    """The restore line gets pasted into whatever shell the person or Claude has."""

    def run_hestia(self, repo, *args):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT)] + list(args), cwd=str(repo), env=dict(os.environ),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        return proc.stdout.decode("utf-8")

    def test_brings_the_work_back_in_every_shell(self):
        repo = self.make_repo("my repo")
        (repo / "a.txt").write_text("one, edited\n")
        (repo / "wip.txt").write_text("half done\n")

        # --keep-agents and no --stop-agents: never signal a real process.
        self.run_hestia(repo, "done", "--keep-agents")
        tomorrow = (self.home / "TOMORROW.md").read_text(encoding="utf-8")
        lines = [re.search(r"`(git -C .+ -- \.)`", tomorrow).group(1)]
        self.run_hestia(repo, "back")
        self.run_hestia(repo, "break", "30m")
        back = self.run_hestia(repo, "back")
        lines.append(re.search(r"Before the break: (.+)", back).group(1).strip())
        top = hestia.git_toplevel(repo)  # macOS adds /private, Windows uses long names
        self.assertIn(shlex.quote(top.as_posix()), lines[0], "quoted, with forward slashes")

        shells = pasting_shells()
        self.assertTrue(shells, "no shell found to paste into")
        for line in lines:
            for name, argv in shells:
                with self.subTest(shell=name, line=line):
                    hestia.git(repo, ["checkout", "--", "."])
                    (repo / "wip.txt").unlink()
                    proc = subprocess.run(argv + [line], cwd=str(self.tmp),
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
                    self.assertEqual((repo / "a.txt").read_text(), "one, edited\n")
                    self.assertEqual((repo / "wip.txt").read_text(), "half done\n")


class TestHooksAsInstalled(TempHome):
    """The commands in hooks.json, run the way Claude Code runs them here."""

    def setUp(self):
        super().setUp()
        # Installed under a folder with a space, like Program Files or "Ann Lee".
        self.root = self.tmp / "Plugin Cache" / "hestia"
        shutil.copytree(str(PLUGIN), str(self.root))
        hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        # Keyed like "SessionStart:startup", one per command in hooks.json.
        self.commands = {
            ":".join(filter(None, [event, entry.get("matcher")])): hook["command"]
            for event, entries in hooks.items() for entry in entries for hook in entry["hooks"]
        }

    def run_hook(self, shell, argv, name, payload=None):
        # Claude Code substitutes the root itself, with forward slashes in
        # Git Bash, and exports it too.
        root = self.root.as_posix()
        command = self.commands[name].replace("${CLAUDE_PLUGIN_ROOT}", root)
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=root)
        proc = subprocess.run(
            argv + [command], input=json.dumps(payload or {}).encode("utf-8"), cwd=str(self.tmp),
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, "%s: %s" % (shell, proc.stderr.decode("utf-8", "replace")))
        return proc.stdout.decode("utf-8").strip()

    def pause(self):
        hestia.write_json(hestia.pause_path(), {"until": (datetime.now().astimezone() + timedelta(hours=8)).isoformat()})

    def test_found_a_shell_to_run_hooks_in(self):
        self.assertTrue(hook_shells(), "Hestia's hooks need Git Bash on Windows")

    def test_silent_on_a_normal_day(self):
        for shell, argv in hook_shells():
            with self.subTest(shell=shell):
                for name in self.commands:
                    self.assertEqual(self.run_hook(shell, argv, name, {"tool_name": "Bash"}), "", name)

    def test_the_pause_holds_and_the_list_arrives_once(self):
        for shell, argv in hook_shells():
            with self.subTest(shell=shell):
                self.pause()
                out = self.run_hook(shell, argv, "PreToolUse:*", {"tool_name": "Edit", "tool_input": {}})
                self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
                back = {"tool_name": "Bash", "tool_input": {"command": 'sh "%s/scripts/hestia" back' % self.root.as_posix()}}
                self.assertEqual(self.run_hook(shell, argv, "PreToolUse:*", back), "", "stdin reached the hook")
                hestia.clear_pause()
                (self.home / "TOMORROW.md").write_text("# Tomorrow\n\nstart with the %s parser\n" % shell)
                out = self.run_hook(shell, argv, "SessionStart:startup")
                self.assertIn("start with the %s parser" % shell, json.loads(out)["hookSpecificOutput"]["additionalContext"])
                self.assertEqual(self.run_hook(shell, argv, "SessionStart:startup"), "", "only once")


class TestWindowsSpellings(TempHome):
    """Windows paths and shells spell the same commands differently."""

    def test_the_way_back_in_is_never_blocked(self):
        hestia.write_json(hestia.pause_path(), {"until": (datetime.now().astimezone() + timedelta(hours=8)).isoformat()})
        allowed = [
            'python "C:\\Users\\Ann Lee\\.claude\\plugins\\cache\\hestia\\scripts\\hestia.py" back',
            "python C:/Users/ann/.claude/plugins/cache/hestia/scripts/hestia.py today",
            "py -3 C:\\hestia\\scripts\\hestia.py status",
            "& python 'C:\\Program Files\\hestia\\scripts\\hestia.py' resume",
            "python3.exe hestia.py dashboard --no-open",
        ]
        for command in allowed + ["python C:\\hestia\\scripts\\hestia.py backup"]:
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "hook"],
                input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}).encode("utf-8"),
                env=dict(os.environ), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            decision = "deny" if proc.stdout.strip() else "allow"
            self.assertEqual(decision, "allow" if command in allowed else "deny", command)

    def test_project_keys_in_claude_json_match_this_folder(self):
        # Claude Code keys projects as C:/Users/..., with forward slashes.
        project = self.tmp / "proj"
        project.mkdir()
        key = project.as_posix()
        if WINDOWS:
            key = key[0].swapcase() + key[1:]  # and drive letters vary in case
        hestia.write_json(self.tmp / "claude.json", {"projects": {key: {"mcpServers": {"github": {}}}}})
        self.assertEqual(hestia.detect_connectors(str(project))["github"]["source"], "local")


if __name__ == "__main__":
    import unittest

    unittest.main()
