#!/usr/bin/env python3
"""hestia - end the working day in Claude Code.

Deterministic engine and standalone CLI. Standard library only, Python 3.8+.

    hestia.py done  [--note TEXT] [--until WHEN] [--json]   (alias: shutdown)
    hestia.py break [45m | 1h30m | 13:50] [--json]          midday pause, lifts itself
    hestia.py back                                          (alias: resume)
    hestia.py today [--json]                                (alias: status)
    hestia.py why   [--days N] [--repo PATH] [--json]       facts for "what was this for"
    hestia.py new   [--since VERSION] [--json]              what changed in Claude Code
    hestia.py dashboard [--out PATH] [--no-open]            # today, as one static page
    hestia.py hook            # PreToolUse hook, reads JSON on stdin
    hestia.py session-start   # SessionStart hook, hands over yesterday's list
    hestia.py selftest        # proves the whole flow on a throwaway repo

Environment overrides (used by the tests and selftest):
    HESTIA_HOME      state directory, default ~/.hestia
    HESTIA_LOG_DIR   Claude Code project logs, default ~/.claude/projects
    HESTIA_CHANGELOG_URL  where `new` reads the changelog (file:// works)
"""

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
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

# Bash commands matching this are always allowed while paused, otherwise the
# pause would block the only command that lifts it.
RESUME_PATTERN = re.compile(
    r"hestia(?:\.py)?['\"]?\s+(back|resume|today|status|dashboard|hook|session-start|selftest)\b"
)


# --------------------------------------------------------------------------
# paths and config
# --------------------------------------------------------------------------

def hestia_home():
    return Path(os.environ.get("HESTIA_HOME") or (Path.home() / ".hestia"))


def log_dir():
    return Path(os.environ.get("HESTIA_LOG_DIR") or (Path.home() / ".claude" / "projects"))


def load_config():
    cfg = {
        "resume_hour": DEFAULT_RESUME_HOUR,
        "working_days": list(DEFAULT_WORKING_DAYS),
        "repos": [],
    }
    path = hestia_home() / "config.json"
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text()))
        except (ValueError, OSError):
            pass
    return cfg


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (ValueError, OSError):
        return default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


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


# --------------------------------------------------------------------------
# running agents
# --------------------------------------------------------------------------

# A headless run has no one watching it, so it is the kind that quietly keeps
# going all night. An interactive session has a person at it.
HEADLESS_MARKERS = ("--print", "--output-format", "-p ")


def _ps_table():
    proc = subprocess.run(
        ["ps", "-eo", "pid=,ppid=,command="],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
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

def write_tomorrow(now, note, snapshots, load, prompts, stopped=None, still_open=None):
    stopped = stopped or []
    still_open = still_open or []
    lines = ["# Tomorrow - %s" % now.strftime("%a %d %b"), ""]
    if note:
        lines += ["> %s" % note.strip(), ""]

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
            lines.append(
                "  `git -C %s checkout %s -- .`" % (snap["repo"], snap["ref"])
            )
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

    recent = [p["text"] for p in prompts[-5:]]
    if recent:
        lines += ["## The last things you asked for", ""]
        for text in recent:
            single = " ".join(text.split())
            if len(single) > 110:
                single = single[:107] + "..."
            lines.append("- %s" % single)
        lines.append("")

    path = hestia_home() / "TOMORROW.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
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
        now, args.note, snapshots, load, prompts, stopped, still_open
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
    # Re-arm the morning handover for this list.
    write_json(
        hestia_home() / "handover.json",
        {"for": tomorrow.stat().st_mtime, "consumed": False},
    )

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

    if pause:
        print("Back on. Paused since %s." % human(since))
    else:
        print("Not paused.")
    tomorrow = hestia_home() / "TOMORROW.md"
    if tomorrow.exists():
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
                    "restore": "git -C %s checkout %s -- ." % (s["repo"], s["ref"]),
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
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; background: #1C1714; color: #A89E97;
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 40px 24px 56px; }
h1, h2 { font-family: Georgia, "Times New Roman", serif; color: #F6EDE3; font-weight: normal; }
h1 { font-size: 30px; margin: 0 0 4px; }
h2 { font-size: 19px; margin: 0 0 14px; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }
code { color: #F4B740; }
.lede { margin: 0 0 28px; }
.tile { background: #2A211C; border: 1px solid #3A2E27; border-radius: 14px; padding: 20px 22px; margin-bottom: 18px; }
.status { font-family: Georgia, serif; font-size: 22px; color: #F6EDE3; margin: 0; }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 18px; margin: 0; }
.facts dt { font-size: 13px; }
.facts dd { margin: 2px 0 0; font-size: 22px; color: #F6EDE3; font-variant-numeric: tabular-nums; }
.facts dd small { font-size: 13px; color: #A89E97; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
@media (max-width: 720px) { .grid { grid-template-columns: 1fr; } }
.grid .tile { margin: 0; }
ul.plain { list-style: none; margin: 0; padding: 0; }
ul.plain li { padding: 7px 0; border-top: 1px solid #3A2E27; display: flex; justify-content: space-between; gap: 12px; }
ul.plain li:first-child { border-top: 0; }
ul.plain .n { color: #F6EDE3; font-variant-numeric: tabular-nums; }
.snap { display: block; margin-top: 2px; word-break: break-all; }
pre { white-space: pre-wrap; margin: 0; color: #A89E97; }
.empty { margin: 0; }
svg text { fill: #A89E97; font: 12px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
.tick { stroke: #E8743B; stroke-width: 2; stroke-linecap: round; }
.hit:hover + .tick, .hit:focus + .tick { stroke: #F4B740; stroke-width: 3; }
.hit { fill: transparent; cursor: default; outline: none; }
.axis { stroke: #3A2E27; stroke-width: 1; }
.gap { stroke: #6B615B; stroke-width: 1; }
details { margin-top: 14px; }
summary { cursor: pointer; font-size: 13px; }
table { border-collapse: collapse; margin-top: 10px; font-size: 13px; width: 100%; }
th, td { text-align: left; padding: 5px 10px 5px 0; border-top: 1px solid #3A2E27; }
th { color: #F6EDE3; font-weight: normal; }
footer { margin-top: 28px; font-size: 13px; color: #6B615B; }
"""


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
    One series, so no legend: the heading names it.
    """
    width, height = 960, 104
    left, right, base = 16, 944, 74
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
        '<line class="axis" x1="%d" y1="%d" x2="%d" y2="%d"/>' % (left, base, right, base),
    ]

    hours = int(span // 3600)
    step = 1 if hours <= 12 else 2
    for i in range(0, hours + 1, step):
        when = start + timedelta(hours=i)
        parts.append(
            '<text x="%.1f" y="%d" text-anchor="middle">%s</text>'
            % (min(max(x(when), left + 14), right - 14), base + 22, when.strftime("%H:%M"))
        )

    for earlier, later in zip(prompts, prompts[1:]):
        minutes = int((later["at"] - earlier["at"]).total_seconds() / 60)
        if minutes < BREAK_MINUTES:
            continue
        x1, x2 = x(earlier["at"]) + 4, x(later["at"]) - 4
        if x2 - x1 < 8:
            continue
        parts.append(
            '<path class="gap" d="M%.1f 18v5H%.1fv-5" fill="none"/>' % (x1, x2)
        )
        if x2 - x1 >= 48:
            parts.append(
                '<text x="%.1f" y="13" text-anchor="middle">%d min</text>'
                % ((x1 + x2) / 2, minutes)
            )

    for prompt in prompts:
        px = x(prompt["at"])
        label = "%s · %s" % (prompt["at"].strftime("%H:%M"), Path(prompt["cwd"]).name or "no project")
        parts.append(
            '<rect class="hit" x="%.1f" y="26" width="10" height="%d" tabindex="0">'
            '<title>%s</title></rect>' % (px - 5, base - 26, _esc(label))
        )
        parts.append('<line class="tick" x1="%.1f" y1="34" x2="%.1f" y2="%d"/>' % (px, px, base - 2))

    parts.append("</svg>")
    return "\n".join(parts)


def render_dashboard(now, pause, prompts, load, snapshots, tomorrow_text):
    """One self-contained HTML page. No scripts, no network, nothing that refreshes."""
    if pause:
        status = _esc(pause_line(pause))
        if pause.get("note"):
            status += " <small>%s</small>" % _esc(pause["note"])
    else:
        status = "Working."

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
            '<section class="tile"><h2>When you were working</h2>%s'
            "<details><summary>As a table</summary>"
            "<table><thead><tr><th>Time</th><th>Project</th></tr></thead><tbody>%s</tbody></table>"
            "</details></section>"
        ) % (_timeline_svg(prompts, now, bool(pause)), rows)

        counts = {}
        for p in prompts:
            if p["cwd"]:
                counts[p["cwd"]] = counts.get(p["cwd"], 0) + 1
        projects = "".join(
            '<li><span title="%s">%s</span><span class="n">%d</span></li>'
            % (_esc(cwd), _esc(Path(cwd).name), n)
            for cwd, n in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ) or "<li>none recorded</li>"
    else:
        facts = '<p class="empty">No prompts recorded today.</p>'
        timeline = ""
        projects = "<li>none recorded</li>"

    if snapshots:
        snaps = "".join(
            "<li><div>%s <small>%s</small>"
            '<code class="snap">git -C %s checkout %s -- .</code></div></li>'
            % (
                _esc(Path(s["repo"]).name),
                _esc(human(s["at"]) if s["at"] else ""),
                _esc(s["repo"]), _esc(s["ref"]),
            )
            for s in snapshots
        )
    else:
        snaps = "<li>no snapshots yet</li>"

    tomorrow = (
        "<pre>%s</pre>" % _esc(tomorrow_text.strip())
        if tomorrow_text and tomorrow_text.strip()
        else '<p class="empty">No list yet. <code>/hestia:done</code> writes one.</p>'
    )

    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hestia · %(date)s</title>
<style>%(css)s</style></head>
<body><main>
<h1>Hestia</h1>
<p class="lede">%(date)s. The same reading as <code>/hestia:today</code>, on one page.</p>
<section class="tile"><p class="status">%(status)s</p></section>
<section class="tile"><h2>Today</h2>%(facts)s</section>
%(timeline)s
<div class="grid">
<section class="tile"><h2>Projects</h2><ul class="plain">%(projects)s</ul></section>
<section class="tile"><h2>Saved work</h2><ul class="plain">%(snaps)s</ul></section>
</div>
<section class="tile" style="margin-top:18px"><h2>Tomorrow's list</h2>%(tomorrow)s</section>
<footer>Read at %(time)s. This page doesn't refresh: run <code>hestia.py dashboard</code> again for a new reading.</footer>
</main></body></html>
""" % {
        "css": DASHBOARD_CSS,
        "date": now.strftime("%A %d %B"),
        "time": now.strftime("%H:%M"),
        "status": status,
        "facts": facts,
        "timeline": timeline,
        "projects": projects,
        "snaps": snaps,
        "tomorrow": tomorrow,
    }


def cmd_dashboard(args):
    now = datetime.now().astimezone()
    cfg = load_config()
    pause = read_pause(now)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    prompts = read_prompts(start_of_day)
    load = day_load(prompts)
    snapshots = list_snapshots(discover_repos(prompts, cfg))

    tomorrow_path = hestia_home() / "TOMORROW.md"
    tomorrow_text = tomorrow_path.read_text() if tomorrow_path.exists() else ""

    out = Path(args.out) if args.out else hestia_home() / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_dashboard(now, pause, prompts, load, snapshots, tomorrow_text), encoding="utf-8")
    print("Dashboard: %s" % out)

    if not args.no_open:
        import webbrowser

        webbrowser.open(out.resolve().as_uri())
    return 0


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
    if tool == "Bash":
        command = str(tool_input.get("command") or "")
        if RESUME_PATTERN.search(command):
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


def cmd_session_start(args):
    """SessionStart hook: hand yesterday's list to the new day, once.

    This is the other half of the mechanism. Stopping is only possible if
    picking back up is guaranteed, so the context you left with is given back
    automatically - but exactly once, or it becomes noise.
    """
    tomorrow = hestia_home() / "TOMORROW.md"
    if not tomorrow.exists():
        return 0
    if read_pause():
        return 0  # still clocked out; the list is not due yet

    handover_path = hestia_home() / "handover.json"
    state = read_json(handover_path) or {}
    stamp = tomorrow.stat().st_mtime
    if state.get("consumed") and state.get("for") == stamp:
        return 0  # already handed over for this list

    text = tomorrow.read_text().strip()
    if not text:
        return 0

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": (
                        "Where the last working day was left off, from Hestia. "
                        "Use it to pick up the thread; do not act on it unless asked.\n\n"
                        + text
                    ),
                }
            }
        )
    )
    write_json(handover_path, {"for": stamp, "consumed": True, "at": datetime.now().astimezone().isoformat()})
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
        for line in tomorrow.read_text().splitlines():
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

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        repo = tmp / "repo"
        repo.mkdir()
        home = tmp / "home"
        logs = tmp / "logs"
        logs.mkdir()

        env = os.environ.copy()
        env["HESTIA_HOME"] = str(home)
        env["HESTIA_LOG_DIR"] = str(logs)
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
        proc = run(["done", "--note", "selftest", "--keep-agents", "--json"])
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

        # The morning handover: not while paused, once afterwards, then silent.
        def handover():
            got = subprocess.run(
                [sys.executable, str(script), "session-start"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            ).stdout.decode().strip()
            if not got:
                return ""
            return json.loads(got)["hookSpecificOutput"]["additionalContext"]

        check("no handover while still paused", handover() == "")

        proc = run(["back"])
        check("back runs", proc.returncode == 0)

        check("old names still work as aliases", run(["status"]).returncode == 0
              and run(["today"]).returncode == 0)

        first = handover()
        check("morning gets yesterday's list", "Where you left off" in first)
        check("handover carries the note", "selftest" in first)
        check("handover happens only once", handover() == "")

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

    failed = [name for name, ok, _ in checks if not ok]
    print("")
    print("%d/%d checks passed" % (len(checks) - len(failed), len(checks)))
    return 1 if failed else 0


# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(prog="hestia", description="End the working day in Claude Code.")
    sub = parser.add_subparsers(dest="command")

    # The old names stay as aliases, so anything already wired up keeps working.
    p = sub.add_parser("done", aliases=["shutdown"], help="save open work, write tomorrow's list, pause agents")
    p.add_argument("--note", default="", help="one line about what today was for")
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
    p = sub.add_parser("dashboard", help="write today's reading as one static page and open it")
    p.add_argument("--out", default="", help="where to write it (default: ~/.hestia/dashboard.html)")
    p.add_argument("--no-open", action="store_true", help="write the page but don't open a browser")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("hook", help="PreToolUse hook (reads JSON on stdin)")
    p.set_defaults(func=cmd_hook)

    p = sub.add_parser("session-start", help="SessionStart hook: hand over yesterday's list, once")
    p.set_defaults(func=cmd_session_start)

    p = sub.add_parser("selftest", help="prove the whole flow on a throwaway repo")
    p.set_defaults(func=cmd_selftest)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
