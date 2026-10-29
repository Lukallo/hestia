#!/usr/bin/env python3
"""hestia - end the working day in Claude Code.

Deterministic engine and standalone CLI. Standard library only, Python 3.8+.

    hestia.py shutdown [--note TEXT] [--until WHEN] [--json]
    hestia.py resume
    hestia.py status [--json]
    hestia.py hook            # PreToolUse hook, reads JSON on stdin
    hestia.py selftest        # proves the whole flow on a throwaway repo

Environment overrides (used by the tests and selftest):
    HESTIA_HOME      state directory, default ~/.hestia
    HESTIA_LOG_DIR   Claude Code project logs, default ~/.claude/projects
"""

import argparse
import hashlib
import json
import os
import re
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
RESUME_PATTERN = re.compile(r"hestia(?:\.py)?['\"]?\s+(resume|status|hook|selftest)\b")


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

def write_tomorrow(now, note, snapshots, load, prompts):
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

    tomorrow = write_tomorrow(now, args.note, snapshots, load, prompts)

    write_json(
        pause_path(),
        {
            "until": until.isoformat(),
            "since": now.isoformat(),
            "note": args.note or "",
            "tomorrow": str(tomorrow),
        },
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

    reason = "Clocked out until %s. Run /hestia:resume if tonight can't wait." % human(
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

        proc = run(["shutdown", "--note", "selftest", "--json"])
        ok = proc.returncode == 0
        check("shutdown runs", ok, proc.stderr.decode().strip()[:200])
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
        check("hook always allows resume", decision == "allow")

        proc = run(["resume"])
        check("resume runs", proc.returncode == 0)

        decision = _hook_decision(script, {"tool_name": "Bash", "tool_input": {"command": "npm test"}}, env)
        check("hook is silent after resume", decision == "allow")

        # An expired pause lets you back in on its own.
        past = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat()
        write_target = home / "pause.json"
        write_target.parent.mkdir(parents=True, exist_ok=True)
        write_target.write_text(json.dumps({"until": past}))
        decision = _hook_decision(script, {"tool_name": "Bash", "tool_input": {"command": "npm test"}}, env)
        check("expired pause clears itself", decision == "allow")

    failed = [name for name, ok, _ in checks if not ok]
    print("")
    print("%d/%d checks passed" % (len(checks) - len(failed), len(checks)))
    return 1 if failed else 0


# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(prog="hestia", description="End the working day in Claude Code.")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("shutdown", help="save open work, write tomorrow's list, pause agents")
    p.add_argument("--note", default="", help="one line about what today was for")
    p.add_argument("--until", default="", help="HH:MM or ISO timestamp (default: next working morning)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_shutdown)

    p = sub.add_parser("resume", help="lift the pause early")
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("status", help="show the pause and today's load")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("hook", help="PreToolUse hook (reads JSON on stdin)")
    p.set_defaults(func=cmd_hook)

    p = sub.add_parser("selftest", help="prove the whole flow on a throwaway repo")
    p.set_defaults(func=cmd_selftest)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
