#!/usr/bin/env python3
"""Bridge Antigravity lifecycle hooks to a local Noctalia status widget.

Only lifecycle metadata is retained, never prompts, transcripts or tool arguments.
Hook output never changes the agent's permission or execution policy.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

EVENTS = ("SessionStart", "PreInvocation", "PreToolUse", "PostToolUse", "Stop")
RETENTION_SECONDS = 600
FAILURE_KINDS = {
    "quota", "rate_limit", "sign_in", "access_denied", "unavailable",
    "timeout", "step_limit", "token_limit", "interrupted", "launch", "request_failed",
}
RETRY_DURATION = re.compile(r"\b(?:resets? in|retry after)\s+((?:\d{1,4}[dhms]\s*)+)\b")
DURATION_UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def status_root():
    override = os.environ.get("NOCTALIA_AGENT_STATUS_DIR")
    if override:
        return Path(override)
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return Path(runtime) / "noctalia-agent"


def process_info(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        if fields[0] == "Z":
            return None
        return int(fields[1]), fields[19]  # parent PID and kernel process start time
    except (OSError, ValueError, IndexError):
        return None


def agent_pid():
    explicit = os.environ.get("NOCTALIA_AGENT_PID", "")
    if explicit.isdigit() and int(explicit) > 1 and process_info(int(explicit)):
        return int(explicit)
    pid = os.getppid()
    for _ in range(32):
        info = process_info(pid)
        if pid <= 1 or info is None:
            break
        try:
            name = Path(os.readlink(f"/proc/{pid}/exe")).name
            if name.startswith(("agy", "antigravity")):
                return pid
        except OSError:
            pass
        pid = info[0]
    return None


def record_path(identifier):
    name = hashlib.sha256(identifier.encode()).hexdigest()
    return status_root() / f"{name}.json"


def failure_details(reason, error, updated):
    """Extract a display category and optional retry time from a Stop event."""
    text = error.casefold() if isinstance(error, str) else ""
    if "quota" in text:
        kind = "quota"
    elif re.search(r"\b(?:429|resource_exhausted|rate limit|too many requests)\b", text):
        kind = "rate_limit"
    elif re.search(r"\b(?:401|unauthenticated|unauthorized)\b", text):
        kind = "sign_in"
    elif re.search(r"\b(?:403|permission_denied|permission denied)\b", text):
        kind = "access_denied"
    elif re.search(r"\b(?:503|service unavailable|overloaded)\b", text):
        kind = "unavailable"
    elif re.search(r"\b(?:timeout|timed out|deadline_exceeded|deadline exceeded)\b", text):
        kind = "timeout"
    elif reason == "max_steps_exceeded":
        kind = "step_limit"
    elif reason == "max_tokens_exceeded":
        kind = "token_limit"
    else:
        kind = "request_failed"

    detail = {"kind": kind}
    if kind in {"quota", "rate_limit"} and (match := RETRY_DURATION.search(text)):
        seconds = sum(int(value) * DURATION_UNITS[unit]
                      for value, unit in re.findall(r"(\d+)([dhms])", match[1]))
        if seconds:
            detail["retry_at"] = updated + seconds
    return detail


def display_time(timestamp):
    try:
        return time.strftime("%b %d %H:%M:%S %Z", time.localtime(timestamp))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def snapshot_failure(record, now):
    """Describe the selected failure using only known kinds and local times."""
    stored = record.get("failure")
    if not isinstance(stored, dict):
        stored = {}
    kind = stored.get("kind")
    detail = {"kind": kind if isinstance(kind, str) and kind in FAILURE_KINDS else "request_failed",
              "reported_at": display_time(float(record["updated"]))}
    retry_at = stored.get("retry_at")
    if detail["kind"] in {"quota", "rate_limit"} and type(retry_at) in (int, float):
        if retry_time := display_time(retry_at):
            detail.update(retry_time=retry_time, retry_passed=now >= retry_at)
    return detail


def save_record(identifier, status, payload, *, failure=None):
    directory = status_root()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    pid = agent_pid()
    identity = process_info(pid) if pid else None
    paths = payload.get("workspacePaths", [])
    workspace = Path(paths[0]).name[:120] if paths and isinstance(paths[0], str) else ""
    data = {"status": status, "updated": time.time(), "pid": pid,
            "start_time": identity[1] if identity else None, "workspace": workspace}
    if status == "error":
        data["failure"] = failure or failure_details(
            payload.get("terminationReason"), payload.get("error"), data["updated"])
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream)
        temporary.replace(record_path(identifier))
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    if pid and identifier != f"launcher-{pid}":
        record_path(f"launcher-{pid}").unlink(missing_ok=True)


def handle_hook(event, payload):
    identifier = payload.get("conversationId")
    if not isinstance(identifier, str) or not identifier:
        return
    status = "working"
    if event == "PreToolUse" and payload.get("toolCall", {}).get("name") == "ask_question":
        status = "waiting"
    elif event == "Stop":
        reason = str(payload.get("terminationReason", ""))
        if payload.get("error") or reason in ("error", "max_steps_exceeded", "max_tokens_exceeded"):
            status = "error"
        elif payload.get("fullyIdle") is False:
            status = "working"
        elif reason in ("cancelled", "canceled", "user_cancel", "user_cancelled"):
            status = "waiting"
        else:
            status = "done"
    save_record(identifier, status, payload)


def snapshot():
    records = []
    now = time.time()
    for path in status_root().glob("*.json"):
        try:
            record = json.loads(path.read_text())
            state = record["status"]
            if state not in ("working", "waiting", "done", "error"):
                continue
            age = max(0, now - float(record["updated"]))
            pid = record.get("pid")
            identity = process_info(pid) if isinstance(pid, int) and pid > 1 else None
            alive = identity is not None and identity[1] == record.get("start_time")
            if not alive:
                if age > RETENTION_SECONDS or state == "waiting":
                    path.unlink(missing_ok=True)
                    continue
                if state == "working":
                    record["status"] = "error"
                    record["failure"] = {"kind": "interrupted"}
            records.append(record)
        except (OSError, ValueError, TypeError, KeyError):
            continue
    counts = {state: sum(r["status"] == state for r in records)
              for state in ("working", "waiting", "done", "error")}
    active = [r for r in records if r["status"] in ("working", "waiting")]
    if active:
        state = "waiting" if counts["waiting"] else "working"
        selected = max((r for r in active if r["status"] == state), key=lambda r: r["updated"])
    elif records:
        selected = max(records, key=lambda r: r["updated"])
        state = selected["status"]
    else:
        selected, state = {}, "idle"
    result = {"status": state, "counts": counts, "workspace": selected.get("workspace", "")}
    if state == "error":
        result["failure"] = snapshot_failure(selected, now)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("hook").add_argument("event", choices=EVENTS)
    commands.add_parser("status")
    commands.add_parser("watch")
    commands.add_parser("run").add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == "hook":
        try:
            handle_hook(args.event, json.load(sys.stdin))
        except Exception as error:
            # This is a display-only observer: failures must never block the agent.
            print(f"[agent-status] {error}", file=sys.stderr)
        response = {"decision": "allow"} if args.event in ("PreToolUse", "Stop") else {}
        print(json.dumps(response))
    elif args.command == "run":
        os.environ["NOCTALIA_AGENT_PID"] = str(os.getpid())
        identifier = f"launcher-{os.getpid()}"
        try:
            save_record(identifier, "waiting", {"workspacePaths": [os.getcwd()]})
        except OSError:
            pass
        executable = str(Path.home() / ".local/bin/agy")
        try:
            os.execv(executable, [executable, "--dangerously-skip-permissions", *args.arguments])
        except OSError as error:
            save_record(identifier, "error", {}, failure={"kind": "launch"})
            print(f"Unable to launch Antigravity: {error}", file=sys.stderr)
            return 127
    elif args.command == "status":
        print(json.dumps(snapshot()))
    else:
        previous = None
        while True:
            current = json.dumps(snapshot(), sort_keys=True)
            if current != previous:
                print(current, flush=True)
                previous = current
            time.sleep(0.5)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BrokenPipeError, KeyboardInterrupt):
        pass
