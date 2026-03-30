#!/usr/bin/env python3
"""
rd-ai-logger
Reads local AI coding session logs (Claude Code, Cursor, GitHub Copilot),
evaluates them for R&D tax credit qualification using Claude AI,
and creates GitLab/GitHub issues retrospectively — one per week.

Supported log sources:
  Claude Code : ~/.claude/projects/*/conversations/*.jsonl
  Cursor      : ~/.cursor/logs/chat/*.json (or exported JSON)
  Copilot     : VS Code extension logs or exported JSON

Usage:
  python logger.py --platform gitlab --repo group/project --months 1
  python logger.py --platform github --repo owner/repo --months 1 --dry-run
"""

import argparse
import json
import os
import sys
import glob
import urllib.request
import urllib.parse
import urllib.error
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path


# ── Constants ─────────────────────────────────────────────────────────────────

ISSUE_LABEL = "R&D"
ISSUE_LABEL_REVIEW = "R&D Needs Review"


# ── Log readers ───────────────────────────────────────────────────────────────

def week_key(date_str):
    """Return ISO week string like 2025-W12 from a date string."""
    try:
        dt = datetime.fromisoformat(date_str[:10])
        return dt.strftime("%G-W%V")
    except Exception:
        return "unknown"


def since_cutoff(months):
    """Return YYYY-MM-DD cutoff date for N months ago."""
    today = datetime.now()
    # Approximate: subtract 30 days per month
    cutoff = today - timedelta(days=30 * months)
    return cutoff.strftime("%Y-%m-%d")


def extract_text(content):
    """Flatten content that may be a string or list of blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text") or block.get("content") or "")
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return str(content)


def read_claude_code_logs(cutoff):
    """
    Read Claude Code conversation logs.
    Location: ~/.claude/projects/*/conversations/*.jsonl
    Format: JSONL, one message per line:
      {"uuid":"...","type":"user","message":{"role":"user","content":"..."},"timestamp":"..."}
    or simpler:
      {"role":"user","content":"...","timestamp":"..."}
    """
    home = Path.home()
    patterns = [
        home / ".claude" / "projects" / "*" / "conversations" / "*.jsonl",
        home / ".claude" / "projects" / "*" / "*.jsonl",
        home / ".claude" / "conversations" / "*.jsonl",
        home / ".claude" / "*.jsonl",
    ]
    sessions = []
    seen = set()
    for pattern in patterns:
        for path in glob.glob(str(pattern)):
            if path in seen:
                continue
            seen.add(path)
            session = _parse_jsonl(path, cutoff, source="Claude Code")
            if session:
                sessions.append(session)
    return sessions


def read_cursor_logs(cutoff):
    """
    Read Cursor chat logs.
    Location: ~/.cursor/logs/chat/*.json  or  exported JSON files
    Format: JSON array of {role, content, timestamp} or Cursor-specific format.
    """
    home = Path.home()
    patterns = [
        home / ".cursor" / "logs" / "chat" / "*.json",
        home / ".cursor" / "logs" / "*.json",
        home / ".cursor-tutor" / "*.json",
        # Also check current directory for exported files
        Path(".") / "cursor_export*.json",
        Path(".") / "cursor_logs" / "*.json",
    ]
    sessions = []
    seen = set()
    for pattern in patterns:
        for path in glob.glob(str(pattern)):
            if path in seen:
                continue
            seen.add(path)
            session = _parse_json_array(path, cutoff, source="Cursor")
            if session:
                sessions.append(session)
    return sessions


def read_copilot_logs(cutoff):
    """
    Read GitHub Copilot chat logs.
    VS Code stores Copilot logs in the extension log directory.
    Users can also export chat history as JSON.
    """
    home = Path.home()
    patterns = [
        # VS Code extension logs (macOS)
        home / "Library" / "Application Support" / "Code" / "logs" / "*" / "GitHub.copilot-chat" / "*.json",
        # VS Code extension logs (Linux)
        home / ".config" / "Code" / "logs" / "*" / "GitHub.copilot-chat" / "*.json",
        # VS Code extension logs (Windows via WSL)
        home / ".vscode" / "extensions" / "github.copilot*" / "*.json",
        # Exported files in current directory
        Path(".") / "copilot_export*.json",
        Path(".") / "copilot_logs" / "*.json",
    ]
    sessions = []
    seen = set()
    for pattern in patterns:
        for path in glob.glob(str(pattern)):
            if path in seen:
                continue
            seen.add(path)
            session = _parse_json_array(path, cutoff, source="GitHub Copilot")
            if session:
                sessions.append(session)
    return sessions


def _parse_jsonl(path, cutoff, source):
    """Parse a JSONL file into a session dict."""
    messages = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    # Handle Claude Code nested format
                    if "message" in raw and isinstance(raw["message"], dict):
                        msg = raw["message"]
                        ts = raw.get("timestamp") or raw.get("created_at") or ""
                    else:
                        msg = raw
                        ts = raw.get("timestamp") or raw.get("created_at") or ""
                    role = msg.get("role") or raw.get("type") or "unknown"
                    content = extract_text(msg.get("content") or raw.get("content") or "")
                    if content:
                        messages.append({"role": role, "content": content, "timestamp": ts})
                except (json.JSONDecodeError, AttributeError):
                    continue
    except Exception:
        return None

    return _build_session(path, messages, cutoff, source)


def _parse_json_array(path, cutoff, source):
    """Parse a JSON array export file into a session dict."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
        # Handle various nesting patterns
        if isinstance(data, dict):
            data = (data.get("messages") or data.get("conversation")
                    or data.get("history") or data.get("chats") or [])
        if not isinstance(data, list):
            return None
        messages = []
        for raw in data:
            if not isinstance(raw, dict):
                continue
            role = raw.get("role") or raw.get("type") or "unknown"
            content = extract_text(
                raw.get("content") or raw.get("text") or raw.get("message") or ""
            )
            ts = raw.get("timestamp") or raw.get("created_at") or raw.get("time") or ""
            if content:
                messages.append({"role": role, "content": content, "timestamp": ts})
    except Exception:
        return None

    return _build_session(path, messages, cutoff, source)


def _build_session(path, messages, cutoff, source):
    """Build a normalised session dict from parsed messages."""
    if not messages:
        return None

    # Find best timestamp
    timestamps = [m["timestamp"] for m in messages if m.get("timestamp")]
    first_ts = timestamps[0] if timestamps else ""
    last_ts = timestamps[-1] if timestamps else ""

    # Use file mtime as fallback
    if not first_ts:
        mtime = os.path.getmtime(path)
        first_ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%dT%H:%M:%S")

    session_date = first_ts[:10]

    # Apply date cutoff
    if cutoff and session_date < cutoff:
        return None

    user_messages = [m["content"] for m in messages if m["role"] in ("user", "human")]
    assistant_messages = [m["content"] for m in messages if m["role"] in ("assistant", "ai", "model")]

    # Estimate duration from timestamps
    duration_minutes = None
    if first_ts and last_ts and first_ts != last_ts:
        try:
            fmt = "%Y-%m-%dT%H:%M:%S"
            t1 = datetime.strptime(first_ts[:19], fmt)
            t2 = datetime.strptime(last_ts[:19], fmt)
            duration_minutes = max(1, int((t2 - t1).total_seconds() / 60))
        except Exception:
            pass

    # Build conversation sample for AI evaluation (first 8 exchanges)
    sample = []
    for msg in messages[:16]:
        role_label = "Developer" if msg["role"] in ("user", "human") else "AI"
        sample.append(f"{role_label}: {msg['content'][:300]}")
    conversation_sample = "\n\n".join(sample)

    return {
        "source": source,
        "path": str(path),
        "date": session_date,
        "week": week_key(session_date),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "duration_minutes": duration_minutes,
        "message_count": len(messages),
        "user_message_count": len(user_messages),
        "first_user_message": user_messages[0][:400] if user_messages else "",
        "conversation_sample": conversation_sample[:3000],
        # To be filled by evaluator
        "verdict": None,
        "confidence": None,
        "reason": None,
        "technical_uncertainty": None,
    }


# ── Claude evaluator ──────────────────────────────────────────────────────────

EVAL_SYSTEM = """You are an R&D tax credit specialist evaluating AI coding sessions.
Determine if the conversation represents qualifying research under IRC Section 41 (4-part test):
1. Technological in nature (engineering, CS, science)
2. Permitted purpose (developing/improving a product, process, or software)
3. Technical uncertainty (developer did not know HOW to achieve the outcome)
4. Experimentation (testing approaches, iterating, debugging unknown behavior)

Common qualifying patterns in AI coding sessions:
- Figuring out HOW to architect or implement something new
- Debugging unexpected/unknown behavior
- Evaluating different technical approaches
- Building new features with uncertain implementation path
- Designing AI agent systems or prompts with unknown outcomes
- Prototyping and iterating on technical solutions

NOT qualifying:
- Generating boilerplate or standard code with known outcome
- Asking how to use a known API or library (documentation lookup)
- Writing tests for already-implemented code
- Formatting, linting, or refactoring with known outcome
- Non-technical questions (writing copy, emails, docs)

Respond ONLY with a valid JSON object, no markdown:
{
  "verdict": "Qualifying" | "Needs Review" | "Not Qualifying",
  "confidence": 0-100,
  "reason": "one sentence explaining the verdict",
  "technical_uncertainty": "one sentence describing what was technically uncertain (if qualifying)"
}"""


def evaluate_session(session):
    """Evaluate a session using the local Claude Code CLI."""
    import subprocess
    prompt = (
        f"{EVAL_SYSTEM}\n\n"
        f"AI coding session to evaluate:\n\n"
        f"Source: {session['source']}\n"
        f"Date: {session['date']}\n"
        f"Messages: {session['message_count']} total, {session['user_message_count']} from developer\n"
        f"Duration: {session['duration_minutes']} minutes\n\n"
        f"First developer message:\n{session['first_user_message']}\n\n"
        f"Conversation sample:\n{session['conversation_sample']}\n\n"
        f"Respond with ONLY a JSON object, no markdown."
    )
    try:
        result = subprocess.run(
            ["claude", "--print"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=30
        )
        text = result.stdout.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except FileNotFoundError:
        print("\n  Error: 'claude' command not found.")
        print("  Make sure Claude Code is installed: https://claude.ai/code")
        sys.exit(1)
    except Exception as e:
        return {
            "verdict": "Needs Review",
            "confidence": 0,
            "reason": f"Evaluation error: {e}",
            "technical_uncertainty": ""
        }


# ── Weekly bundler ────────────────────────────────────────────────────────────

def bundle_by_week(sessions):
    """Group qualifying/review sessions by ISO week."""
    weeks = defaultdict(list)
    for s in sessions:
        if s["verdict"] in ("Qualifying", "Needs Review"):
            weeks[s["week"]].append(s)
    return dict(sorted(weeks.items()))


def build_issue_body(week, sessions):
    """Build the GitLab/GitHub issue body for a weekly bundle."""
    total_minutes = sum(s["duration_minutes"] or 0 for s in sessions)
    total_hours = round(total_minutes / 60, 1)
    qualifying = [s for s in sessions if s["verdict"] == "Qualifying"]
    needs_review = [s for s in sessions if s["verdict"] == "Needs Review"]
    sources = list(set(s["source"] for s in sessions))

    lines = [
        f"## R&D AI Coding Sessions — Week {week}",
        f"",
        f"**Generated by:** [rd-ai-logger](https://github.com/yourusername/rd-ai-logger)  ",
        f"**Sources:** {', '.join(sources)}  ",
        f"**Sessions:** {len(sessions)} total ({len(qualifying)} qualifying, {len(needs_review)} needs review)  ",
        f"**Estimated time:** {total_hours}h  ",
        f"",
        f"---",
        f"",
        f"## Technical Uncertainty",
        f"",
        f"*What was being figured out in these sessions:*",
        f"",
    ]

    for s in qualifying:
        if s.get("technical_uncertainty"):
            lines.append(f"- {s['technical_uncertainty']}")

    lines += [
        f"",
        f"---",
        f"",
        f"## Sessions",
        f"",
        f"| Date | Source | Duration | Verdict | Confidence | Summary |",
        f"|---|---|---|---|---|---|",
    ]

    for s in sessions:
        duration = f"{s['duration_minutes']}min" if s["duration_minutes"] else "—"
        summary = s["first_user_message"][:80].replace("\n", " ").replace("|", "/")
        lines.append(
            f"| {s['date']} | {s['source']} | {duration} "
            f"| {s['verdict']} | {s['confidence']}% | {summary} |"
        )

    lines += [
        f"",
        f"---",
        f"",
        f"## IRS 4-Part Test",
        f"",
        f"- [x] **Technological in nature** — software development using AI assistance",
        f"- [x] **Permitted purpose** — developing or improving a product, process, or software",
        f"- [x] **Technical uncertainty** — see Technical Uncertainty section above",
        f"- [x] **Experimentation** — iterative AI-assisted development with uncertain outcomes",
        f"",
        f"---",
        f"",
        f"> *Human review required before using for R&D tax credit filing.*  ",
        f"> *Add assignee hourly rate to calculate QRE value.*",
    ]

    return "\n".join(lines)


# ── Issue creators ────────────────────────────────────────────────────────────

def create_gitlab_issue(repo, token, title, body, label, dry_run):
    """Create a GitLab issue."""
    encoded = urllib.parse.quote(repo, safe="")
    url = f"https://gitlab.com/api/v4/projects/{encoded}/issues"

    # Ensure label exists first
    _ensure_gitlab_label(repo, token, label, dry_run)

    payload = json.dumps({
        "title": title,
        "description": body,
        "labels": label,
    }).encode()

    if dry_run:
        print(f"    [DRY RUN] Would create GitLab issue: {title}")
        return {"web_url": "(dry run)", "iid": 0}

    req = urllib.request.Request(url, data=payload, headers={
        "PRIVATE-TOKEN": token,
        "Content-Type": "application/json",
        "User-Agent": "rd-ai-logger",
    })
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"    Error creating issue: {e.code} {e.reason}")
        body = e.read().decode()
        print(f"    {body[:200]}")
        return None


def _ensure_gitlab_label(repo, token, label_name, dry_run):
    """Create label in GitLab if it doesn't exist."""
    if dry_run:
        return
    encoded = urllib.parse.quote(repo, safe="")
    url = f"https://gitlab.com/api/v4/projects/{encoded}/labels"
    payload = json.dumps({
        "name": label_name,
        "color": "#28A745" if label_name == ISSUE_LABEL else "#FFC107",
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "PRIVATE-TOKEN": token,
        "Content-Type": "application/json",
        "User-Agent": "rd-ai-logger",
    })
    try:
        urllib.request.urlopen(req)
    except urllib.error.HTTPError:
        pass  # Label likely already exists


def create_github_issue(repo, token, title, body, label, dry_run):
    """Create a GitHub issue."""
    url = f"https://api.github.com/repos/{repo}/issues"

    # Ensure label exists first
    _ensure_github_label(repo, token, label, dry_run)

    payload = json.dumps({
        "title": title,
        "body": body,
        "labels": [label],
    }).encode()

    if dry_run:
        print(f"    [DRY RUN] Would create GitHub issue: {title}")
        return {"html_url": "(dry run)", "number": 0}

    req = urllib.request.Request(url, data=payload, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "rd-ai-logger",
    })
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"    Error creating issue: {e.code} {e.reason}")
        return None


def _ensure_github_label(repo, token, label_name, dry_run):
    """Create label in GitHub if it doesn't exist."""
    if dry_run:
        return
    url = f"https://api.github.com/repos/{repo}/labels"
    color = "28A745" if label_name == ISSUE_LABEL else "FFC107"
    payload = json.dumps({"name": label_name, "color": color}).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "rd-ai-logger",
    })
    try:
        urllib.request.urlopen(req)
    except urllib.error.HTTPError:
        pass  # Label likely already exists


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Log AI coding sessions as R&D issues in GitLab/GitHub"
    )
    parser.add_argument("--platform", choices=["github", "gitlab"], required=True,
                        help="Target platform for creating issues")
    parser.add_argument("--repo", required=True,
                        help="Repository path, e.g. owner/repo or group/project")
    parser.add_argument("--token", default=os.environ.get("GIT_TOKEN"),
                        help="Personal access token (or set GIT_TOKEN env var)")
    parser.add_argument("--months", type=int, default=1,
                        help="How many months back to scan (default: 1)")
    parser.add_argument("--logs-dir", default=None,
                        help="Optional: path to exported log files (overrides auto-detection)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Evaluate sessions but do not create issues")
    parser.add_argument("--skip-not-qualifying", action="store_true",
                        help="Only process Qualifying sessions (skip Needs Review)")
    parser.add_argument("--sources", default="claude,cursor,copilot",
                        help="Comma-separated sources to scan: claude,cursor,copilot (default: all)")
    args = parser.parse_args()

    if not args.token and not args.dry_run:
        print("Error: provide --token or set GIT_TOKEN env var")
        sys.exit(1)

    sources = [s.strip().lower() for s in args.sources.split(",")]
    cutoff = since_cutoff(args.months)

    print(f"\nrd-ai-logger")
    print(f"Platform  : {args.platform}")
    print(f"Repo      : {args.repo}")
    print(f"Scanning  : {args.months} month(s) back (since {cutoff})")
    print(f"Sources   : {', '.join(sources)}")
    print(f"Dry run   : {'YES — no issues will be created' if args.dry_run else 'No'}")
    print()

    # ── Step 1: Read logs ────────────────────────────────────────────────────
    print("Step 1: Reading local AI coding session logs...")
    all_sessions = []

    if args.logs_dir:
        print(f"  Scanning custom directory: {args.logs_dir}")
        for path in glob.glob(os.path.join(args.logs_dir, "**", "*.jsonl"), recursive=True):
            s = _parse_jsonl(path, cutoff, source="Custom")
            if s:
                all_sessions.append(s)
        for path in glob.glob(os.path.join(args.logs_dir, "**", "*.json"), recursive=True):
            s = _parse_json_array(path, cutoff, source="Custom")
            if s:
                all_sessions.append(s)
    else:
        if "claude" in sources:
            sessions = read_claude_code_logs(cutoff)
            print(f"  Claude Code : {len(sessions)} sessions found")
            all_sessions.extend(sessions)
        if "cursor" in sources:
            sessions = read_cursor_logs(cutoff)
            print(f"  Cursor      : {len(sessions)} sessions found")
            all_sessions.extend(sessions)
        if "copilot" in sources:
            sessions = read_copilot_logs(cutoff)
            print(f"  Copilot     : {len(sessions)} sessions found")
            all_sessions.extend(sessions)

    if not all_sessions:
        print("\n  No sessions found.")
        print("  Tips:")
        print("  - Make sure Claude Code has been used in the past", args.months, "month(s)")
        print("  - For Cursor/Copilot, export logs and use --logs-dir /path/to/exports")
        print("  - Use --months 3 to look further back")
        sys.exit(0)

    print(f"  Total     : {len(all_sessions)} sessions to evaluate")

    # ── Step 2: Evaluate with Claude ─────────────────────────────────────────
    print(f"\nStep 2: Evaluating {len(all_sessions)} sessions with Claude AI...")
    for idx, session in enumerate(all_sessions, 1):
        label = session["first_user_message"][:50].replace("\n", " ")
        print(f"  [{idx}/{len(all_sessions)}] {session['date']} — {label}...")
        result = evaluate_session(session)
        session["verdict"] = result.get("verdict", "Needs Review")
        session["confidence"] = result.get("confidence", 0)
        session["reason"] = result.get("reason", "")
        session["technical_uncertainty"] = result.get("technical_uncertainty", "")
        print(f"    → {session['verdict']} ({session['confidence']}%)")

    qualifying_count = sum(1 for s in all_sessions if s["verdict"] == "Qualifying")
    review_count = sum(1 for s in all_sessions if s["verdict"] == "Needs Review")
    print(f"\n  Qualifying: {qualifying_count} | Needs Review: {review_count} "
          f"| Not Qualifying: {len(all_sessions) - qualifying_count - review_count}")

    # ── Step 3: Bundle by week and create issues ──────────────────────────────
    print(f"\nStep 3: Bundling by week and creating issues...")

    # Filter sessions
    included_sessions = [
        s for s in all_sessions
        if s["verdict"] == "Qualifying"
        or (s["verdict"] == "Needs Review" and not args.skip_not_qualifying)
    ]

    if not included_sessions:
        print("  No qualifying sessions to create issues for.")
        sys.exit(0)

    weekly_bundles = bundle_by_week(included_sessions)
    print(f"  {len(weekly_bundles)} weekly issue(s) to create")

    created = []
    for week, sessions in weekly_bundles.items():
        has_review = any(s["verdict"] == "Needs Review" for s in sessions)
        label = ISSUE_LABEL_REVIEW if has_review else ISSUE_LABEL
        title = f"R&D AI Coding Sessions — {week}"
        body = build_issue_body(week, sessions)

        print(f"\n  Week {week} ({len(sessions)} sessions) → label: {label}")

        if args.platform == "gitlab":
            result = create_gitlab_issue(
                args.repo, args.token, title, body, label, args.dry_run
            )
            url = result.get("web_url", "") if result else ""
        else:
            result = create_github_issue(
                args.repo, args.token, title, body, label, args.dry_run
            )
            url = result.get("html_url", "") if result else ""

        if result:
            print(f"    ✓ {url}")
            created.append({"week": week, "sessions": len(sessions), "url": url, "label": label})
        else:
            print(f"    ✗ Failed to create issue for week {week}")

    # ── Summary ───────────────────────────────────────────────────────────────
    total_minutes = sum(
        s["duration_minutes"] or 0
        for s in included_sessions
    )
    total_hours = round(total_minutes / 60, 1)

    print(f"""
Done.
  Sessions evaluated   : {len(all_sessions)}
  Qualifying sessions  : {qualifying_count}
  Needs Review         : {review_count}
  Issues created       : {len(created)}
  Estimated total time : {total_hours}h

Issues created:""")
    for c in created:
        print(f"  [{c['label']}] Week {c['week']} ({c['sessions']} sessions) → {c['url']}")

    if args.dry_run:
        print("\n  DRY RUN — no issues were actually created. Remove --dry-run to create them.")

    print("""
Next steps:
  1. Open the issues in your repo and verify the AI verdicts
  2. Add your hourly rate to calculate QRE value
  3. Run rd-credit-scanner to include these issues in your R&D report
""")


# ── Module-level helpers (needed by main) ────────────────────────────────────
# These are defined here so the file is self-contained

def _parse_jsonl(path, cutoff, source):
    messages = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    if "message" in raw and isinstance(raw["message"], dict):
                        msg = raw["message"]
                        ts = raw.get("timestamp") or raw.get("created_at") or ""
                    else:
                        msg = raw
                        ts = raw.get("timestamp") or raw.get("created_at") or ""
                    role = msg.get("role") or raw.get("type") or "unknown"
                    content = extract_text(msg.get("content") or raw.get("content") or "")
                    if content:
                        messages.append({"role": role, "content": content, "timestamp": ts})
                except (json.JSONDecodeError, AttributeError):
                    continue
    except Exception:
        return None
    return _build_session(path, messages, cutoff, source)


def _parse_json_array(path, cutoff, source):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = (data.get("messages") or data.get("conversation")
                    or data.get("history") or data.get("chats") or [])
        if not isinstance(data, list):
            return None
        messages = []
        for raw in data:
            if not isinstance(raw, dict):
                continue
            role = raw.get("role") or raw.get("type") or "unknown"
            content = extract_text(
                raw.get("content") or raw.get("text") or raw.get("message") or ""
            )
            ts = raw.get("timestamp") or raw.get("created_at") or raw.get("time") or ""
            if content:
                messages.append({"role": role, "content": content, "timestamp": ts})
    except Exception:
        return None
    return _build_session(path, messages, cutoff, source)


def _build_session(path, messages, cutoff, source):
    if not messages:
        return None
    timestamps = [m["timestamp"] for m in messages if m.get("timestamp")]
    first_ts = timestamps[0] if timestamps else ""
    last_ts = timestamps[-1] if timestamps else ""
    if not first_ts:
        mtime = os.path.getmtime(path)
        first_ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%dT%H:%M:%S")
    session_date = first_ts[:10]
    if cutoff and session_date < cutoff:
        return None
    user_messages = [m["content"] for m in messages if m["role"] in ("user", "human")]
    duration_minutes = None
    if first_ts and last_ts and first_ts != last_ts:
        try:
            fmt = "%Y-%m-%dT%H:%M:%S"
            t1 = datetime.strptime(first_ts[:19], fmt)
            t2 = datetime.strptime(last_ts[:19], fmt)
            duration_minutes = max(1, int((t2 - t1).total_seconds() / 60))
        except Exception:
            pass
    sample = []
    for msg in messages[:16]:
        role_label = "Developer" if msg["role"] in ("user", "human") else "AI"
        sample.append(f"{role_label}: {msg['content'][:300]}")
    return {
        "source": source,
        "path": str(path),
        "date": session_date,
        "week": week_key(session_date),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "duration_minutes": duration_minutes,
        "message_count": len(messages),
        "user_message_count": len(user_messages),
        "first_user_message": user_messages[0][:400] if user_messages else "",
        "conversation_sample": "\n\n".join(sample)[:3000],
        "verdict": None,
        "confidence": None,
        "reason": None,
        "technical_uncertainty": None,
    }


if __name__ == "__main__":
    main()
