#!/usr/bin/env python3
"""hestia - end the working day in Claude Code.

Deterministic engine and standalone CLI. Standard library only, Python 3.8+.

    hestia.py done  [--note TEXT] [--until WHEN] [--json]   (alias: shutdown)
    hestia.py break [45m | 1h30m | 13:50] [--json]          midday pause, lifts itself
    hestia.py back                                          (alias: resume)
    hestia.py today [--json]                                (alias: status)
    hestia.py why   [--days N] [--repo PATH] [--json]       facts for "what was this for"
    hestia.py new   [--since VERSION] [--json]              what changed in Claude Code
    hestia.py init  [detect|write|remove] [--seen NAMES] [--answers JSON]
    hestia.py connector NAME [--present] [--set allowed|declined]
    hestia.py dashboard [--out PATH] [--no-open]            # today, as one static page; off with no screen
    hestia.py hook            # PreToolUse hook, reads JSON on stdin
    hestia.py session-start   # SessionStart hook, hands over yesterday's list
    hestia.py prompt-submit   # UserPromptSubmit hook, the same handover for a window left open
    hestia.py handover [--again]  # show whether the morning list is still due, or re-arm it
    hestia.py dream plan|check|restore [--backup ID] [--json]  # tidy Claude's memory at done
    hestia.py selftest        # proves the whole flow on a throwaway repo

Environment overrides (used by the tests and selftest):
    HESTIA_HOME           state directory, default ~/.hestia
    HESTIA_LOG_DIR        Claude Code project logs, default <claude dir>/projects
    HESTIA_CLAUDE_DIR     where the user's CLAUDE.md lives, default <claude dir>
    HESTIA_CLAUDE_JSON    Claude Code's config, default ~/.claude.json
                          (<claude dir>/.claude.json when CLAUDE_CONFIG_DIR is set)

<claude dir> is $CLAUDE_CONFIG_DIR when Claude Code was started with it, else ~/.claude.
    HESTIA_CHANGELOG_URL  where `new` reads the changelog (file:// works)
    HESTIA_MEMORY_ROOT    Claude Code's auto-memory projects, default <claude dir>/projects
"""

import argparse
import shlex
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

BREAK_MINUTES = 20
DEFAULT_RESUME_HOUR = 8
DEFAULT_WORKING_DAYS = [0, 1, 2, 3, 4]  # Monday-Friday
DEFAULT_BREAK_MINUTES = 45
# A break longer than this is the end of the day, which is what `done` is for.
MAX_BREAK_MINUTES = 4 * 60

CHANGELOG_URL = "https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md"
# `new` hands Claude a bounded slice of the changelog, never the whole history.
NEW_MAX_RELEASES = 10
NEW_MAX_CHANGES = 150

# The tools Claude Code runs shell commands with. PowerShell is the one it uses
# on Windows when Git Bash isn't installed.
SHELL_TOOLS = ("Bash", "PowerShell")

# A shell command matching this is always allowed while paused, otherwise the
# pause would block the only command that lifts it. It has to be the whole
# command: one hestia call, with an optional interpreter in front (python,
# or sh for the launcher the commands use).
RESUME_PATTERN = re.compile(
    r"""^
    (?:(?:"[^"]*(?:python[\w.]*|(?:ba)?sh(?:\.exe)?)"|(?:[^\s"']*[/\\])?(?:python[\w.]*|(?:ba)?sh(?:\.exe)?)|py(?:\s+-3(?:\.\d+)?)?)\s+)?
    (?:"(?:[^"]*[/\\])?hestia(?:\.py)?"|'(?:[^']*[/\\])?hestia(?:\.py)?'|(?:[^\s"']*[/\\])?hestia(?:\.py)?)
    \s+(back|resume|today|status|dashboard|hook|session-start|prompt-submit|handover|selftest|dream\s+restore)\b
    """,
    re.X | re.I,
)

# Anything that could run a second command never counts as the way back in:
# chaining, pipes, redirects, subshells and new lines.
SHELL_JOINERS = re.compile(r"[;&|`\n\r<>]|\$\(")


# --------------------------------------------------------------------------
# paths and config
# --------------------------------------------------------------------------

def hestia_home():
    return Path(os.environ.get("HESTIA_HOME") or (Path.home() / ".hestia"))


def claude_config_dir():
    """Where Claude Code keeps its own files: CLAUDE_CONFIG_DIR, or ~/.claude.

    People who run more than one Claude Code account set CLAUDE_CONFIG_DIR.
    Claude Code then keeps the session logs, CLAUDE.md and .claude.json in
    that folder, and passes the variable on to hooks.
    """
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else None


def log_dir():
    if os.environ.get("HESTIA_LOG_DIR"):
        return Path(os.environ["HESTIA_LOG_DIR"])
    return (claude_config_dir() or Path.home() / ".claude") / "projects"


def load_config():
    cfg = {
        "resume_hour": DEFAULT_RESUME_HOUR,
        "working_days": list(DEFAULT_WORKING_DAYS),
        "repos": [],
        "screen": None,  # set by init; None means guess with has_screen()
    }
    path = hestia_home() / "config.json"
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            pass
    return cfg


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# time
# --------------------------------------------------------------------------

def parse_ts(value):
    """Parse a log timestamp (ISO 8601, usually ending in Z) into local time."""
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def next_resume(now, cfg):
    """The next working morning at the configured hour."""
    hour = int(cfg.get("resume_hour", DEFAULT_RESUME_HOUR))
    days = cfg.get("working_days") or DEFAULT_WORKING_DAYS
    candidate = (now + timedelta(days=1)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    for _ in range(8):
        if candidate.weekday() in days:
            return candidate
        candidate += timedelta(days=1)
    return candidate


def parse_until(value, now, cfg):
    if not value:
        return next_resume(now, cfg)
    text = value.strip()
    match = re.match(r"^(\d{1,2}):(\d{2})$", text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise SystemExit("could not read --until %r (use HH:MM or an ISO timestamp)" % value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=now.tzinfo)
    return dt.astimezone()


def human(dt):
    return dt.strftime("%a %d %b %H:%M")


def parse_break(value, now):
    """How long a break lasts: `45m`, `1h`, `1h30m`, `90` (minutes) or `13:50`.

    Returns the moment the break ends. Raises SystemExit with a plain reason
    when the value can't be read, is not in the future, or is long enough to
    be the end of the day instead.
    """
    text = (value or "").strip().lower().replace(" ", "")
    if not text:
        until = now + timedelta(minutes=DEFAULT_BREAK_MINUTES)
    else:
        clock = re.match(r"^(\d{1,2}):(\d{2})$", text)
        span = re.match(r"^(?:(\d+)h)?(?:(\d+)(?:m|min|mins)?)?$", text)
        if clock:
            hour, minute = int(clock.group(1)), int(clock.group(2))
            if hour > 23 or minute > 59:
                raise SystemExit("could not read the break length %r" % value)
            until = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if until <= now:
                raise SystemExit("%s has already passed today" % until.strftime("%H:%M"))
        elif span and (span.group(1) or span.group(2)):
            minutes = int(span.group(1) or 0) * 60 + int(span.group(2) or 0)
            until = now + timedelta(minutes=minutes)
        else:
            raise SystemExit(
                "could not read the break length %r (use 45m, 1h30m, 90 or 13:50)" % value
            )
    minutes = (until - now).total_seconds() / 60.0
    if minutes < 1:
        raise SystemExit("a break needs to last at least a minute")
    if minutes > MAX_BREAK_MINUTES:
        raise SystemExit(
            "that's longer than %d hours, so it's the end of the day: use done instead"
            % (MAX_BREAK_MINUTES // 60)
        )
    return until


# --------------------------------------------------------------------------
# pause state
# --------------------------------------------------------------------------

def pause_path():
    return hestia_home() / "pause.json"


def read_pause(now=None):
    """Return the active pause, or None. Expired pauses are cleared on read."""
    now = now or datetime.now().astimezone()
    data = read_json(pause_path())
    if not isinstance(data, dict):
        return None
    until = parse_ts(data.get("until"))
    if until is None:
        return None
    if now >= until:
        clear_pause()
        return None
    data["_until"] = until
    # Pauses written before breaks existed have no kind; they were all `done`.
    data["kind"] = data.get("kind") or "done"
    return data


def pause_line(pause):
    """The one sentence that says what kind of pause this is and when it ends."""
    if pause.get("kind") == "break":
        return "On a break until %s." % pause["_until"].strftime("%H:%M")
    return "Clocked out until %s." % human(pause["_until"])


def clear_pause():
    try:
        pause_path().unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------

class GitResult(object):
    def __init__(self, ok, out, err):
        self.ok = ok
        self.out = out
        self.err = err


def git(repo, args, env=None):
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    proc = subprocess.run(
        ["git"] + args,
        cwd=str(repo),
        env=full_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return GitResult(
        proc.returncode == 0,
        proc.stdout.decode("utf-8", "replace").strip(),
        proc.stderr.decode("utf-8", "replace").strip(),
    )


def git_toplevel(path):
    result = git(path, ["rev-parse", "--show-toplevel"])
    return Path(result.out) if result.ok and result.out else None


def index_fingerprint(repo):
    """Hash of .git/index, so we can prove the snapshot did not touch it."""
    common = git(repo, ["rev-parse", "--git-dir"])
    if not common.ok:
        return None
    git_dir = Path(common.out)
    if not git_dir.is_absolute():
        git_dir = Path(repo) / git_dir
    index = git_dir / "index"
    if not index.exists():
        return "no-index"
    return hashlib.sha256(index.read_bytes()).hexdigest()


def snapshot_repo(repo, ref, message):
    """Commit the working tree to a hestia ref without touching the repo.

    Uses a temporary index, so .git/index, the working tree and the current
    branch are all left exactly as they were. Returns a dict of facts.
    """
    repo = Path(repo)
    before_head = git(repo, ["rev-parse", "HEAD"])
    before_status = git(repo, ["status", "--porcelain"]).out
    before_index = index_fingerprint(repo)
    branch = git(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).out or "(detached)"

    dirty = [line for line in before_status.splitlines() if line.strip()]
    info = {
        "repo": str(repo),
        "branch": branch,
        "dirty_files": len(dirty),
        "ref": None,
        "commit": None,
        "clean": not dirty,
        "untouched": True,
        "error": None,
    }

    if not dirty:
        return info

    handle, index_file = tempfile.mkstemp(prefix="hestia-index-")
    os.close(handle)
    os.unlink(index_file)  # git wants to create it itself
    env = {"GIT_INDEX_FILE": index_file}
    try:
        if before_head.ok:
            read = git(repo, ["read-tree", before_head.out], env=env)
            if not read.ok:
                info["error"] = read.err
                return info
        added = git(repo, ["add", "-A"], env=env)
        if not added.ok:
            info["error"] = added.err
            return info
        tree = git(repo, ["write-tree"], env=env)
        if not tree.ok:
            info["error"] = tree.err
            return info
        args = ["commit-tree", tree.out, "-m", message]
        if before_head.ok:
            args[2:2] = ["-p", before_head.out]
        commit = git(repo, args)
        if not commit.ok:
            info["error"] = commit.err
            return info
        updated = git(repo, ["update-ref", ref, commit.out])
        if not updated.ok:
            info["error"] = updated.err
            return info
        info["ref"] = ref
        info["commit"] = commit.out
    finally:
        try:
            os.unlink(index_file)
        except OSError:
            pass

    after_head = git(repo, ["rev-parse", "HEAD"]).out
    after_status = git(repo, ["status", "--porcelain"]).out
    after_index = index_fingerprint(repo)
    info["untouched"] = (
        after_head == before_head.out
        and after_status == before_status
        and after_index == before_index
    )
    return info


def restore_line(repo, ref):
    """The one command that brings saved work back.

    Forward slashes and POSIX quoting keep it pasteable into bash, zsh, Git
    Bash and PowerShell, including Windows paths and folders with spaces.
    """
    import shlex

    return "git -C %s checkout %s -- ." % (shlex.quote(Path(repo).as_posix()), ref)


# --------------------------------------------------------------------------
# running agents
# --------------------------------------------------------------------------

# A headless run has no one watching it, so it is the kind that quietly keeps
# going all night. An interactive session has a person at it.
HEADLESS_MARKERS = ("--print", "--output-format", "-p ")


def _ps_table():
    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,command="],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError:
        return []  # no `ps` (Windows): no agents found, so none are signalled
    rows = []
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
        except ValueError:
            continue
    return rows


def _own_pids(rows):
    """This process and everything that spawned it - never our own feet."""
    parents = {pid: ppid for pid, ppid, _ in rows}
    pids = set()
    current = os.getpid()
    for _ in range(16):
        if current in pids or current <= 1:
            break
        pids.add(current)
        current = parents.get(current, 0)
    return pids


def _is_claude(command):
    parts = command.split()
    if not parts:
        return False
    if os.path.basename(parts[0]) in ("claude", "claude-code"):
        return True
    # node /path/to/claude ...
    if os.path.basename(parts[0]) in ("node", "bun") and len(parts) > 1:
        return os.path.basename(parts[1]) in ("claude", "claude-code", "cli.js")
    return False


def running_agents():
    """Claude Code processes other than our own, split by whether anyone's watching."""
    rows = _ps_table()
    mine = _own_pids(rows)
    found = []
    for pid, _ppid, command in rows:
        if pid in mine or not _is_claude(command):
            continue
        headless = any(marker in command + " " for marker in HEADLESS_MARKERS)
        found.append({"pid": pid, "command": command[:120], "headless": headless})
    return found


def stop_agents(stop_all=False):
    """SIGTERM unattended agents. Interactive sessions are reported, not killed."""
    import signal

    stopped, left = [], []
    for agent in running_agents():
        if agent["headless"] or stop_all:
            try:
                os.kill(agent["pid"], signal.SIGTERM)
                agent["signal"] = "SIGTERM"
                stopped.append(agent)
            except OSError as error:
                agent["error"] = str(error)
                left.append(agent)
        else:
            left.append(agent)
    return stopped, left


# --------------------------------------------------------------------------
# Claude Code session logs
# --------------------------------------------------------------------------

def _plain_text(content):
    """Return the text of a real typed prompt, or None."""
    if not isinstance(content, str):
        return None  # list content is tool results, not something a person typed
    text = content.strip()
    if not text:
        return None
    if text.startswith("<"):
        return None  # <command-name>, <local-command-stdout>, system wrappers
    return text


def read_prompts(since):
    """Every prompt a person typed since `since`, oldest first."""
    prompts = []
    root = log_dir()
    if not root.exists():
        return prompts
    for path in root.glob("*/*.jsonl"):
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line or '"user"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("type") != "user" or entry.get("isSidechain"):
                    continue
                when = parse_ts(entry.get("timestamp"))
                if when is None or when < since:
                    continue
                message = entry.get("message") or {}
                text = _plain_text(message.get("content"))
                if text is None:
                    continue
                prompts.append(
                    {
                        "at": when,
                        "cwd": entry.get("cwd") or "",
                        "branch": entry.get("gitBranch") or "",
                        "text": text,
                        "session": entry.get("sessionId") or path.stem,
                    }
                )
    prompts.sort(key=lambda item: item["at"])
    return prompts


def day_load(prompts):
    """Workload facts for a list of prompts. Shown only when asked for."""
    load = {
        "prompts": len(prompts),
        "projects": sorted({p["cwd"] for p in prompts if p["cwd"]}),
        "first": None,
        "last": None,
        "span_hours": 0.0,
        "last_break_at": None,
        "last_break_minutes": 0,
        "breaks": 0,
    }
    if not prompts:
        return load
    load["first"] = prompts[0]["at"]
    load["last"] = prompts[-1]["at"]
    load["span_hours"] = round(
        (prompts[-1]["at"] - prompts[0]["at"]).total_seconds() / 3600.0, 1
    )
    for earlier, later in zip(prompts, prompts[1:]):
        minutes = (later["at"] - earlier["at"]).total_seconds() / 60.0
        if minutes >= BREAK_MINUTES:
            load["breaks"] += 1
            load["last_break_at"] = earlier["at"]
            load["last_break_minutes"] = int(minutes)
    return load


def discover_repos(prompts, cfg, extra=None):
    """Repos touched today: from the logs, the current folder, and config."""
    candidates = []
    for prompt in prompts:
        if prompt["cwd"]:
            candidates.append(prompt["cwd"])
    candidates.append(os.getcwd())
    candidates.extend(cfg.get("repos") or [])
    candidates.extend(extra or [])

    repos = []
    seen = set()
    for candidate in candidates:
        path = Path(candidate)
        if not path.is_dir():
            continue
        top = git_toplevel(path)
        if top is None:
            continue
        key = str(top.resolve())
        if key in seen:
            continue
        seen.add(key)
        repos.append(top)
    return repos


# --------------------------------------------------------------------------
# tomorrow's list
# --------------------------------------------------------------------------

def _one_line(text, limit):
    single = " ".join(str(text).split())
    return single if len(single) <= limit else single[: limit - 3] + "..."


def sessions_to_resume(prompts, limit=3):
    """The most recent sessions today, one per session, newest first.

    Each comes with the exact command that reopens it, so picking up the
    thread is one paste, not a search through history.
    """
    latest = {}
    for prompt in prompts:
        if prompt.get("session"):
            latest[prompt["session"]] = prompt
    ordered = sorted(latest.values(), key=lambda p: p["at"], reverse=True)
    return ordered[:limit]


def write_tomorrow(now, note, snapshots, load, prompts, stopped=None, still_open=None, next_step=""):
    stopped = stopped or []
    still_open = still_open or []
    lines = ["# Tomorrow - %s" % now.strftime("%a %d %b"), ""]
    if note:
        lines += ["> %s" % note.strip(), ""]

    # The first thing to do, before anything else, because that is what the
    # morning actually needs. Written by Claude at shutdown from the session.
    if next_step and next_step.strip():
        lines += ["## Start here", "", "- %s" % _one_line(next_step, 300), ""]

    lines += ["## Where you left off", ""]
    saved = [s for s in snapshots if s["ref"]]
    clean = [s for s in snapshots if s["clean"]]
    if saved:
        for snap in saved:
            lines.append(
                "- **%s** (%s) - %d file%s saved"
                % (
                    Path(snap["repo"]).name,
                    snap["branch"],
                    snap["dirty_files"],
                    "" if snap["dirty_files"] == 1 else "s",
                )
            )
            lines.append("  `%s`" % restore_line(snap["repo"], snap["ref"]))
            last = git(snap["repo"], ["log", "-1", "--format=%s"])
            if last.ok and last.out:
                lines.append("  last commit: %s" % _one_line(last.out, 120))
    for snap in clean:
        lines.append(
            "- **%s** (%s) - nothing uncommitted"
            % (Path(snap["repo"]).name, snap["branch"])
        )
    if not snapshots:
        lines.append("- no repos touched today")
    lines.append("")

    lines += ["## Today", ""]
    if load["prompts"]:
        lines.append(
            "- %d prompts across %d project%s"
            % (
                load["prompts"],
                len(load["projects"]),
                "" if len(load["projects"]) == 1 else "s",
            )
        )
        lines.append(
            "- first at %s, last at %s (%s hours)"
            % (
                load["first"].strftime("%H:%M"),
                load["last"].strftime("%H:%M"),
                load["span_hours"],
            )
        )
        if load["last_break_at"]:
            lines.append(
                "- last real break: %d min at %s"
                % (load["last_break_minutes"], load["last_break_at"].strftime("%H:%M"))
            )
        else:
            lines.append("- no break longer than %d minutes" % BREAK_MINUTES)
    else:
        lines.append("- no prompts recorded today")
    lines.append("")

    lines += ["## Overnight", ""]
    if stopped:
        lines.append(
            "- stopped %d unattended agent%s at %s"
            % (len(stopped), "" if len(stopped) == 1 else "s", now.strftime("%H:%M"))
        )
    if still_open:
        lines.append(
            "- %d Claude session%s left open, paused until morning"
            % (len(still_open), "" if len(still_open) == 1 else "s")
        )
    if not stopped and not still_open:
        lines.append("- nothing was running")
    lines.append("")

    sessions = sessions_to_resume(prompts)
    if sessions:
        lines += ["## Sessions to pick up", ""]
        for prompt in sessions:
            where = prompt["cwd"] or "."
            lines.append(
                "- **%s** at %s: %s"
                % (Path(where).name or where, prompt["at"].strftime("%H:%M"), _one_line(prompt["text"], 120))
            )
            lines.append("  `cd %s && claude --resume %s`" % (shlex.quote(where), prompt["session"]))
        lines.append("")

    recent = [p["text"] for p in prompts[-5:]]
    if recent:
        lines += ["## The last things you asked for", ""]
        for text in recent:
            lines.append("- %s" % _one_line(text, 160))
        lines.append("")

    path = hestia_home() / "TOMORROW.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_shutdown(args):
    now = datetime.now().astimezone()
    cfg = load_config()
    until = parse_until(args.until, now, cfg)

    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    prompts = read_prompts(start_of_day)
    load = day_load(prompts)
    repos = discover_repos(prompts, cfg)

    stamp = now.strftime("%Y-%m-%d-%H%M%S")
    message = "hestia shutdown %s" % now.strftime("%Y-%m-%d %H:%M:%S")
    snapshots = [
        snapshot_repo(repo, "refs/hestia/%s" % stamp, message) for repo in repos
    ]

    if args.keep_agents:
        stopped, still_open = [], running_agents()
    else:
        stopped, still_open = stop_agents(stop_all=args.stop_all)

    tomorrow = write_tomorrow(
        now, args.note, snapshots, load, prompts, stopped, still_open,
        next_step=getattr(args, "next", "") or "",
    )

    write_json(
        pause_path(),
        {
            "kind": "done",
            "until": until.isoformat(),
            "since": now.isoformat(),
            "note": args.note or "",
            "tomorrow": str(tomorrow),
        },
    )
    arm_handover(tomorrow, since=now)

    result = {
        "paused_until": until.isoformat(),
        "paused_until_human": human(until),
        "tomorrow": str(tomorrow),
        "snapshots": [
            {k: v for k, v in snap.items() if k != "error"} for snap in snapshots
        ],
        "saved": len([s for s in snapshots if s["ref"]]),
        "load": {
            "prompts": load["prompts"],
            "projects": len(load["projects"]),
            "span_hours": load["span_hours"],
            "last_break_minutes": load["last_break_minutes"],
        },
        "agents_stopped": stopped,
        "agents_still_open": still_open,
        "errors": [s["error"] for s in snapshots if s["error"]],
        "integrity_ok": all(s["untouched"] for s in snapshots),
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print("Clocked out until %s." % human(until))
    saved = [s for s in snapshots if s["ref"]]
    for snap in saved:
        print(
            "  saved %d file%s in %s -> %s"
            % (
                snap["dirty_files"],
                "" if snap["dirty_files"] == 1 else "s",
                Path(snap["repo"]).name,
                snap["ref"],
            )
        )
    if not saved:
        print("  nothing uncommitted to save")
    if stopped:
        print("  stopped %d unattended agent%s" % (len(stopped), "" if len(stopped) == 1 else "s"))
    if still_open:
        print(
            "  %d Claude session%s still open - paused, they'll stop at their next step"
            % (len(still_open), "" if len(still_open) == 1 else "s")
        )
    print("  tomorrow's list: %s" % tomorrow)
    if result["errors"]:
        for error in result["errors"]:
            print("  warning: %s" % error, file=sys.stderr)
    return 0


def cmd_resume(args):
    pause = read_pause()
    clear_pause()
    since = (parse_ts(pause.get("since")) if pause else None) or datetime.now().astimezone()

    if pause and pause.get("kind") == "break":
        # The day isn't over, so the thing to hand back is where you were
        # before the break, not yesterday's list.
        print("Back on. On a break since %s." % since.strftime("%H:%M"))
        for saved in pause.get("saved") or []:
            print("Before the break: %s" % saved.get("restore", ""))
        return 0

    now = datetime.now().astimezone()
    if pause:
        print("Back on. Paused since %s." % human(since))
        record_back(now)
    else:
        print("Not paused.")
    tomorrow = hestia_home() / "TOMORROW.md"
    if tomorrow.exists():
        # Coming back is a handover too: take it here, so the next prompt
        # doesn't hand the same list over a second time.
        due = handover_due(tomorrow)
        if due and claim_handover(due, via="back"):
            first = start_here(tomorrow.read_text(encoding="utf-8", errors="replace"))
            if first:
                print("Start here: %s" % first)
            fact = overnight_fact(due)
            if fact:
                print(fact)
        print("Yesterday's list: %s" % tomorrow)
    return 0


def cmd_break(args):
    """A midday pause: save where you are, pause agents, lift itself.

    It reuses the same pause.json and the same hook as `done`, with
    kind=break. It writes no list, because the day isn't over, and it leaves
    the morning handover alone.
    """
    now = datetime.now().astimezone()
    current = read_pause(now)
    if current and current.get("kind") != "break":
        # Never let a break shorten the night.
        print("Already clocked out until %s. A break can't shorten the night." % human(current["_until"]))
        return 0

    until = parse_break(args.length, now)

    cfg = load_config()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    prompts = read_prompts(start_of_day)
    repos = discover_repos(prompts, cfg)

    stamp = now.strftime("%Y-%m-%d-%H%M%S")
    message = "hestia break %s" % now.strftime("%Y-%m-%d %H:%M:%S")
    snapshots = [
        snapshot_repo(repo, "refs/hestia/%s" % stamp, message) for repo in repos
    ]
    saved = [s for s in snapshots if s["ref"]]

    # Headless runs are paused by the hook at their next step, not killed:
    # their work should still be there when you're back in an hour.
    if args.stop_agents:
        stopped, still_open = stop_agents(stop_all=False)
    else:
        stopped, still_open = [], running_agents()

    write_json(
        pause_path(),
        {
            "kind": "break",
            "until": until.isoformat(),
            "since": now.isoformat(),
            "saved": [
                {
                    "repo": s["repo"],
                    "ref": s["ref"],
                    "restore": restore_line(s["repo"], s["ref"]),
                }
                for s in saved
            ],
        },
    )

    result = {
        "kind": "break",
        "until": until.isoformat(),
        "until_human": until.strftime("%H:%M"),
        "snapshots": [
            {k: v for k, v in snap.items() if k != "error"} for snap in snapshots
        ],
        "saved": len(saved),
        "agents_stopped": stopped,
        "agents_still_open": still_open,
        "errors": [s["error"] for s in snapshots if s["error"]],
        "integrity_ok": all(s["untouched"] for s in snapshots),
    }
    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print("On a break until %s." % until.strftime("%H:%M"))
    for snap in saved:
        print(
            "  saved %d file%s in %s -> %s"
            % (
                snap["dirty_files"],
                "" if snap["dirty_files"] == 1 else "s",
                Path(snap["repo"]).name,
                snap["ref"],
            )
        )
    if not saved:
        print("  nothing uncommitted to save")
    if stopped:
        print("  stopped %d unattended agent%s" % (len(stopped), "" if len(stopped) == 1 else "s"))
    if still_open:
        print(
            "  %d Claude session%s paused, they'll stop at their next step"
            % (len(still_open), "" if len(still_open) == 1 else "s")
        )
    for error in result["errors"]:
        print("  warning: %s" % error, file=sys.stderr)
    return 0


def cmd_status(args):
    now = datetime.now().astimezone()
    pause = read_pause(now)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    prompts = read_prompts(start_of_day)
    load = day_load(prompts)

    result = {
        "paused": bool(pause),
        "pause_kind": pause["kind"] if pause else None,
        "paused_until": pause["_until"].isoformat() if pause else None,
        "prompts_today": load["prompts"],
        "projects_today": len(load["projects"]),
        "span_hours": load["span_hours"],
        "last_break_minutes": load["last_break_minutes"],
        "breaks": load["breaks"],
    }
    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    if pause:
        print(pause_line(pause))
    else:
        print("Working.")
    if load["prompts"]:
        print(
            "  %d prompts across %d project%s, %s hours from first to last"
            % (
                load["prompts"],
                len(load["projects"]),
                "" if len(load["projects"]) == 1 else "s",
                load["span_hours"],
            )
        )
        if load["last_break_at"]:
            print(
                "  last real break: %d min at %s"
                % (load["last_break_minutes"], load["last_break_at"].strftime("%H:%M"))
            )
        else:
            print("  no break longer than %d minutes today" % BREAK_MINUTES)
    else:
        print("  no prompts recorded today")
    return 0


# --------------------------------------------------------------------------
# dashboard: the same reading as `today`, as one static page
# --------------------------------------------------------------------------

# A page you open, read and close. It never refreshes, never alerts, and shows
# no totals, scores or comparisons: only today's facts, and only when asked.

DASHBOARD_CSS = """
:root { color-scheme: dark;
  --coal: #1C1714; --raised: #2A211C; --edge: #3A2E27; --ash: #6B615B; --ash-light: #A89E97;
  --ember: #E8743B; --ember-deep: #C2502A; --gold: #F4B740; --clay: #A4472B; --cream: #F6EDE3;
  --serif: Georgia, "Times New Roman", serif;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --mono: ui-monospace, "SF Mono", Consolas, Menlo, monospace; }
* { box-sizing: border-box; }
html { background: #120F0D; }
body { margin: 0; color: var(--ash-light); font: 15px/1.6 var(--sans);
  background: radial-gradient(ellipse 900px 520px at 50% -120px, rgba(232,116,59,.10), transparent 70%); }
main { max-width: 1040px; margin: 0 auto; padding: 28px 20px 48px; }
h1, h2, h3 { font-family: var(--serif); color: var(--cream); font-weight: normal; margin: 0; }
h2 { font-size: 20px; margin-bottom: 16px; display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; }
h2 small { font-family: var(--sans); font-size: 13px; color: var(--ash-light); }
code, pre { font-family: var(--mono); font-size: 13px; }
code { color: var(--gold); }
small { font-size: 13px; color: var(--ash-light); }
.eyebrow { font-size: 12px; text-transform: uppercase; letter-spacing: 2px; color: var(--ember); margin: 0; }

/* top bar */
.top { display: flex; align-items: center; gap: 12px; margin-bottom: 18px; }
.top .mark { width: 34px; height: 34px; }
.top h1 { font-size: 24px; letter-spacing: 1.5px; }
.top .date { margin-left: auto; font-size: 14px; text-align: right; }

/* the hearth */
.hero { position: relative; display: grid; grid-template-columns: 250px 1fr; gap: 12px 30px; align-items: center;
  padding: 26px 32px 44px; border-radius: 26px; border: 1px solid var(--edge); overflow: hidden; margin-bottom: 18px; }
.hero.tend { background: radial-gradient(circle at 150px 52%, rgba(232,116,59,.42), rgba(232,116,59,0) 300px), linear-gradient(160deg, #2A1D16, #1C1714 60%); }
.hero.rest { background: radial-gradient(circle at 150px 30%, rgba(244,183,64,.30), rgba(244,183,64,0) 300px), linear-gradient(160deg, #2A2116, #1C1714 60%); }
.hero.keep { background: radial-gradient(circle at 150px 70%, rgba(194,80,42,.30), rgba(194,80,42,0) 220px), linear-gradient(170deg, #110F14, #1C1714 75%); }
.hero .scene { width: 250px; height: 230px; }
.hero .status { font-family: var(--serif); font-size: 40px; line-height: 1.15; color: var(--cream); margin: 8px 0 10px; }
.hero blockquote { margin: 0 0 14px; padding: 2px 0 2px 14px; border-left: 3px solid var(--clay);
  font-family: var(--serif); font-style: italic; font-size: 19px; color: var(--cream); opacity: .9; }
.hero .way { display: inline-flex; align-items: center; gap: 8px; padding: 7px 14px; border-radius: 999px;
  background: rgba(28,23,20,.75); border: 1px solid var(--edge); font-size: 14px; }
.hero .quiet { margin: 0; font-size: 15px; }
.hero .key { position: absolute; left: 32px; bottom: 16px; width: calc(100% - 64px); height: 14px; opacity: .85; }
@keyframes flicker { 0%, 100% { transform: scale(1, 1); } 35% { transform: scale(.97, 1.05); } 70% { transform: scale(1.02, .97); } }
.flame { transform-origin: 32px 50px; animation: flicker 2.8s ease-in-out infinite; }
.keep .flame { animation-duration: 4.2s; }
@media (prefers-reduced-motion: reduce) { .flame { animation: none; } }

/* the day's path */
.path { position: relative; display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px;
  padding: 18px 16px 14px; margin-bottom: 18px; background: var(--coal); border: 1px solid var(--edge); border-radius: 20px; }
.path::before { content: ""; position: absolute; left: 10%; right: 10%; top: 42px; border-top: 2px dotted var(--ash); opacity: .6; }
.path .s { position: relative; text-align: center; opacity: .42; }
.path .s svg { display: block; margin: 0 auto 8px; background: var(--coal); border-radius: 12px; }
.path .s b { display: block; font-family: var(--serif); font-weight: normal; color: var(--cream); font-size: 16px; }
.path .s span { display: block; font-size: 12px; }
.path .s.on { opacity: 1; }
.path .s.on svg { box-shadow: 0 0 0 2px var(--ember), 0 0 26px rgba(232,116,59,.55); }
.path .s.on .here { display: table; margin: 6px auto 0; white-space: nowrap; font-style: normal; padding: 1px 9px; border-radius: 999px;
  background: var(--ember); color: var(--coal); font-size: 11px; font-weight: 700; letter-spacing: .5px; }
.path .here { display: none; }

/* tiles */
.tile { background: var(--coal); border: 1px solid var(--edge); border-radius: 20px; padding: 22px 24px; margin-bottom: 18px; }
.facts { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 0; }
.facts div { background: var(--raised); border: 1px solid var(--edge); border-radius: 14px; padding: 14px 16px; }
.facts dt { font-size: 12px; text-transform: uppercase; letter-spacing: 1.2px; }
.facts dd { margin: 4px 0 0; font-family: var(--serif); font-size: 32px; line-height: 1.1; color: var(--cream); font-variant-numeric: tabular-nums; }
.facts dd small { display: block; font-family: var(--sans); margin-top: 4px; }
.empty { margin: 0; }

/* timeline */
.chart { margin-top: 4px; overflow-x: auto; }
.chart svg { display: block; overflow: visible; }
svg text { fill: var(--ash-light); font: 12px var(--sans); }
.band { fill: var(--raised); }
.tick { stroke: var(--ember); stroke-width: 2; stroke-linecap: round; }
.hit:hover + .tick, .hit:focus + .tick { stroke: var(--gold); stroke-width: 3; }
.hit { fill: transparent; cursor: default; outline: none; }
.axis { stroke: var(--edge); stroke-width: 1; }
.hour { stroke: var(--edge); stroke-width: 1; stroke-dasharray: 2 4; }
.gap { stroke: var(--ash); stroke-width: 1.5; }
.gaplabel { fill: var(--cream); }
.now { stroke: var(--gold); stroke-width: 1.5; stroke-dasharray: 3 3; }
.nowlabel { fill: var(--gold); font-weight: 600; }
details { margin-top: 12px; }
summary { cursor: pointer; font-size: 13px; color: var(--ash-light); }
table { border-collapse: collapse; margin-top: 10px; font-size: 13px; width: 100%; }
th, td { text-align: left; padding: 5px 10px 5px 0; border-top: 1px solid var(--edge); }
th { color: var(--cream); font-weight: normal; }

/* lower half */
.split { display: grid; grid-template-columns: 1.55fr 1fr; gap: 18px; align-items: start; }
.split .tile { margin: 0; }
.side { display: grid; gap: 18px; }
ul.plain { list-style: none; margin: 0; padding: 0; }
ul.plain > li { padding: 10px 0; border-top: 1px solid var(--edge); }
ul.plain > li:first-child { border-top: 0; padding-top: 0; }
.row { display: flex; justify-content: space-between; gap: 12px; }
.row .n { color: var(--cream); font-variant-numeric: tabular-nums; }
.repo { color: var(--cream); }
.restore { display: block; margin-top: 6px; padding: 8px 11px; background: #0E0B09; border: 1px solid var(--edge);
  border-radius: 9px; word-break: break-all; user-select: all; cursor: text; }

/* tomorrow's list, as a page */
.letter { position: relative; background: linear-gradient(180deg, #221A16, #1C1714 120px); }
.letter .file { position: absolute; top: 24px; right: 24px; font: 12px var(--mono); color: var(--ash); }
.letter h3 { font-size: 26px; margin: 2px 0 14px; }
.letter blockquote { margin: 0 0 18px; padding: 10px 16px; border-left: 3px solid var(--ember); background: var(--raised);
  border-radius: 0 10px 10px 0; font-family: var(--serif); font-style: italic; font-size: 18px; color: var(--cream); }
.letter h4 { margin: 20px 0 8px; font: 600 12px var(--sans); text-transform: uppercase; letter-spacing: 1.5px; color: var(--ember); }
.letter ul { margin: 0; padding: 0; list-style: none; }
.letter li { position: relative; padding: 4px 0 4px 18px; color: var(--cream); }
.letter li::before { content: ""; position: absolute; left: 3px; top: 13px; width: 6px; height: 6px; border-radius: 50%; background: var(--clay); }
.letter li .sub { display: block; color: var(--ash-light); font-size: 14px; }
.letter li .sub code { display: inline-block; margin-top: 4px; padding: 5px 9px; background: #0E0B09; border: 1px solid var(--edge);
  border-radius: 8px; word-break: break-all; user-select: all; }
.letter .start li { font-family: var(--serif); font-size: 20px; padding-left: 26px; }
.letter .start li::before { width: 12px; height: 12px; top: 13px; left: 0; background: var(--gold); box-shadow: 0 0 12px rgba(244,183,64,.7); }
.letter strong { color: var(--cream); font-weight: 600; }
.letter p { margin: 0 0 8px; }

footer { margin-top: 30px; text-align: center; font-size: 13px; color: var(--ash); }
footer svg { display: block; width: 100%; height: 14px; margin-bottom: 14px; opacity: .6; }

@media (max-width: 820px) {
  .hero { grid-template-columns: 1fr; text-align: left; padding: 18px 20px 40px; }
  .hero .scene { width: 190px; height: 175px; }
  .hero .status { font-size: 30px; }
  .facts { grid-template-columns: repeat(2, 1fr); }
  .split { grid-template-columns: 1fr; }
  .path { grid-template-columns: repeat(5, minmax(0, 1fr)); padding: 14px 6px 10px; }
  .path .s span, .path .s.on .here { display: none; }
  .chart svg { min-width: 640px; }
  .path .s b { font-size: 13px; }
  .path::before { top: 38px; }
}
"""

# The brand's one flame shape (assets/icons/tend.svg) and the stage icons it
# appears in, inlined so the page needs nothing from the plugin folder.
FLAME = (
    '<path d="M32 10c3 9 13 14 13 27a13 13 0 0 1-26 0c0-6 3-10 6-14 .5 5 2.5 7.5 5 8.5'
    'C29 25 29 17 32 10z" fill="%s"/>'
)
FLAME_CORE = (
    '<path d="M32 31c2 4.5 6.5 6.5 6.5 12a6.5 6.5 0 0 1-13 0c0-3.5 2-6 3.5-8 .8 2 1.8 3 3 3.2'
    '-.2-2.5-.2-4.7 0-7.2z" fill="#F4B740"/>'
)
STAGE_ICONS = {
    "tend": '<ellipse cx="32" cy="52" rx="16" ry="3" fill="#E8743B" opacity=".25"/>'
            + FLAME % "#E8743B" + FLAME_CORE,
    "rest": '<circle cx="32" cy="15" r="6" fill="#F4B740"/>'
            '<g stroke="#F4B740" stroke-width="2" stroke-linecap="round" opacity=".7">'
            '<path d="M20 15h-4"/><path d="M48 15h-4"/><path d="M23.5 6.5l2.5 2.5"/><path d="M40.5 6.5l-2.5 2.5"/></g>'
            '<ellipse cx="32" cy="50" rx="13" ry="3" fill="#E8743B" opacity=".3"/>'
            '<g transform="translate(17.6 23.5) scale(.55)">' + FLAME % "#E8743B" + FLAME_CORE + "</g>"
            '<rect x="15" y="52" width="34" height="3" rx="1.5" fill="#6B615B"/>',
    "bank": '<ellipse cx="32" cy="47" rx="19" ry="4" fill="#E8743B" opacity=".35"/>'
            '<path d="M11 47c1-6 5-9.5 9.5-9.5 1.5-5 6-8.5 11.5-8.5s10 3.5 11.5 8.5c4.5 0 8.5 3.5 9.5 9.5z" fill="#6B615B"/>'
            '<circle cx="21" cy="46" r="2.4" fill="#E8743B"/><circle cx="32" cy="47" r="2.8" fill="#F4B740"/>'
            '<circle cx="43" cy="46" r="2.4" fill="#E8743B"/>'
            '<path d="M32 25c-3.5-3 3.5-6 0-10" stroke="#8A817C" stroke-width="2" fill="none" stroke-linecap="round"/>',
    "keep": '<circle cx="42" cy="19" r="8" fill="#F4B740"/><circle cx="46" cy="16" r="7" fill="#1C1714"/>'
            '<circle cx="19" cy="14" r="1.3" fill="#F6EDE3" opacity=".7"/>'
            '<circle cx="27" cy="22" r="1" fill="#F6EDE3" opacity=".5"/>'
            '<ellipse cx="32" cy="50" rx="10" ry="2.5" fill="#E8743B" opacity=".3"/>'
            '<g transform="translate(18 25) scale(.56)">' + FLAME % "#C2502A" + "</g>"
            '<rect x="15" y="52" width="34" height="3" rx="1.5" fill="#6B615B"/>',
    "relight": '<path d="M18 46a14 14 0 0 1 28 0z" fill="#F4B740"/>'
               '<g stroke="#F4B740" stroke-width="2.5" stroke-linecap="round"><path d="M32 18v6"/>'
               '<path d="M14.5 28.5l4 4"/><path d="M49.5 28.5l-4 4"/><path d="M9 43h4"/><path d="M51 43h4"/></g>'
               '<rect x="9" y="48" width="46" height="3" rx="1.5" fill="#6B615B"/>'
               '<g transform="translate(25.5 33) scale(.2)">' + FLAME % "#C2502A" + "</g>",
}
STAGES = [
    ("tend", "Tend", "the working day"),
    ("rest", "Rest", "a break"),
    ("bank", "Bank", "/hestia:done"),
    ("keep", "Keep", "paused overnight"),
    ("relight", "Relight", "the morning list"),
]


def _greek_key(cls, pattern_id):
    return (
        '<svg class="%s" aria-hidden="true"><defs><pattern id="%s" width="14" height="14" '
        'patternUnits="userSpaceOnUse"><path d="M0 12.5H14M11.5 12.5V2H3V9H8.5V5.5" fill="none" '
        'stroke="#A4472B" stroke-width="1.6"/></pattern></defs>'
        '<rect width="100%%" height="14" fill="url(#%s)"/></svg>' % (cls, pattern_id, pattern_id)
    )


def _icon(stage, size=64):
    return (
        '<svg width="%d" height="%d" viewBox="0 0 64 64" aria-hidden="true">'
        '<rect width="64" height="64" rx="14" fill="#1C1714"/>'
        '<rect x="0.75" y="0.75" width="62.5" height="62.5" rx="13.25" fill="none" '
        'stroke="#3A2E27" stroke-width="1.5"/>%s</svg>' % (size, size, STAGE_ICONS[stage])
    )


def _hearth_svg(stage):
    """The hero picture: one hearth, drawn for the stage you are in.

    A full fire while working, a lower one under the sun on a break, and one
    guarded ember under the moon at night. Only the flame moves.
    """
    scale, color, core = {"tend": (2.5, "#E8743B", True),
                          "rest": (1.7, "#E8743B", True),
                          "keep": (1.05, "#C2502A", False)}[stage]
    fx, fy = 125 - 32 * scale, 166 - 50 * scale
    sky = ""
    if stage == "rest":
        sky = ('<circle cx="206" cy="42" r="15" fill="#F4B740"/>'
               '<g stroke="#F4B740" stroke-width="3" stroke-linecap="round" opacity=".75">'
               '<path d="M206 14v-7"/><path d="M178 42h-7"/><path d="M241 42h-7"/>'
               '<path d="M186 22l-5-5"/><path d="M226 22l5-5"/></g>')
    elif stage == "keep":
        sky = ('<circle cx="196" cy="46" r="19" fill="#F4B740"/><circle cx="206" cy="39" r="17" fill="#15121A"/>'
               '<g fill="#F6EDE3"><circle cx="40" cy="30" r="1.8" opacity=".8"/><circle cx="72" cy="58" r="1.2" opacity=".55"/>'
               '<circle cx="150" cy="22" r="1.4" opacity=".7"/><circle cx="26" cy="86" r="1" opacity=".45"/>'
               '<circle cx="228" cy="96" r="1.3" opacity=".5"/><circle cx="110" cy="40" r="1" opacity=".5"/></g>')
    return (
        '<svg class="scene" viewBox="0 0 250 230" aria-hidden="true">%(sky)s'
        '<circle cx="125" cy="128" r="88" fill="none" stroke="#3A2E27" stroke-width="9"/>'
        '<circle cx="125" cy="128" r="88" fill="none" stroke="#6B615B" stroke-width="2.5" stroke-dasharray="15 10"/>'
        '<ellipse cx="125" cy="176" rx="%(glow)d" ry="9" fill="%(color)s" opacity=".35"/>'
        '<g transform="translate(%(fx).1f %(fy).1f) scale(%(scale)s)"><g class="flame">%(flame)s</g></g>'
        '<rect x="80" y="168" width="90" height="12" rx="6" fill="#6B615B" transform="rotate(-8 125 174)"/>'
        '<rect x="80" y="168" width="90" height="12" rx="6" fill="#A4472B" transform="rotate(8 125 174)"/>'
        '<circle cx="104" cy="180" r="2.5" fill="#E8743B"/><circle cx="146" cy="181" r="2" fill="#F4B740"/>'
        "</svg>"
    ) % {
        "sky": sky, "glow": int(26 * scale), "color": color, "fx": fx, "fy": fy, "scale": scale,
        "flame": FLAME % color + (FLAME_CORE if core else ""),
    }


def _esc(value):
    return html.escape(str(value), quote=True)


def list_snapshots(repos, limit=5):
    """The most recent refs/hestia snapshots in each repo, newest first."""
    found = []
    for repo in repos:
        result = git(
            repo,
            ["for-each-ref", "refs/hestia", "--sort=-creatordate", "--count=%d" % limit,
             "--format=%(refname)%09%(creatordate:iso-strict)"],
        )
        if not result.ok:
            continue
        for line in result.out.splitlines():
            ref, _, when = line.partition("\t")
            if ref:
                found.append({"repo": str(repo), "ref": ref, "at": parse_ts(when)})
    found.sort(key=lambda s: s["at"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return found


def _timeline_svg(prompts, now, paused):
    """Every prompt today as a tick on the hours it happened in.

    Gaps of BREAK_MINUTES or more are marked, because those are the breaks.
    One series, so no legend: the heading names it. Each tick has its own
    tooltip, and the table under the chart has the same data.
    """
    width, height = 960, 116
    left, right, top, base = 16, 944, 30, 82
    start = prompts[0]["at"].replace(minute=0, second=0, microsecond=0)
    end_at = prompts[-1]["at"] if paused else max(prompts[-1]["at"], now)
    end = end_at.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    span = (end - start).total_seconds() or 1.0

    def x(when):
        return left + (right - left) * (when - start).total_seconds() / span

    parts = [
        '<svg viewBox="0 0 %d %d" width="100%%" role="img" aria-labelledby="tl-title">' % (width, height),
        '<title id="tl-title">Prompts today, from %s to %s</title>'
        % (prompts[0]["at"].strftime("%H:%M"), prompts[-1]["at"].strftime("%H:%M")),
        '<rect class="band" x="%d" y="%d" width="%d" height="%d" rx="8"/>' % (left, top - 4, right - left, base - top + 4),
    ]

    hours = int(span // 3600)
    step = 1 if hours <= 12 else 2
    for i in range(0, hours + 1, step):
        when = start + timedelta(hours=i)
        hx = x(when)
        if left < hx < right:
            parts.append('<line class="hour" x1="%.1f" y1="%d" x2="%.1f" y2="%d"/>' % (hx, top - 4, hx, base))
        parts.append(
            '<text x="%.1f" y="%d" text-anchor="middle">%s</text>'
            % (min(max(hx, left + 14), right - 14), base + 22, when.strftime("%H:%M"))
        )
    parts.append('<line class="axis" x1="%d" y1="%d" x2="%d" y2="%d"/>' % (left, base, right, base))

    for earlier, later in zip(prompts, prompts[1:]):
        minutes = int((later["at"] - earlier["at"]).total_seconds() / 60)
        if minutes < BREAK_MINUTES:
            continue
        x1, x2 = x(earlier["at"]) + 4, x(later["at"]) - 4
        if x2 - x1 < 8:
            continue
        parts.append('<path class="gap" d="M%.1f 14v5H%.1fv-5" fill="none"/>' % (x1, x2))
        if x2 - x1 >= 48:
            parts.append(
                '<text class="gaplabel" x="%.1f" y="10" text-anchor="middle">%d min</text>'
                % ((x1 + x2) / 2, minutes)
            )

    for prompt in prompts:
        px = x(prompt["at"])
        label = "%s · %s" % (prompt["at"].strftime("%H:%M"), Path(prompt["cwd"]).name or "no project")
        parts.append(
            '<rect class="hit" x="%.1f" y="%d" width="12" height="%d" tabindex="0">'
            '<title>%s</title></rect>' % (px - 6, top - 4, base - top + 4, _esc(label))
        )
        parts.append('<line class="tick" x1="%.1f" y1="%d" x2="%.1f" y2="%d"/>' % (px, top + 4, px, base - 4))

    if not paused and start <= now <= end:
        nx = x(now)
        parts.append('<line class="now" x1="%.1f" y1="%d" x2="%.1f" y2="%d"/>' % (nx, top - 8, nx, base + 4))
        parts.append(
            '<text class="nowlabel" x="%.1f" y="%d" text-anchor="%s">now</text>'
            % (nx + (-5 if nx > right - 40 else 5), top - 12, "end" if nx > right - 40 else "start")
        )

    parts.append("</svg>")
    return "\n".join(parts)


def _md_inline(text):
    text = _esc(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)


def _render_tomorrow(text):
    """TOMORROW.md as a page: only the few shapes write_tomorrow produces.

    Everything is escaped before the inline marks are turned into tags.
    """
    out, in_list, in_item = [], False, False

    def close(level):
        nonlocal in_list, in_item
        if in_item:
            out.append("</li>")
            in_item = False
        if level == "list" and in_list:
            out.append("</ul>")
            in_list = False

    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("# "):
            close("list")
            out.append("<h3>%s</h3>" % _md_inline(line[2:]))
        elif line.startswith("> "):
            close("list")
            out.append("<blockquote>%s</blockquote>" % _md_inline(line[2:]))
        elif line.startswith("## "):
            close("list")
            title = line[3:].strip()
            out.append("<h4>%s</h4>" % _md_inline(title))
            out.append('<ul class="start">' if title == "Start here" else "<ul>")
            in_list = True
        elif line.startswith("- "):
            close("item")
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append("<li>%s" % _md_inline(line[2:]))
            in_item = True
        elif line.startswith("  ") and in_item:
            out.append('<span class="sub">%s</span>' % _md_inline(line.strip()))
        else:
            close("list")
            out.append("<p>%s</p>" % _md_inline(line))
    close("list")
    return "\n".join(out)


def render_dashboard(now, pause, prompts, load, snapshots, tomorrow_text):
    """One self-contained HTML page. No scripts, no network, nothing that refreshes."""
    if pause and pause.get("kind") == "break":
        stage, label = "rest", "On a break"
        way = "It lifts itself. <code>/hestia:back</code> ends it early."
    elif pause:
        stage, label = "keep", "Banked for the night"
        way = "<code>/hestia:back</code> if tonight can't wait."
    else:
        stage, label, way = "tend", "Tending the fire", ""
        if load["prompts"]:
            way = "Since %s, across %d project%s." % (
                load["first"].strftime("%H:%M"), len(load["projects"]),
                "" if len(load["projects"]) == 1 else "s")

    status = _esc(pause_line(pause)) if pause else "Working."
    note = (
        "<blockquote>%s</blockquote>" % _esc(pause["note"])
        if pause and pause.get("note") else ""
    )

    path = "".join(
        '<div class="s%s">%s<b>%s</b><span>%s</span><em class="here">you are here</em></div>'
        % (" on" if key == stage else "", _icon(key, 48), name, _esc(what))
        for key, name, what in STAGES
    )

    if load["prompts"]:
        if load["last_break_at"]:
            last_break = "%d min <small>at %s</small>" % (
                load["last_break_minutes"], load["last_break_at"].strftime("%H:%M"))
        else:
            last_break = "none <small>longer than %d min</small>" % BREAK_MINUTES
        facts = (
            '<dl class="facts">'
            "<div><dt>Prompts</dt><dd>%d</dd></div>"
            "<div><dt>Projects</dt><dd>%d</dd></div>"
            "<div><dt>First to last</dt><dd>%s <small>hours, %s–%s</small></dd></div>"
            "<div><dt>Last real break</dt><dd>%s</dd></div>"
            "</dl>"
        ) % (
            load["prompts"], len(load["projects"]), load["span_hours"],
            load["first"].strftime("%H:%M"), load["last"].strftime("%H:%M"), last_break,
        )
        rows = "".join(
            "<tr><td>%s</td><td>%s</td></tr>"
            % (p["at"].strftime("%H:%M"), _esc(Path(p["cwd"]).name or "no project"))
            for p in prompts
        )
        timeline = (
            '<section class="tile"><h2>When you were working <small>one mark per prompt, '
            "gaps of %d minutes or more are breaks</small></h2><div class=\"chart\">%s</div>"
            "<details><summary>As a table</summary>"
            "<table><thead><tr><th>Time</th><th>Project</th></tr></thead><tbody>%s</tbody></table>"
            "</details></section>"
        ) % (BREAK_MINUTES, _timeline_svg(prompts, now, bool(pause)), rows)

        counts = {}
        for p in prompts:
            if p["cwd"]:
                counts[p["cwd"]] = counts.get(p["cwd"], 0) + 1
        projects = "".join(
            '<li class="row"><span class="repo" title="%s">%s</span><span class="n">%d</span></li>'
            % (_esc(cwd), _esc(Path(cwd).name), n)
            for cwd, n in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ) or "<li>none recorded</li>"
    else:
        facts = '<p class="empty">No prompts recorded today.</p>'
        timeline = ""
        projects = "<li>none recorded</li>"

    if snapshots:
        snaps = "".join(
            '<li><div class="row"><span class="repo">%s</span><small>%s</small></div>'
            '<code class="restore">%s</code></li>'
            % (
                _esc(Path(s["repo"]).name),
                _esc(human(s["at"]) if s["at"] else ""),
                _esc(restore_line(s["repo"], s["ref"])),
            )
            for s in snapshots
        )
    else:
        snaps = "<li>no snapshots yet</li>"

    if tomorrow_text and tomorrow_text.strip():
        tomorrow = (
            '<section class="tile letter"><span class="file">~/.hestia/TOMORROW.md</span>'
            '<p class="eyebrow">Tomorrow\'s list</p>%s</section>' % _render_tomorrow(tomorrow_text.strip())
        )
    else:
        tomorrow = (
            '<section class="tile letter"><p class="eyebrow">Tomorrow\'s list</p>'
            '<h3>Not written yet</h3><p class="empty">No list yet. <code>/hestia:done</code> writes one.</p></section>'
        )

    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hestia · %(date)s</title>
<style>%(css)s</style></head>
<body><main>
<div class="top">
<svg class="mark" viewBox="0 0 64 64" aria-hidden="true"><rect width="64" height="64" rx="14" fill="#1C1714"/>%(mark)s</svg>
<h1>Hestia</h1>
<div class="date">%(date)s<br><small>the same reading as <code>/hestia:today</code></small></div>
</div>
<section class="hero %(stage)s">
%(scene)s
<div><p class="eyebrow">%(label)s</p><p class="status">%(status)s</p>%(note)s%(way)s</div>
%(key)s
</section>
<nav class="path" aria-label="Stages of the day">%(path)s</nav>
<section class="tile"><h2>Today</h2>%(facts)s</section>
%(timeline)s
<div class="split">
%(tomorrow)s
<div class="side">
<section class="tile"><h2>Saved work <small>click a line to select it</small></h2><ul class="plain">%(snaps)s</ul></section>
<section class="tile"><h2>Projects <small>prompts today</small></h2><ul class="plain">%(projects)s</ul></section>
</div>
</div>
<footer>%(footkey)s Read at %(time)s. This page doesn't refresh: run <code>/hestia:dashboard</code> again for a new reading.</footer>
</main></body></html>
""" % {
        "css": DASHBOARD_CSS,
        "mark": STAGE_ICONS["tend"],
        "stage": stage,
        "scene": _hearth_svg(stage),
        "key": _greek_key("key", "key-top"),
        "footkey": _greek_key("", "key-foot"),
        "label": label,
        "status": status,
        "note": note,
        "way": ('<span class="way">%s</span>' if pause else '<p class="quiet">%s</p>') % way if way else "",
        "path": path,
        "date": now.strftime("%A %d %B"),
        "time": now.strftime("%H:%M"),
        "facts": facts,
        "timeline": timeline,
        "projects": projects,
        "snaps": snaps,
        "tomorrow": tomorrow,
    }


def has_screen():
    """A guess: would a page opened here reach a screen someone is looking at?"""
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return True
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return False  # over SSH even a Mac would open it on its own screen, not yours
    return sys.platform == "darwin" or os.name == "nt"


def cmd_dashboard(args):
    cfg = load_config()
    if cfg.get("screen") is False:
        # The user told init there is no screen here, so there is no page to open.
        print("No dashboard on this computer: it has no screen. /hestia:today gives the same reading.")
        print("To change that, run /hestia:init again.")
        return 0

    now = datetime.now().astimezone()
    pause = read_pause(now)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    prompts = read_prompts(start_of_day)
    load = day_load(prompts)
    snapshots = list_snapshots(discover_repos(prompts, cfg))

    tomorrow_path = hestia_home() / "TOMORROW.md"
    tomorrow_text = tomorrow_path.read_text(encoding="utf-8", errors="replace") if tomorrow_path.exists() else ""

    out = Path(args.out) if args.out else hestia_home() / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_dashboard(now, pause, prompts, load, snapshots, tomorrow_text), encoding="utf-8")
    print("Dashboard: %s" % out)

    # Without an answer from init, only open a browser where there's a display.
    # On a server it would do nothing, or start a text browser and hang.
    if not args.no_open and (cfg.get("screen") is True or has_screen()):
        import webbrowser

        webbrowser.open(out.resolve().as_uri())
    return 0


def is_way_back_in(tool, tool_input):
    """True only for a single shell command that lifts the pause or reads it."""
    if tool not in SHELL_TOOLS:
        return False
    command = str(tool_input.get("command") or "").strip()
    command = re.sub(r"\s*2>&1\s*$", "", command)  # folding stderr in runs nothing new
    if command.startswith("&"):  # PowerShell's call operator
        command = command[1:].lstrip()
    if SHELL_JOINERS.search(command):
        return False
    return bool(RESUME_PATTERN.match(command))


def cmd_hook(args):
    """PreToolUse hook. Silent and immediate when there is no pause."""
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    pause = read_pause()
    if not pause:
        return 0  # no pause, no cost, no output

    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}

    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}

    # Never block the way back in.
    if is_way_back_in(tool, tool_input):
        return 0

    if pause.get("kind") == "break":
        reason = "%s Run /hestia:back to end it early." % pause_line(pause)
    else:
        reason = "%s Run /hestia:back if tonight can't wait." % pause_line(pause)
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    return 0


# --------------------------------------------------------------------------
# the morning handover
# --------------------------------------------------------------------------
#
# Stopping is only possible if picking back up is guaranteed. So the list
# written at shutdown is handed to the first session that a person is
# actually at once the pause is over, whichever way the morning starts:
#
#   - a new session            -> SessionStart (startup or resume)
#   - a window left open       -> UserPromptSubmit, on its first prompt
#   - /hestia:back             -> back takes it itself and prints where to start
#
# It is handed over exactly once. The claim is an O_EXCL file named after the
# list, so two sessions starting in the same instant can't both get it.
# Scripted runs (claude -p, the Agent SDK) never take it: nobody is there to
# read it, and taking it would hide it from the person who is.

HANDOVER_LEAD = (
    "Where the last working day was left off, from Hestia, written at shutdown. "
    "Keep it in mind to pick up the thread, and do not act on it unless asked. "
    "If the user greets you or asks where they were, answer with the 'Start here' "
    "item in one sentence. Do not recite the list."
)


def handover_path():
    return hestia_home() / "handover.json"


def _list_key(tomorrow):
    return str(tomorrow.stat().st_mtime_ns)


def _claim_path(key):
    return hestia_home() / ("handover-%s.taken" % key)


def arm_handover(tomorrow, since=None):
    """A fresh list: forget any earlier claim and make this one due."""
    for old in hestia_home().glob("handover-*.taken"):
        try:
            old.unlink()
        except OSError:
            pass
    write_json(
        handover_path(),
        {
            "for": _list_key(tomorrow),
            "consumed": False,
            "since": (since or datetime.now().astimezone()).isoformat(),
        },
    )


def record_back(now):
    """Remember when the pause was lifted early, for the overnight fact."""
    state = read_json(handover_path())
    if isinstance(state, dict) and not state.get("consumed"):
        state["back_at"] = now.isoformat()
        write_json(handover_path(), state)


def handover_due(tomorrow=None):
    """The handover state if the list is still waiting to be handed over, else None.

    Cheap on purpose: on a normal day this is one or two stat calls.
    """
    tomorrow = tomorrow or (hestia_home() / "TOMORROW.md")
    try:
        key = _list_key(tomorrow)
    except OSError:
        return None  # no list, nothing to hand over
    if _claim_path(key).exists():
        return None  # already handed over
    state = read_json(handover_path()) or {}
    # Lists armed before claims existed recorded a float mtime instead.
    if state.get("consumed") and str(state.get("for")) in (key, str(tomorrow.stat().st_mtime)):
        return None
    if read_pause():
        return None  # still clocked out; the list is not due yet
    state = dict(state)
    state["key"] = key
    return state


def claim_handover(due, via, session="", cwd=""):
    """Take the handover. True for exactly one caller per list."""
    try:
        handle = os.open(str(_claim_path(due["key"])), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except OSError:
        return False  # someone else got it first, or the folder is gone
    os.close(handle)
    record = {k: v for k, v in due.items() if k != "key"}
    record.update(
        {
            "for": due["key"],
            "consumed": True,
            "at": datetime.now().astimezone().isoformat(),
            "via": via,
            "session": session,
            "cwd": cwd,
        }
    )
    write_json(handover_path(), record)
    return True


def session_is_headless():
    """True when no person is at this Claude Code session.

    Claude Code tells hooks directly: CLAUDE_CODE_SESSION_ATTENDED is 0 and
    CLAUDE_CODE_ENTRYPOINT is sdk-* for `claude -p` and Agent SDK runs, and
    1 / cli for an interactive one. Older versions fall back to the launching
    process's own flags. When nothing can be told, assume a person is there:
    a list given to a script is lost, but a list given to a person never is.
    """
    forced = os.environ.get("HESTIA_SESSION_KIND", "")
    if forced:
        return forced == "headless"
    attended = os.environ.get("CLAUDE_CODE_SESSION_ATTENDED", "")
    if attended in ("0", "1"):
        return attended == "0"
    entry = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")
    if entry:
        return entry.startswith("sdk")
    pid = os.environ.get("CLAUDE_PID", "")
    if pid.isdigit():
        try:
            proc = subprocess.run(
                ["ps", "-o", "command=", "-p", pid],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=2,
            )
            command = proc.stdout.decode("utf-8", "replace") + " "
            return any(marker in command for marker in HEADLESS_MARKERS)
        except (OSError, subprocess.SubprocessError):
            pass
    return False


def start_here(text):
    """The first item under 'Start here' in a list, or ''."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "## Start here":
            for item in lines[index + 1:]:
                if item.startswith("- "):
                    return item[2:].strip()
                if item.startswith("## "):
                    break
    return ""


def _blocked_by_pause(block):
    text = json.dumps(block.get("content"))
    return "Clocked out until" in text or "On a break until" in text


def overnight_fact(due, now=None):
    """What actually ran between shutdown and now, read from the session logs.

    A plain fact, never a score: either nothing ran, or how many tool calls
    did and when the first one was. Tool calls the pause blocked are counted
    separately, because they are the pause doing its job. `back` itself is
    left out, since lifting the pause is not work.
    """
    since = parse_ts((due or {}).get("since"))
    if since is None:
        return ""
    end = parse_ts(due.get("back_at")) or now or datetime.now().astimezone()
    uses, blocked = {}, set()
    root = log_dir()
    if root.exists():
        for path in root.glob("*/*.jsonl"):
            try:
                if path.stat().st_mtime < since.timestamp():
                    continue
                handle = path.open("r", encoding="utf-8", errors="replace")
            except OSError:
                continue
            with handle:
                for line in handle:
                    if '"tool_use"' not in line and '"tool_result"' not in line:
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    when = parse_ts(entry.get("timestamp"))
                    if when is None or not (since <= when <= end):
                        continue
                    content = (entry.get("message") or {}).get("content")
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_use":
                            command = str((block.get("input") or {}).get("command") or "")
                            if RESUME_PATTERN.search(command):
                                continue
                            uses[block.get("id")] = (when, entry.get("cwd") or "")
                        elif block.get("type") == "tool_result" and _blocked_by_pause(block):
                            blocked.add(block.get("tool_use_id"))
    ran = sorted(v for k, v in uses.items() if k not in blocked)
    stopped = len([k for k in uses if k in blocked])
    window = "between %s and %s" % (human(since), human(end))
    if not ran:
        fact = "Overnight: nothing ran %s." % window
        if stopped:
            fact += " The pause blocked %d tool call%s." % (stopped, "" if stopped == 1 else "s")
        return fact
    first_at, first_cwd = ran[0]
    return "Overnight: %d tool call%s ran %s, the first at %s%s." % (
        len(ran), "" if len(ran) == 1 else "s", window, first_at.strftime("%H:%M"),
        (" in " + Path(first_cwd).name) if first_cwd else "",
    )


def _read_hook_input():
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _deliver_handover(event):
    """Shared by both hooks. Silent and immediate unless the list is due."""
    # Read what Claude Code sends first, as the pause hook does, so it never
    # writes into a pipe that has already closed.
    payload = _read_hook_input()
    tomorrow = hestia_home() / "TOMORROW.md"
    due = handover_due(tomorrow)
    if not due:
        return 0
    if session_is_headless():
        return 0  # leave it for the person
    text = tomorrow.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return 0
    if not claim_handover(due, via=event, session=str(payload.get("session_id") or ""),
                          cwd=str(payload.get("cwd") or "")):
        return 0
    fact = overnight_fact(due)
    context = HANDOVER_LEAD + "\n\n" + ((fact + "\n\n") if fact else "") + text
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}))
    return 0


def cmd_session_start(args):
    """SessionStart hook: hand yesterday's list to a new or resumed session, once."""
    return _deliver_handover("SessionStart")


def cmd_prompt_submit(args):
    """UserPromptSubmit hook: the same handover, for a window left open overnight."""
    return _deliver_handover("UserPromptSubmit")


def cmd_handover(args):
    tomorrow = hestia_home() / "TOMORROW.md"
    if not tomorrow.exists():
        print("No list yet. /hestia:done writes one.")
        return 0
    if args.again:
        state = read_json(handover_path()) or {}
        arm_handover(tomorrow, since=parse_ts(state.get("since")))
        print("The next session you open will get the list again.")
        return 0
    if handover_due(tomorrow):
        print("Due: the next session you're at will get %s." % tomorrow)
    elif read_pause():
        print("Waiting for the pause to end.")
    else:
        state = read_json(handover_path()) or {}
        when = parse_ts(state.get("at"))
        print("Handed over%s." % ((" at " + human(when)) if when else ""))
    return 0


# --------------------------------------------------------------------------
# why: the facts behind "who did this help, and what changed for them"
# --------------------------------------------------------------------------

def repo_commits(repo, since, limit=30):
    """Commits on local branches since `since`, newest first.

    Uses --branches rather than --all, so Hestia's own refs/hestia snapshots
    are never mistaken for work. Filters to the repo's configured author when
    there is one, so a busy shared repo doesn't put words in your mouth.
    """
    args = [
        "log", "--branches", "--since=%s" % since.isoformat(),
        "--format=%h%x09%s", "-n", str(limit),
    ]
    # The identity git would commit as right now, which honours both config
    # and GIT_AUTHOR_EMAIL, unlike reading user.email alone.
    ident = git(repo, ["var", "GIT_AUTHOR_IDENT"]).out
    match = re.search(r"<([^>]+)>", ident)
    email = match.group(1) if match else ""
    if email:
        args.append("--author=%s" % email)
    result = git(repo, args)
    commits = []
    if result.ok:
        for line in result.out.splitlines():
            if "\t" in line:
                sha, subject = line.split("\t", 1)
                commits.append({"sha": sha, "subject": subject})
    return commits


def last_note():
    """The note from the most recent `done`, if the list is still there."""
    tomorrow = hestia_home() / "TOMORROW.md"
    try:
        for line in tomorrow.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("> "):
                return line[2:].strip()
    except OSError:
        pass
    return ""


def gather_why(days=1, extra_repos=None):
    now = datetime.now().astimezone()
    since = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=max(days, 1) - 1)
    prompts = read_prompts(since)
    repos = discover_repos(prompts, load_config(), extra=extra_repos)
    found = []
    for repo in repos:
        commits = repo_commits(repo, since)
        status = git(repo, ["status", "--porcelain"]).out
        uncommitted = len([line for line in status.splitlines() if line.strip()])
        if not commits and not uncommitted:
            continue
        found.append(
            {
                "name": Path(repo).name,
                "path": str(repo),
                "branch": git(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).out,
                "commits": commits,
                "uncommitted_files": uncommitted,
            }
        )
    recent = []
    for prompt in prompts[-15:]:
        single = " ".join(prompt["text"].split())
        recent.append(single if len(single) <= 200 else single[:197] + "...")
    return {
        "since": since.isoformat(),
        "days": max(days, 1),
        "note": last_note(),
        "repos": found,
        "prompt_count": len(prompts),
        "recent_prompts": recent,
    }


def cmd_why(args):
    facts = gather_why(args.days, args.repo)
    if args.json:
        print(json.dumps(facts, indent=2))
        return 0
    since = parse_ts(facts["since"])
    print("What the work since %s touched:" % since.strftime("%a %d %b"))
    if not facts["repos"] and not facts["prompt_count"]:
        print("  nothing yet: no commits, no open changes, no prompts")
        return 0
    for repo in facts["repos"]:
        print(
            "  %s (%s): %d commit%s, %d file%s uncommitted"
            % (
                repo["name"], repo["branch"],
                len(repo["commits"]), "" if len(repo["commits"]) == 1 else "s",
                repo["uncommitted_files"], "" if repo["uncommitted_files"] == 1 else "s",
            )
        )
        for commit in repo["commits"][:5]:
            print("    %s %s" % (commit["sha"], commit["subject"]))
    print("  %d prompt%s" % (facts["prompt_count"], "" if facts["prompt_count"] == 1 else "s"))
    if facts["note"]:
        print("  last shutdown note: %s" % facts["note"])
    return 0


# --------------------------------------------------------------------------
# new: only what changed in Claude Code, so one thing can be picked from it
# --------------------------------------------------------------------------

VERSION_RE = re.compile(r"(\d+(?:\.\d+)+)")


def version_key(text):
    match = VERSION_RE.search(text or "")
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def installed_claude_version():
    try:
        proc = subprocess.run(
            ["claude", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = VERSION_RE.search(proc.stdout.decode("utf-8", "replace"))
    return match.group(1) if match else None


def parse_changelog(text):
    """[(version, [change, ...]), ...] in file order, newest first."""
    releases = []
    current = None
    for line in text.splitlines():
        heading = re.match(r"^##\s+v?(\d+(?:\.\d+)+)\b", line)
        if heading:
            current = (heading.group(1), [])
            releases.append(current)
            continue
        if current is None:
            continue
        if line.startswith("- ") or line.startswith("* "):
            current[1].append(line[2:].strip())
        elif line.startswith("  ") and current[1] and line.strip():
            current[1][-1] += " " + line.strip()
    return releases


def fetch_changelog(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.read().decode("utf-8", "replace"), None
    except Exception as error:  # offline, DNS, 404, bad URL: all mean "couldn't check"
        return None, str(error) or error.__class__.__name__


def gather_new(since=None):
    url = os.environ.get("HESTIA_CHANGELOG_URL") or CHANGELOG_URL
    installed = installed_claude_version()
    digest_path = hestia_home() / "digest.json"
    digest = read_json(digest_path) or {}

    text, error = fetch_changelog(url)
    if text is None:
        return {"ok": False, "reason": error, "installed": installed}
    releases = parse_changelog(text)
    if not releases:
        return {"ok": False, "reason": "the changelog had no releases in it", "installed": installed}

    # "Since your version or the last check", whichever is later.
    if since:
        baseline = since
    else:
        known = [v for v in (installed, digest.get("seen")) if version_key(v)]
        baseline = max(known, key=version_key) if known else None

    if baseline is None:
        newer = releases[:1]  # never checked and no claude on PATH: just the latest
    else:
        newer = [r for r in releases if version_key(r[0]) > version_key(baseline)]

    shown, budget = [], NEW_MAX_CHANGES
    for version, changes in newer[:NEW_MAX_RELEASES]:
        if budget <= 0:
            break
        taken = changes[:budget]
        budget -= len(taken)
        shown.append(
            {
                "version": version,
                "installed": bool(installed) and version_key(version) <= version_key(installed),
                "total_changes": len(changes),
                "changes": taken,
            }
        )

    write_json(
        digest_path,
        {"seen": releases[0][0], "checked_at": datetime.now().astimezone().isoformat()},
    )
    return {
        "ok": True,
        "installed": installed,
        "since": baseline,
        "latest": releases[0][0],
        "releases": shown,
        "left_out": len(newer) - len(shown),
    }


def cmd_new(args):
    facts = gather_new(args.since)
    if args.json:
        print(json.dumps(facts, indent=2))
        return 0
    if not facts["ok"]:
        print("Couldn't check the Claude Code changelog, so there's nothing to do.")
        return 0
    if not facts["releases"]:
        print("Nothing new since %s." % facts["since"])
        return 0
    print(
        "%d release%s since %s (you have %s):"
        % (
            len(facts["releases"]),
            "" if len(facts["releases"]) == 1 else "s",
            facts["since"] or "your last check",
            facts["installed"] or "no claude on PATH",
        )
    )
    for release in facts["releases"]:
        print(
            "  %s  %d change%s%s"
            % (
                release["version"],
                release["total_changes"],
                "" if release["total_changes"] == 1 else "s",
                "" if release["installed"] else ", not installed yet",
            )
        )
    if facts["left_out"]:
        print("  %d older release%s left out" % (facts["left_out"], "" if facts["left_out"] == 1 else "s"))
    return 0


# init: get to know the user, once
# --------------------------------------------------------------------------

SECTION_START = "<!-- hestia:start -->"
SECTION_END = "<!-- hestia:end -->"

# First match wins. Covers the connectors in connectors/README.md; anything
# else is recorded as "other" rather than guessed. Microsoft 365 counts as a
# calendar because that's the part Hestia would read.
CONNECTOR_KINDS = [
    ("calendar", ("calendar", "gcal", "reclaim", "calendly", "cal.com", "microsoft 365", "microsoft_365", "m365")),
    ("email", ("gmail", "outlook", "mail")),
    ("chat", ("slack", "discord", "imessage", "teams", "telegram", "whatsapp")),
    ("tasks", ("linear", "jira", "atlassian", "asana", "monday", "clickup", "todoist", "ticktick", "trello")),
    ("code", ("github", "gitlab", "bitbucket")),
    ("docs", ("notion", "drive", "docs", "sheets", "slides", "confluence", "dropbox")),
]

TONES = {
    "plain": "Keep it short and plain.",
    "warm": "Be warm and encouraging, but brief.",
    "detail": "Detail is welcome when I ask how something works.",
}

CONSENTS = ("allowed", "declined", "unasked")
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def claude_dir():
    if os.environ.get("HESTIA_CLAUDE_DIR"):
        return Path(os.environ["HESTIA_CLAUDE_DIR"])
    return claude_config_dir() or Path.home() / ".claude"


def claude_json_path():
    if os.environ.get("HESTIA_CLAUDE_JSON"):
        return Path(os.environ["HESTIA_CLAUDE_JSON"])
    config = claude_config_dir()
    return config / ".claude.json" if config else Path.home() / ".claude.json"


def claude_md_path():
    return claude_dir() / "CLAUDE.md"


def connectors_path():
    return hestia_home() / "connectors.json"


def profile_path():
    return hestia_home() / "profile.json"


def connector_key(server):
    """'claude_ai_Google_Calendar' -> 'google-calendar'."""
    name = re.sub(r"^(claude[_ .]ai[_ ]|plugin:[^:]*:)", "", server.strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unnamed"


def display_name(server):
    """'claude_ai_Google_Calendar' -> 'Google Calendar', for people to read."""
    name = re.sub(r"^(claude[_ .]ai[_ ]|plugin:[^:]*:)", "", server.strip(), flags=re.I)
    return name.replace("_", " ").strip() or server


def classify_connector(server):
    lowered = server.lower()
    for kind, words in CONNECTOR_KINDS:
        if any(word in lowered for word in words):
            return kind
    return "other"


def tool_prefix(server):
    """Claude Code names MCP tools mcp__<server>__<tool>."""
    return "mcp__%s__" % re.sub(r"[^A-Za-z0-9_-]", "_", server)


def _same_path(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def detect_connectors(cwd, seen=()):
    """MCP servers this user has, keyed by a stable slug.

    Configured servers come from Claude Code's own files. claude.ai connectors
    aren't in any file, so the command passes the servers Claude can see.
    """
    found = {}

    def add(server, source):
        key = connector_key(server)
        if key in found:
            return
        found[key] = {
            "server": server,
            "name": display_name(server),
            "kind": classify_connector(server),
            "source": source,
            "status": "available",
            "tool_prefix": tool_prefix(server),
        }

    config = read_json(claude_json_path()) or {}
    for server in (config.get("mcpServers") or {}):
        add(server, "user")
    for path, project in (config.get("projects") or {}).items():
        if isinstance(project, dict) and _same_path(path, cwd):
            for server in (project.get("mcpServers") or {}):
                add(server, "local")
    for server in ((read_json(Path(cwd) / ".mcp.json") or {}).get("mcpServers") or {}):
        add(server, "project")
    for server in seen:
        server = server.strip()
        if server:
            add(server, "claude.ai" if re.match(r"claude[_ .]ai", server, re.I) else "session")
    return found


def usual_hours(now, days=14):
    """Typical first and last prompt of the day, from recent logs."""
    by_day = {}
    for prompt in read_prompts(now - timedelta(days=days)):
        local = prompt["at"].astimezone()
        first, last = by_day.get(local.date(), (local, local))
        by_day[local.date()] = (min(first, local), max(last, local))
    if not by_day:
        return None

    def median_clock(times):
        minutes = sorted(t.hour * 60 + t.minute for t in times)
        middle = minutes[len(minutes) // 2]
        return "%02d:%02d" % (middle // 60, middle % 60)

    return {
        "days_seen": len(by_day),
        "usual_start": median_clock([first for first, _ in by_day.values()]),
        "usual_end": median_clock([last for _, last in by_day.values()]),
    }


def _find_program(name):
    found = shutil.which(name)
    if found or name != "gh":
        return found
    # The GitHub CLI installer doesn't always reach PATH in an open shell.
    for folder in (os.environ.get("ProgramFiles", ""), "/opt/homebrew/bin", "/usr/local/bin"):
        candidate = Path(folder) / ("GitHub CLI/gh.exe" if os.name == "nt" else "gh")
        if folder and candidate.exists():
            return str(candidate)
    return None


def _run_quiet(args, timeout=5):
    program = _find_program(args[0])
    if not program:
        return ""
    try:
        proc = subprocess.run([program] + list(args[1:]), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.decode("utf-8", "replace").strip()


def detect_environment(cwd, seen=(), now=None):
    """Everything init can learn without asking, so it only asks the rest."""
    now = now or datetime.now().astimezone()
    md = claude_md_path()
    md_text = md.read_bytes().decode("utf-8", "replace") if md.exists() else ""
    gh = re.search(r"account (\S+)", _run_quiet(["gh", "auth", "status"]))
    return {
        "name": _run_quiet(["git", "config", "--global", "user.name"]) or None,
        "github_login": gh.group(1) if gh else None,
        "hours": usual_hours(now),
        "claude_md": {
            "path": str(md),
            "exists": md.exists(),
            "has_hestia_section": SECTION_START in md_text,
        },
        "config": load_config(),
        "has_screen": has_screen(),
        "profile": read_json(profile_path()),
        "connectors": detect_connectors(cwd, seen),
        "consent": {
            key: entry.get("consent", "unasked")
            for key, entry in ((read_json(connectors_path()) or {}).get("connectors") or {}).items()
        },
    }


def _days_label(days):
    days = sorted(set(days))
    if days == list(range(days[0], days[-1] + 1)) and len(days) > 1:
        return "%s-%s" % (DAY_NAMES[days[0]], DAY_NAMES[days[-1]])
    return ", ".join(DAY_NAMES[d] for d in days)


def render_claude_section(profile, cfg, connectors):
    """The block that lives in ~/.claude/CLAUDE.md. Short: it loads every session."""
    lines = [SECTION_START, "## Hestia: about me", ""]
    if profile.get("name"):
        lines.append("- Name: %s." % profile["name"])
    if profile.get("why"):
        lines.append("- I build because: %s." % profile["why"].rstrip("."))
    if profile.get("helps"):
        lines.append("- Right now my work helps: %s." % profile["helps"].rstrip("."))
    lines.append(
        "- When you explain a change, say what it does for people first, then how. %s"
        % TONES.get(profile.get("tone"), TONES["plain"])
    )
    lines.append(
        "- My working days are %s. Hestia pauses agents overnight and resumes at %02d:00; "
        "after I run /hestia:done, don't suggest more work." % (_days_label(cfg["working_days"]), cfg["resume_hour"])
    )
    allowed = sorted(c["name"] for c in connectors.values() if c.get("consent") == "allowed")
    lines.append(
        "- Connectors Hestia may use: %s. Don't suggest installing others."
        % (", ".join(allowed) if allowed else "none")
    )
    lines += ["", "<!-- Written by /hestia:init. Re-run it to change this; remove with: hestia.py init remove -->", SECTION_END]
    return "\n".join(lines)


def _find_section(text):
    start = text.find(SECTION_START)
    if start < 0:
        return None
    end = text.find(SECTION_END, start)
    if end < 0:
        raise ValueError("CLAUDE.md has a Hestia start marker but no end marker; fix it by hand")
    return start, end + len(SECTION_END)


def _backup(path):
    if path.exists():
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        target = hestia_home() / "backups" / ("CLAUDE.md." + stamp)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(path), str(target))


def upsert_marked_section(path, block):
    """Insert or replace the Hestia block. Text outside the markers is kept byte for byte.

    The block is always joined with a single newline on each side, so
    remove_marked_section can take it out again exactly.
    """
    path = Path(path)
    text = path.read_bytes().decode("utf-8") if path.exists() else ""
    span = _find_section(text)
    if span:
        updated = text[: span[0]] + block + text[span[1]:]
    elif text:
        updated = text + "\n" + block + "\n"
    else:
        updated = block + "\n"
    if updated != text:
        _backup(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(updated.encode("utf-8"))
    return updated != text


def remove_marked_section(path):
    path = Path(path)
    if not path.exists():
        return False
    text = path.read_bytes().decode("utf-8")
    span = _find_section(text)
    if not span:
        return False
    block = text[span[0]:span[1]]
    for joined in ("\n" + block + "\n", block + "\n", block):
        if joined in text:
            text = text.replace(joined, "", 1)
            break
    _backup(path)
    if text:
        path.write_bytes(text.encode("utf-8"))
    else:
        path.unlink()  # we created it, so leave nothing behind
    return True


def _clean_answers(raw):
    answers = {}
    for field in ("name", "why", "helps"):
        value = str(raw.get(field) or "").strip()
        if value:
            answers[field] = value[:200]
    if raw.get("tone") in TONES:
        answers["tone"] = raw["tone"]
    hour = raw.get("resume_hour")
    if isinstance(hour, int) and 0 <= hour <= 23:
        answers["resume_hour"] = hour
    days = raw.get("working_days")
    if isinstance(days, list) and days and all(isinstance(d, int) and 0 <= d <= 6 for d in days):
        answers["working_days"] = sorted(set(days))
    if isinstance(raw.get("screen"), bool):
        answers["screen"] = raw["screen"]
    if isinstance(raw.get("dream"), bool):
        answers["dream"] = raw["dream"]
    consent = raw.get("connectors") or {}
    answers["connectors"] = {
        connector_key(k): v for k, v in consent.items() if v in ("allowed", "declined")
    }
    return answers


def init_write(answers, cwd, seen=(), now=None):
    """Save the answers everywhere Hestia and Claude read them. Returns what was written."""
    now = now or datetime.now().astimezone()
    answers = _clean_answers(answers)
    detected = detect_environment(cwd, seen, now)

    profile = dict(detected["profile"] or {})
    profile.update({k: answers[k] for k in ("name", "why", "helps", "tone", "dream") if k in answers})
    profile.setdefault("name", detected["name"])
    profile.setdefault("tone", "plain")
    profile.setdefault("dream", True)
    profile["github_login"] = detected["github_login"]
    profile["hours"] = detected["hours"]
    profile["updated_at"] = now.isoformat()
    write_json(profile_path(), profile)

    config_file = hestia_home() / "config.json"
    raw_config = read_json(config_file) or {}
    for field in ("resume_hour", "working_days", "screen"):
        if field in answers:
            raw_config[field] = answers[field]
    write_json(config_file, raw_config)

    # Re-running init is the one place a "no" can turn into a "yes".
    connectors = detected["connectors"]
    for key, entry in connectors.items():
        entry["consent"] = answers["connectors"].get(key, "unasked")
        entry["asked_at"] = now.isoformat() if key in answers["connectors"] else None
    kinds_present = {entry["kind"] for entry in connectors.values()}
    write_json(
        connectors_path(),
        {
            "version": 1,
            "checked_at": now.isoformat(),
            "connectors": connectors,
            "missing_kinds": [kind for kind, _ in CONNECTOR_KINDS if kind not in kinds_present],
        },
    )

    block = render_claude_section(profile, load_config(), connectors)
    upsert_marked_section(claude_md_path(), block)
    return {
        "profile": str(profile_path()),
        "config": str(config_file),
        "connectors": str(connectors_path()),
        "claude_md": str(claude_md_path()),
        "allowed": sorted(e["name"] for e in connectors.values() if e["consent"] == "allowed"),
    }


def _seen_list(value):
    return [part for part in re.split(r"[,\s]+", value or "") if part]


def cmd_init(args):
    cwd = os.getcwd()
    seen = _seen_list(args.seen)
    if args.action == "remove":
        removed = remove_marked_section(claude_md_path())
        print("Removed the Hestia section from %s." % claude_md_path() if removed
              else "No Hestia section in %s; nothing to remove." % claude_md_path())
        print("Profile and connector choices are kept in %s; delete that folder to forget them." % hestia_home())
        return 0

    if args.action == "write":
        raw = sys.stdin.read() if args.answers == "-" else args.answers
        try:
            answers = json.loads(raw or "{}")
        except ValueError as exc:
            print("--answers is not valid JSON: %s" % exc, file=sys.stderr)
            return 1
        if not isinstance(answers, dict):
            print("--answers must be a JSON object", file=sys.stderr)
            return 1
        try:
            written = init_write(answers, cwd, seen)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(written, indent=2))
            return 0
        print("Hestia knows you now: %s" % written["profile"])
        print("Every session will see it: section added to %s" % written["claude_md"])
        print("Connectors allowed: %s. Undo with: hestia.py init remove"
              % (", ".join(written["allowed"]) or "none"))
        return 0

    detected = detect_environment(cwd, seen)
    if args.json:
        print(json.dumps(detected, indent=2, default=str))
        return 0
    hours = detected["hours"]
    print("Name: %s" % (detected["name"] or "unknown"))
    print("GitHub: %s" % (detected["github_login"] or "not logged in"))
    print("Usual day: %s" % ("%s to %s, over %d days" % (hours["usual_start"], hours["usual_end"], hours["days_seen"])
                             if hours else "no history yet"))
    print("Connectors: %s" % (", ".join(sorted(detected["connectors"])) or "none found"))
    print("Screen: %s" % ("yes" if detected["has_screen"] else "none found"))
    print("CLAUDE.md: %s" % ("has a Hestia section" if detected["claude_md"]["has_hestia_section"]
                             else "exists" if detected["claude_md"]["exists"] else "not created yet"))
    return 0


def cmd_connector(args):
    """The gate the connectors work calls before touching a connector.

    Exit 0: allowed, go ahead. Exit 10: never asked, ask once now and record
    the answer with --set. Exit 1: declined or not there, skip silently.
    """
    state = read_json(connectors_path()) or {"version": 1, "connectors": {}, "missing_kinds": []}
    connectors = state.setdefault("connectors", {})
    key = connector_key(args.name)
    entry = connectors.get(key)
    now = datetime.now().astimezone().isoformat()

    if entry is None and (args.present or args.set):
        # Appeared after init: it gets exactly one question, like the others did.
        entry = connectors[key] = {
            "server": args.name,
            "name": display_name(args.name),
            "kind": classify_connector(args.name),
            "source": "session",
            "status": "available",
            "tool_prefix": tool_prefix(args.name),
            "consent": "unasked",
            "asked_at": None,
        }

    if args.set:
        entry["consent"] = args.set
        entry["asked_at"] = now
        write_json(connectors_path(), state)
        print(args.set)
        return 0

    if entry is None:
        print("missing")
        return 1
    if args.present:
        write_json(connectors_path(), state)
    consent = entry.get("consent", "unasked")
    print(consent if consent != "unasked" else "ask")
    return {"allowed": 0, "unasked": 10}.get(consent, 1)


# --------------------------------------------------------------------------
# dream: tidy Claude's auto memory as the day ends
# --------------------------------------------------------------------------
#
# Claude Code keeps auto memory in ~/.claude/projects/<project>/memory/: a
# MEMORY.md index plus one file per topic. Only the first 200 lines (25 KB) of
# the index load each session, so a long or messy index silently loses facts.
# At `done` the ending session tidies it: plan (backup + facts), Claude edits,
# check (validate, or roll back). Hestia never writes a memory itself.

MEMORY_INDEX = "MEMORY.md"
MEMORY_LOAD_LINES = 200
MEMORY_LOAD_BYTES = 25 * 1024
LINK_PATTERN = re.compile(r"\]\(([^)\s]+\.md)\)")


def memory_root():
    if os.environ.get("HESTIA_MEMORY_ROOT"):
        return Path(os.environ["HESTIA_MEMORY_ROOT"])
    return (claude_config_dir() or Path.home() / ".claude") / "projects"


def memory_slug(path):
    """How Claude Code names a project folder: every non-alphanumeric becomes '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def memory_dirs_for(cwd):
    """The memory folder for a working directory, trying the folder and its git root."""
    candidates = [Path(cwd)]
    top = git_toplevel(Path(cwd)) if Path(cwd).is_dir() else None
    if top is not None:
        candidates.append(top)
    found = []
    for candidate in candidates:
        folder = memory_root() / memory_slug(candidate) / "memory"
        if folder.is_dir() and folder not in found:
            found.append(folder)
    return found


def auto_memory_off():
    """Why auto memory is off, or None. Honours Claude Code's own switches."""
    if os.environ.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY", "").strip() not in ("", "0", "false"):
        return "auto memory is turned off (CLAUDE_CODE_DISABLE_AUTO_MEMORY)"
    settings = read_json(claude_dir() / "settings.json") or {}
    if settings.get("autoMemoryEnabled") is False:
        return "auto memory is turned off in Claude Code's settings"
    if settings.get("autoMemoryDirectory"):
        return "auto memory lives in a custom folder, which Hestia leaves alone"
    if (read_json(profile_path()) or {}).get("dream") is False:
        return "memory tidying is turned off in /hestia:init"
    return None


def parse_memory_file(path):
    """Frontmatter of one memory file: {'name', 'description', 'type'} or None if broken."""
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None
    fields = {}
    for line in lines[1:end]:
        match = re.match(r"\s*([A-Za-z_]+)\s*:\s*(.*)$", line)
        if match:
            fields[match.group(1).lower()] = match.group(2).strip()
    if not fields.get("name") or not fields.get("description"):
        return None
    return {"name": fields["name"], "description": fields["description"], "type": fields.get("type", "")}


def _normal(text):
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def memory_findings(folder):
    """Facts about one memory folder. Deterministic; the tidying is Claude's."""
    index_path = folder / MEMORY_INDEX
    index_text = index_path.read_bytes().decode("utf-8", "replace") if index_path.exists() else ""
    linked = set(LINK_PATTERN.findall(index_text))
    files, broken = [], []
    for path in sorted(folder.glob("*.md")):
        if path.name == MEMORY_INDEX:
            continue
        meta = parse_memory_file(path)
        if meta is None:
            broken.append(path.name)
            continue
        files.append(dict(meta, file=path.name, lines=len(path.read_bytes().splitlines())))

    duplicates = []
    for i, first in enumerate(files):
        for second in files[i + 1:]:
            same_name = _normal(first["name"]) == _normal(second["name"])
            close = SequenceMatcher(None, _normal(first["description"]), _normal(second["description"])).ratio()
            if same_name or close >= 0.8:
                duplicates.append([first["file"], second["file"]])

    names = {f["file"] for f in files} | set(broken)
    index_lines = len(index_text.splitlines())
    return {
        "folder": str(folder),
        "files": files,
        "broken": broken,
        "missing_from_index": sorted(names - linked),
        "index_points_to_missing": sorted(link for link in linked if not (folder / link).exists()),
        "duplicates": duplicates,
        "index_lines": index_lines,
        "index_bytes": len(index_text.encode("utf-8")),
        "over_load_limit": index_lines > MEMORY_LOAD_LINES or len(index_text.encode("utf-8")) > MEMORY_LOAD_BYTES,
    }


def _memory_backup_root():
    return hestia_home() / "backups" / "memory"


def _copy_folder(source, target):
    if target.exists():
        shutil.rmtree(str(target))
    shutil.copytree(str(source), str(target))


def dream_plan(now=None):
    """Back up today's projects' memory and return the facts to tidy from."""
    now = now or datetime.now().astimezone()
    off = auto_memory_off()
    if off:
        return {"backup": None, "projects": [], "skipped": off}

    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    prompts = read_prompts(start_of_day)
    cwds = []
    for cwd in [p["cwd"] for p in prompts if p["cwd"]] + [os.getcwd()]:
        if cwd not in cwds:
            cwds.append(cwd)

    projects, seen = [], set()
    for cwd in cwds:
        for folder in memory_dirs_for(cwd):
            if str(folder) in seen or not any(p.name != MEMORY_INDEX for p in folder.glob("*.md")):
                continue
            seen.add(str(folder))
            facts = memory_findings(folder)
            facts["prompts_today"] = [
                p["text"][:200] for p in prompts if p["cwd"] and _same_path(p["cwd"], cwd)
            ][-15:]
            projects.append(facts)

    if not projects:
        return {"backup": None, "projects": [], "skipped": "no memory to tidy"}

    backup = now.strftime("%Y-%m-%d-%H%M%S")
    root = _memory_backup_root() / backup
    manifest = {}
    for facts in projects:
        folder = Path(facts["folder"])
        key = folder.parent.name
        _copy_folder(folder, root / key)
        manifest[key] = str(folder)
    write_json(root / "manifest.json", manifest)
    return {"backup": backup, "projects": projects, "skipped": None}


def _latest_backup():
    root = _memory_backup_root()
    found = sorted(p.name for p in root.iterdir() if (p / "manifest.json").exists()) if root.exists() else []
    return found[-1] if found else None


def dream_restore(backup=None):
    """Put every folder in a backup back exactly. Returns the folders restored."""
    backup = backup or _latest_backup()
    if not backup:
        return []
    root = _memory_backup_root() / backup
    manifest = read_json(root / "manifest.json") or {}
    restored = []
    for key, folder in manifest.items():
        _copy_folder(root / key, Path(folder))
        restored.append(folder)
    return restored


def dream_check(backup):
    """Validate the tidy against its backup. A folder that fails is rolled back."""
    root = _memory_backup_root() / backup
    manifest = read_json(root / "manifest.json")
    if manifest is None:
        raise ValueError("no memory backup called %s" % backup)
    results = []
    for key, folder in manifest.items():
        folder, before = Path(folder), root / key
        old = {p.name: p.read_bytes() for p in before.glob("*.md")}
        new = {p.name: p.read_bytes() for p in folder.glob("*.md")} if folder.is_dir() else {}
        facts = memory_findings(folder) if folder.is_dir() else None
        problems = []
        if facts is None:
            problems.append("the memory folder is gone")
        else:
            if facts["broken"]:
                problems.append("broken frontmatter: %s" % ", ".join(facts["broken"]))
            if facts["index_points_to_missing"]:
                problems.append("index links to missing files: %s" % ", ".join(facts["index_points_to_missing"]))
        added = sorted(set(new) - set(old))
        if added:
            problems.append("new files are not allowed (Hestia never adds memories): %s" % ", ".join(added))
        result = {
            "folder": str(folder),
            "removed": sorted(set(old) - set(new)),
            "edited": sorted(n for n in set(old) & set(new) if old[n] != new[n] and n != MEMORY_INDEX),
            "index_changed": old.get(MEMORY_INDEX) != new.get(MEMORY_INDEX),
            "problems": problems,
            "rolled_back": False,
        }
        if problems:
            _copy_folder(before, folder)
            result["rolled_back"] = True
        results.append(result)
    return results


def cmd_dream(args):
    try:
        if args.action == "plan":
            result = dream_plan()
        elif args.action == "check":
            backup = args.backup or _latest_backup()
            if not backup:
                print("no memory backup to check against", file=sys.stderr)
                return 1
            result = {"backup": backup, "projects": dream_check(backup)}
        else:
            result = {"restored": dream_restore(args.backup or None)}
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    if args.action == "plan":
        if result["skipped"]:
            print("Nothing to tidy: %s." % result["skipped"])
        else:
            print("Backed up %d memory folder(s) as %s." % (len(result["projects"]), result["backup"]))
    elif args.action == "check":
        for project in result["projects"]:
            if project["rolled_back"]:
                print("Rolled back %s: %s" % (project["folder"], "; ".join(project["problems"])))
            else:
                print("%s: %d removed, %d edited%s" % (project["folder"], len(project["removed"]),
                      len(project["edited"]), ", index updated" if project["index_changed"] else ""))
    else:
        print("Restored %d memory folder(s)." % len(result["restored"]) if result["restored"]
              else "No memory backup to restore.")
    return 0


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def _hook_decision(script, payload, env):
    proc = subprocess.run(
        [sys.executable, str(script), "hook"],
        input=json.dumps(payload).encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    out = proc.stdout.decode().strip()
    if not out:
        return "allow"
    try:
        data = json.loads(out)
    except ValueError:
        return "allow"
    return data.get("hookSpecificOutput", {}).get("permissionDecision", "allow")


def cmd_selftest(args):
    """Build a throwaway repo and prove the whole flow, end to end."""
    script = Path(__file__).resolve()
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print("%s %s%s" % ("PASS" if ok else "FAIL", name, (" - " + detail) if detail else ""))

    # A space and text no single code page holds, as real names and notes have.
    note = "selftest: Łukasz’s café ☕"

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        repo = tmp / "my repo"
        repo.mkdir()
        home = tmp / "home"
        logs = tmp / "logs"
        logs.mkdir()

        env = os.environ.copy()
        env["HESTIA_HOME"] = str(home)
        env["HESTIA_LOG_DIR"] = str(logs)
        # Tell the handover a person is at this session, so the selftest gives
        # the same answer inside an interactive window, a CI job or claude -p.
        env["HESTIA_SESSION_KIND"] = "interactive"
        env["HESTIA_CLAUDE_DIR"] = str(tmp / "claude")
        env["HESTIA_CLAUDE_JSON"] = str(tmp / "claude.json")
        env["HESTIA_MEMORY_ROOT"] = str(tmp / "memory-root")
        env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "hestia selftest"
        env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "selftest@hestia.local"

        def run(args_list, cwd=repo):
            return subprocess.run(
                [sys.executable, str(script)] + args_list,
                cwd=str(cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        def rungit(args_list):
            return subprocess.run(
                ["git"] + args_list,
                cwd=str(repo),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        rungit(["init", "-q"])
        (repo / "README.md").write_text("committed\n")
        rungit(["add", "."])
        rungit(["commit", "-q", "-m", "first"])

        # Unsaved work, of both kinds.
        (repo / "README.md").write_text("committed and then edited\n")
        (repo / "scratch.txt").write_text("never committed\n")

        head_before = rungit(["rev-parse", "HEAD"]).stdout.decode().strip()
        status_before = rungit(["status", "--porcelain"]).stdout.decode()
        index_before = hashlib.sha256((repo / ".git" / "index").read_bytes()).hexdigest()

        # --keep-agents: the selftest must never touch real processes.
        proc = run(["done", "--note", note, "--next", "rerun the selftest", "--keep-agents", "--json"])
        ok = proc.returncode == 0
        check("done runs", ok, proc.stderr.decode().strip()[:200])
        if not ok:
            return 1
        result = json.loads(proc.stdout.decode())

        check("snapshot saved the work", result["saved"] == 1)
        check("snapshot reported untouched", result["integrity_ok"])

        head_after = rungit(["rev-parse", "HEAD"]).stdout.decode().strip()
        status_after = rungit(["status", "--porcelain"]).stdout.decode()
        index_after = hashlib.sha256((repo / ".git" / "index").read_bytes()).hexdigest()
        check("branch unchanged", head_before == head_after)
        check("working tree unchanged", status_before == status_after)
        check("index byte-identical", index_before == index_after)

        ref = result["snapshots"][0]["ref"]
        show = rungit(["show", "%s:scratch.txt" % ref]).stdout.decode()
        check("uncommitted file is recoverable", show.strip() == "never committed")

        check("tomorrow's list written", Path(result["tomorrow"]).exists())

        # The pause blocks agent tool use...
        decision = _hook_decision(script, {"tool_name": "Bash", "tool_input": {"command": "npm test"}}, env)
        check("hook blocks tool use while paused", decision == "deny")

        decision = _hook_decision(script, {"tool_name": "Edit", "tool_input": {"file_path": "a.py"}}, env)
        check("hook blocks edits while paused", decision == "deny")

        # ...but never the way back in.
        decision = _hook_decision(
            script, {"tool_name": "Bash", "tool_input": {"command": "python3 hestia.py resume"}}, env
        )
        check("hook always allows back", decision == "allow")

        decision = _hook_decision(
            script, {"tool_name": "PowerShell", "tool_input": {"command": 'python "C:\\hestia\\hestia.py" back'}}, env
        )
        check("hook allows back from PowerShell", decision == "allow")

        decision = _hook_decision(
            script, {"tool_name": "Bash", "tool_input": {"command": 'sh "${CLAUDE_PLUGIN_ROOT}/scripts/hestia" back'}}, env
        )
        check("hook allows back through the launcher", decision == "allow")

        # Only the whole command gets through: nothing can ride along with it.
        decision = _hook_decision(
            script, {"tool_name": "Bash", "tool_input": {"command": "python3 hestia.py back && npm test"}}, env
        )
        check("hook blocks work chained after back", decision == "deny")

        # Connectors and other plugins' tools are paused like any other tool.
        decision = _hook_decision(
            script, {"tool_name": "mcp__claude_ai_Slack__slack_send_message", "tool_input": {"text": "hi"}}, env
        )
        check("hook blocks connector tools while paused", decision == "deny")

        # The morning handover: not while paused, once afterwards, then silent.
        def handover(event="session-start", kind="interactive"):
            got = subprocess.run(
                [sys.executable, str(script), event],
                input=b'{"session_id": "selftest", "cwd": "/selftest"}',
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=dict(env, HESTIA_SESSION_KIND=kind),
            ).stdout.decode().strip()
            if not got:
                return ""
            return json.loads(got)["hookSpecificOutput"]["additionalContext"]

        check("no handover while still paused", handover() == "")

        # Morning: the pause runs out on its own.
        pause_file = home / "pause.json"
        overnight = json.loads(pause_file.read_text())
        overnight["until"] = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat()
        pause_file.write_text(json.dumps(overnight))

        check("scripted runs never take the list", handover(kind="headless") == "")

        first = handover("prompt-submit")
        check("a window left open gets it on its next prompt", "Where you left off" in first)
        check("handover carries the note", "selftest" in first)
        check("non-ASCII text survives the night", note in first)
        check("handover leads with where to start", "Start here" in first and "rerun the selftest" in first)
        check("handover says what ran overnight", "Overnight: nothing ran" in first)
        check("handover happens only once", handover() == "" and handover("prompt-submit") == "")

        # Eight sessions starting in the same instant: exactly one gets it.
        run(["handover", "--again"])
        racers = [
            subprocess.Popen(
                [sys.executable, str(script), "session-start" if i % 2 else "prompt-submit"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            )
            for i in range(8)
        ]
        outputs = [racer.communicate()[0].decode().strip() for racer in racers]
        check("sessions starting together get it once", len([o for o in outputs if o]) == 1)

        run(["handover", "--again"])
        check("morning gets yesterday's list", "Where you left off" in handover())

        proc = run(["back"])
        check("back runs", proc.returncode == 0)

        check("old names still work as aliases", run(["status"]).returncode == 0
              and run(["today"]).returncode == 0)

        decision = _hook_decision(script, {"tool_name": "Bash", "tool_input": {"command": "npm test"}}, env)
        check("hook is silent after back", decision == "allow")

        # An expired pause lets you back in on its own.
        past = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat()
        write_target = home / "pause.json"
        write_target.parent.mkdir(parents=True, exist_ok=True)
        write_target.write_text(json.dumps({"until": past}))
        decision = _hook_decision(script, {"tool_name": "Bash", "tool_input": {"command": "npm test"}}, env)
        check("expired pause clears itself", decision == "allow")

        # A midday break: the same pause, kind=break, no list, lifts itself.
        list_before = (home / "TOMORROW.md").read_bytes()
        handover_before = (home / "handover.json").read_bytes()
        proc = run(["break", "30m", "--json"])
        ok = proc.returncode == 0
        check("break runs", ok, proc.stderr.decode().strip()[:200])
        if ok:
            result = json.loads(proc.stdout.decode())
            check("break saves where you are", result["saved"] == 1 and result["integrity_ok"])
        proc = subprocess.run(
            [sys.executable, str(script), "hook"],
            input=json.dumps({"tool_name": "Edit", "tool_input": {}}).encode(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        check("hook pauses agents during a break", "On a break until" in proc.stdout.decode())
        check("break writes no list",
              (home / "TOMORROW.md").read_bytes() == list_before
              and (home / "handover.json").read_bytes() == handover_before)
        back = run(["back"]).stdout.decode()
        check("back ends a break early", "On a break since" in back and "Before the break" in back)

        # The restore line is valid shell, even with a space in the path.
        import shlex

        restore = shlex.split(back.partition("Before the break: ")[2].strip())
        rungit(["checkout", "--", "."])
        (repo / "scratch.txt").unlink()
        if restore:
            subprocess.run(restore, cwd=str(tmp), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        check("restore line brings the work back",
              (repo / "scratch.txt").exists()
              and (repo / "README.md").read_text(encoding="utf-8") == "committed and then edited\n")
        write_target.write_text(json.dumps({"kind": "break", "until": past}))
        decision = _hook_decision(script, {"tool_name": "Bash", "tool_input": {"command": "npm test"}}, env)
        check("break lifts itself", decision == "allow")

        # why: the facts come from real commits, never from Hestia's own snapshots.
        proc = run(["why", "--json"])
        facts = json.loads(proc.stdout.decode()) if proc.returncode == 0 else {}
        subjects = [c["subject"] for r in facts.get("repos", []) for c in r["commits"]]
        check("why reads today's commits", subjects == ["first"])

        # new: offline-safe, reads a local changelog, shows only what changed.
        changelog = tmp / "CHANGELOG.md"
        changelog.write_text("# Changelog\n\n## 9.2.0\n\n- b\n\n## 9.1.0\n\n- a\n\n## 9.0.0\n\n- old\n")
        env["HESTIA_CHANGELOG_URL"] = changelog.resolve().as_uri()
        facts = json.loads(run(["new", "--since", "9.0.0", "--json"]).stdout.decode())
        check("new shows only what changed",
              [r["version"] for r in facts.get("releases", [])] == ["9.2.0", "9.1.0"])
        facts = json.loads(run(["new", "--since", "9.2.0", "--json"]).stdout.decode())
        check("new is quiet when nothing changed", facts.get("ok") and facts.get("releases") == [])
        env["HESTIA_CHANGELOG_URL"] = (tmp / "missing.md").resolve().as_uri()
        proc = run(["new"])
        check("new degrades cleanly offline",
              proc.returncode == 0 and "nothing to do" in proc.stdout.decode())
        # init: writes the shared context, and leaves the user's own CLAUDE.md alone.
        claude_md = tmp / "claude" / "CLAUDE.md"
        claude_md.parent.mkdir()
        original = b"# My notes\r\nKeep these exactly.\r\n"
        claude_md.write_bytes(original)
        answers = {"why": "making things people use", "tone": "plain", "screen": False,
                   "connectors": {"Google_Calendar": "allowed"}}
        proc = run(["init", "write", "--seen", "Google_Calendar", "--answers", json.dumps(answers)])
        written = all((home / name).exists() for name in ("profile.json", "config.json", "connectors.json"))
        check("init writes profile, config and connectors", proc.returncode == 0 and written,
              proc.stderr.decode().strip()[:200])
        text = claude_md.read_bytes()
        check("init keeps CLAUDE.md outside its markers", text.startswith(original)
              and SECTION_START.encode() in text and b"making things people use" in text)
        proc = run(["dashboard", "--no-open"])  # never a real browser, even if this breaks
        check("no dashboard on a computer with no screen",
              proc.returncode == 0 and "/hestia:today" in proc.stdout.decode()
              and not (home / "dashboard.html").exists())
        run(["init", "remove"])
        check("init remove restores CLAUDE.md exactly", claude_md.read_bytes() == original)

        # dream: backs up the repo's auto memory, rolls back a bad tidy, restores exactly.
        # Name the folder from git's view of the repo, as Hestia looks it up
        # (macOS temp paths sit behind a /var -> /private/var symlink).
        top = rungit(["rev-parse", "--show-toplevel"]).stdout.decode().strip()
        memory = tmp / "memory-root" / memory_slug(Path(top)) / "memory"
        memory.mkdir(parents=True)
        (memory / "MEMORY.md").write_bytes(b"- [Tabs](tabs.md) - indent with tabs\n")
        (memory / "tabs.md").write_bytes(b"---\nname: tabs\ndescription: indent with tabs\n---\nUse tabs.\n")
        before = {p.name: p.read_bytes() for p in memory.iterdir()}
        env.pop("CLAUDE_CODE_DISABLE_AUTO_MEMORY", None)
        proc = run(["dream", "plan", "--json"])
        plan = json.loads(proc.stdout.decode() or "{}")
        check("dream plan backs up today's memory", proc.returncode == 0 and bool(plan.get("backup"))
              and len(plan.get("projects") or []) == 1, proc.stderr.decode().strip()[:200])
        (memory / "tabs.md").write_bytes(b"no frontmatter any more\n")
        (memory / "invented.md").write_bytes(b"---\nname: x\ndescription: y\n---\n")
        proc = run(["dream", "check", "--json", "--backup", plan.get("backup") or "none"])
        rolled = (json.loads(proc.stdout.decode() or "{}").get("projects") or [{}])[0].get("rolled_back")
        after = {p.name: p.read_bytes() for p in memory.iterdir()}
        check("dream check rolls back a broken tidy", bool(rolled) and after == before)
        (memory / "tabs.md").write_bytes(b"---\nname: tabs\ndescription: edited\n---\n")
        run(["dream", "restore"])
        check("dream restore puts memory back exactly",
              {p.name: p.read_bytes() for p in memory.iterdir()} == before)

    failed = [name for name, ok, _ in checks if not ok]
    print("")
    print("%d/%d checks passed" % (len(checks) - len(failed), len(checks)))
    return 1 if failed else 0


# --------------------------------------------------------------------------

def main(argv=None):
    # Claude Code reads hooks and commands as UTF-8 on every OS. Left alone,
    # Windows uses its ANSI code page here and crashes on names like Łukasz.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="hestia", description="End the working day in Claude Code.")
    sub = parser.add_subparsers(dest="command")

    # The old names stay as aliases, so anything already wired up keeps working.
    p = sub.add_parser("done", aliases=["shutdown"], help="save open work, write tomorrow's list, pause agents")
    p.add_argument("--note", default="", help="one line about what today was for")
    p.add_argument("--next", default="", help="the first thing to do next time, for the top of the list")
    p.add_argument("--until", default="", help="HH:MM or ISO timestamp (default: next working morning)")
    p.add_argument("--keep-agents", action="store_true", help="report running agents but don't stop them")
    p.add_argument("--stop-all", action="store_true", help="also stop interactive Claude sessions")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_shutdown)

    p = sub.add_parser("break", help="midday pause: save where you are, pause agents, lift itself")
    p.add_argument("length", nargs="?", default="", help="45m, 1h30m, 90 or 13:50 (default 45m)")
    p.add_argument("--stop-agents", action="store_true", help="also SIGTERM unattended agents")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_break)

    p = sub.add_parser("back", aliases=["resume"], help="lift the pause or break early")
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("today", aliases=["status"], help="show the pause and today's load")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("why", help="the facts behind what today's work was for")
    p.add_argument("--days", type=int, default=1, help="look back this many days (default: today)")
    p.add_argument("--repo", action="append", default=[], help="also include this repo")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_why)

    p = sub.add_parser("new", help="what changed in Claude Code since your version or last check")
    p.add_argument("--since", default="", help="compare against this version instead")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_new)
    p = sub.add_parser("init", help="get to know the user once: profile, CLAUDE.md section, connectors")
    p.add_argument("action", nargs="?", default="detect", choices=["detect", "write", "remove"])
    p.add_argument("--seen", default="", help="MCP server names Claude can see, comma separated")
    p.add_argument("--answers", default="", help="write: the user's answers as a JSON object, or - for stdin")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("dream", help="tidy Claude's auto memory at done: plan (backup + facts), check, restore")
    p.add_argument("action", choices=["plan", "check", "restore"])
    p.add_argument("--backup", default="", help="check/restore: which backup (default: the latest)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_dream)

    p = sub.add_parser("connector", help="may Hestia use this connector? exit 0 yes, 10 ask once, 1 no")
    p.add_argument("name", help="connector key or MCP server name")
    p.add_argument("--present", action="store_true", help="it exists right now, so record it if new")
    p.add_argument("--set", choices=["allowed", "declined"], help="record the user's answer")
    p.set_defaults(func=cmd_connector)

    p = sub.add_parser("dashboard", help="write today's reading as one static page and open it (off with no screen)")
    p.add_argument("--out", default="", help="where to write it (default: ~/.hestia/dashboard.html)")
    p.add_argument("--no-open", action="store_true", help="write the page but don't open a browser")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("hook", help="PreToolUse hook (reads JSON on stdin)")
    p.set_defaults(func=cmd_hook)

    p = sub.add_parser("session-start", help="SessionStart hook: hand over yesterday's list, once")
    p.set_defaults(func=cmd_session_start)

    p = sub.add_parser("prompt-submit", help="UserPromptSubmit hook: the handover for a window left open")
    p.set_defaults(func=cmd_prompt_submit)

    p = sub.add_parser("handover", help="is the morning list still due? --again re-arms it")
    p.add_argument("--again", action="store_true", help="hand the list over once more")
    p.set_defaults(func=cmd_handover)

    p = sub.add_parser("selftest", help="prove the whole flow on a throwaway repo")
    p.set_defaults(func=cmd_selftest)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
