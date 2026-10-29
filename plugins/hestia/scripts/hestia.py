#!/usr/bin/env python3
"""hestia - end the working day in Claude Code.

Deterministic engine and standalone CLI. Standard library only, Python 3.8+.

    hestia.py done  [--note TEXT] [--until WHEN] [--json]   (alias: shutdown)
    hestia.py back                                          (alias: resume)
    hestia.py today [--json]                                (alias: status)
    hestia.py init  [detect|write|remove] [--seen NAMES] [--answers JSON]
    hestia.py connector NAME [--present] [--set allowed|declined]
    hestia.py hook            # PreToolUse hook, reads JSON on stdin
    hestia.py session-start   # SessionStart hook, hands over yesterday's list
    hestia.py selftest        # proves the whole flow on a throwaway repo

Environment overrides (used by the tests and selftest):
    HESTIA_HOME         state directory, default ~/.hestia
    HESTIA_LOG_DIR      Claude Code project logs, default ~/.claude/projects
    HESTIA_CLAUDE_DIR   where the user's CLAUDE.md lives, default ~/.claude
    HESTIA_CLAUDE_JSON  Claude Code's config, default ~/.claude.json
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

BREAK_MINUTES = 20
DEFAULT_RESUME_HOUR = 8
DEFAULT_WORKING_DAYS = [0, 1, 2, 3, 4]  # Monday-Friday

# Bash commands matching this are always allowed while paused, otherwise the
# pause would block the only command that lifts it.
RESUME_PATTERN = re.compile(
    r"hestia(?:\.py)?['\"]?\s+(back|resume|today|status|hook|session-start|selftest)\b"
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
    return data


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
    if pause:
        print("Back on. Paused since %s." % human(parse_ts(pause.get("since")) or datetime.now().astimezone()))
    else:
        print("Not paused.")
    tomorrow = hestia_home() / "TOMORROW.md"
    if tomorrow.exists():
        print("Yesterday's list: %s" % tomorrow)
    return 0


def cmd_status(args):
    now = datetime.now().astimezone()
    pause = read_pause(now)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    prompts = read_prompts(start_of_day)
    load = day_load(prompts)

    result = {
        "paused": bool(pause),
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
        print("Clocked out until %s." % human(pause["_until"]))
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

    reason = "Clocked out until %s. Run /hestia:back if tonight can't wait." % human(
        pause["_until"]
    )
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
    return Path(os.environ.get("HESTIA_CLAUDE_DIR") or (Path.home() / ".claude"))


def claude_json_path():
    return Path(os.environ.get("HESTIA_CLAUDE_JSON") or (Path.home() / ".claude.json"))


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
    profile.update({k: answers[k] for k in ("name", "why", "helps", "tone") if k in answers})
    profile.setdefault("name", detected["name"])
    profile.setdefault("tone", "plain")
    profile["github_login"] = detected["github_login"]
    profile["hours"] = detected["hours"]
    profile["updated_at"] = now.isoformat()
    write_json(profile_path(), profile)

    config_file = hestia_home() / "config.json"
    raw_config = read_json(config_file) or {}
    for field in ("resume_hour", "working_days"):
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
        env["HESTIA_CLAUDE_DIR"] = str(tmp / "claude")
        env["HESTIA_CLAUDE_JSON"] = str(tmp / "claude.json")
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

        # init: writes the shared context, and leaves the user's own CLAUDE.md alone.
        claude_md = tmp / "claude" / "CLAUDE.md"
        claude_md.parent.mkdir()
        original = b"# My notes\r\nKeep these exactly.\r\n"
        claude_md.write_bytes(original)
        answers = {"why": "making things people use", "tone": "plain", "connectors": {"Google_Calendar": "allowed"}}
        proc = run(["init", "write", "--seen", "Google_Calendar", "--answers", json.dumps(answers)])
        written = all((home / name).exists() for name in ("profile.json", "config.json", "connectors.json"))
        check("init writes profile, config and connectors", proc.returncode == 0 and written,
              proc.stderr.decode().strip()[:200])
        text = claude_md.read_bytes()
        check("init keeps CLAUDE.md outside its markers", text.startswith(original)
              and SECTION_START.encode() in text and b"making things people use" in text)
        run(["init", "remove"])
        check("init remove restores CLAUDE.md exactly", claude_md.read_bytes() == original)

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

    p = sub.add_parser("back", aliases=["resume"], help="lift the pause early")
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("today", aliases=["status"], help="show the pause and today's load")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("init", help="get to know the user once: profile, CLAUDE.md section, connectors")
    p.add_argument("action", nargs="?", default="detect", choices=["detect", "write", "remove"])
    p.add_argument("--seen", default="", help="MCP server names Claude can see, comma separated")
    p.add_argument("--answers", default="", help="write: the user's answers as a JSON object, or - for stdin")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("connector", help="may Hestia use this connector? exit 0 yes, 10 ask once, 1 no")
    p.add_argument("name", help="connector key or MCP server name")
    p.add_argument("--present", action="store_true", help="it exists right now, so record it if new")
    p.add_argument("--set", choices=["allowed", "declined"], help="record the user's answer")
    p.set_defaults(func=cmd_connector)

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
