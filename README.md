# rd-ai-logger

Automatically log your AI coding sessions (Claude Code, Cursor, GitHub Copilot) as **R&D tax credit issues** in GitLab or GitHub — one issue per week, evaluated by Claude AI against the IRS 4-part test.

Works alongside [rd-credit-scanner](https://github.com/yourusername/rd-credit-scanner) — scan existing issues for R&D credits.

---

## What it does

1. Reads local AI coding session logs from Claude Code, Cursor, or GitHub Copilot
2. Evaluates each session for R&D qualification using Claude AI
3. Bundles qualifying sessions by week
4. Creates one GitLab/GitHub issue per week with full documentation

Each issue includes:
- What technical uncertainty was being resolved
- How many sessions and estimated hours
- IRS 4-part test checklist
- AI verdict and confidence per session
- Ready for human review and rate annotation

---

## Why this matters

When you build with AI coding tools, the R&D work happens **in the conversation** — not in a GitLab/GitHub issue. That work is invisible to any R&D tracking system and often missed entirely at tax time.

This tool surfaces that work, documents it properly, and creates the contemporaneous records the IRS requires.

---

## Requirements

- Python 3.8+ (no external dependencies)
- [Claude Code](https://claude.ai/code) installed locally (used for AI evaluation)
- A GitLab or GitHub personal access token

**For Claude Code session logs:**
Claude Code stores sessions automatically. No extra steps needed.

**For Cursor session logs:**
Cursor does not expose logs in a standard location. You need to export your chat history manually:
1. Open Cursor
2. Open the Command Palette (`Cmd+Shift+P` on Mac, `Ctrl+Shift+P` on Windows/Linux)
3. Search for `Export Chat History` and run it
4. Save the exported JSON file to a folder, e.g. `~/cursor-exports/`
5. Pass that folder to the logger with `--logs-dir ~/cursor-exports/`



---

## Setup

```bash
git clone https://github.com/yourusername/rd-ai-logger
cd rd-ai-logger

export GIT_TOKEN=your_gitlab_or_github_token
```

Evaluation runs through Claude Code locally — no API key needed.

---

## First run — Claude Code + Cursor combined

This is the recommended first run. It scans both Claude Code (auto-detected) and your exported Cursor logs together, does a dry run so nothing is created yet, and lets you review what would be logged.

**Step 1 — Export your Cursor history**
Open Cursor → Command Palette → `Export Chat History` → save to `~/cursor-exports/`

**Step 2 — Dry run to preview everything**
```bash
python logger.py \
  --platform gitlab \
  --repo mycompany/rd-log \
  --months 1 \
  --sources claude,cursor \
  --logs-dir ~/cursor-exports/ \
  --dry-run
```

You'll see every session Claude Code found automatically plus everything from your Cursor export, evaluated and grouped by week — but nothing is created yet.

**Step 3 — Create the issues when you're happy**
```bash
python logger.py \
  --platform gitlab \
  --repo mycompany/rd-log \
  --months 1 \
  --sources claude,cursor \
  --logs-dir ~/cursor-exports/
```

**GitHub version:**
```bash
python logger.py \
  --platform github \
  --repo mycompany/rd-log \
  --months 1 \
  --sources claude,cursor \
  --logs-dir ~/cursor-exports/ \
  --dry-run
```

---



## Recommended setup — dedicated R&D log repo

The recommended pattern is a single dedicated repo that collects all your R&D activity, separate from your code repos:

```
mycompany/rd-log        ← all R&D issues go here
mycompany/product-api   ← your actual code lives here
mycompany/frontend      ← your actual code lives here
```

**Create the repo once:**

```bash
# GitLab
# Create a new repo called rd-log (or any name) in your GitLab group

# GitHub
# Create a new repo called rd-log in your GitHub org or personal account
```

**Then run the logger pointing at it:**

```bash
# All Claude Code sessions from the past month → your R&D log repo
python logger.py --platform gitlab --repo mycompany/rd-log --months 1

# Or GitHub
python logger.py --platform github --repo mycompany/rd-log --months 1
```

The logger scans **all** local Claude Code sessions regardless of which project they belong to, and creates one weekly issue per week in your R&D log repo. Your code repos stay untouched.

**Tip:** Run `rd-credit-scanner` against the same R&D log repo to generate your quarterly report:

```bash
python scanner.py --platform gitlab --repo mycompany/rd-log --since 2025-01-01
```

---



```bash
# Scan last month of Claude Code sessions → create GitLab issues
python logger.py --platform gitlab --repo group/project --months 1

# Scan last month → GitHub → dry run first (recommended)
python logger.py --platform github --repo owner/repo --months 1 --dry-run

# Scan last 3 months (IRS lookback)
python logger.py --platform gitlab --repo group/project --months 3

# Only Claude Code logs (skip Cursor and Copilot)
python logger.py --platform gitlab --repo group/project --sources claude

# Use exported log files instead of auto-detection
python logger.py --platform gitlab --repo group/project --logs-dir ~/exports/cursor_logs

# Skip Needs Review — only create issues for high-confidence qualifying sessions
python logger.py --platform gitlab --repo group/project --skip-not-qualifying
```

---

## Log file locations (auto-detected)

### Claude Code
```
~/.claude/projects/*/conversations/*.jsonl
~/.claude/projects/*/*.jsonl
~/.claude/conversations/*.jsonl
```

### Cursor
```
~/.cursor/logs/chat/*.json
~/.cursor/logs/*.json
./cursor_export*.json        ← exported files in current directory
```

### GitHub Copilot
```
~/Library/Application Support/Code/logs/*/GitHub.copilot-chat/*.json  (macOS)
~/.config/Code/logs/*/GitHub.copilot-chat/*.json                       (Linux)
./copilot_export*.json       ← exported files in current directory
```

> **Cursor and Copilot tip:** If auto-detection doesn't find your logs, export your chat history from the tool and point to the folder with `--logs-dir`.

---

## What an issue looks like

```
Title: R&D AI Coding Sessions — 2025-W12

## R&D AI Coding Sessions — Week 2025-W12

Source: Claude Code, Cursor
Sessions: 4 total (3 qualifying, 1 needs review)
Estimated time: 6.5h

## Technical Uncertainty
- Uncertain whether WebSockets or SSE would handle reconnection at scale
- Evaluating three approaches to multi-tenant data isolation
- Debugging unexpected memory leak in async queue processor

## Sessions
| Date       | Source      | Duration | Verdict    | Confidence | Summary                          |
|------------|-------------|----------|------------|------------|----------------------------------|
| 2025-03-18 | Claude Code | 45min    | Qualifying | 92%        | How do I architect the webhook.. |
| 2025-03-19 | Cursor      | 30min    | Qualifying | 88%        | Debugging the SSE reconnection.. |
...

## IRS 4-Part Test
- [x] Technological in nature
- [x] Permitted purpose
- [x] Technical uncertainty
- [x] Experimentation
```

The issue is created with the `R&D` label (or `R&D Needs Review` if any sessions are uncertain). Labels are created automatically if they don't exist.

---

## Workflow with rd-credit-scanner

These two tools are designed to work together:

```
rd-ai-logger   →  Creates issues for AI coding sessions
rd-credit-scanner  →  Scans ALL issues (including those) for R&D report
```

Run `rd-ai-logger` first to populate issues, then run `rd-credit-scanner` to generate the full report for your tax specialist.

---

## Cost

Free. Evaluation runs locally through Claude Code — no API costs.

---

## Privacy

- Your logs never leave your machine except to call the GitHub/GitLab API to create issues
- Session content is passed to Claude Code locally for evaluation — nothing is sent to external servers
- No data is stored or logged by this tool
- Use `--dry-run` to see evaluations without creating any issues

---

## Supported log formats

| Tool | Format | Auto-detected |
|---|---|---|
| Claude Code | JSONL per message | Yes |
| Cursor | JSON array export | Yes (partial) |
| GitHub Copilot | JSON from VS Code logs | Yes (partial) |
| Generic | JSONL or JSON array with role/content/timestamp | Via --logs-dir |

---

## Contributing

PRs welcome. Ideas:

- [ ] Support Windsurf / Codeium logs
- [ ] Support Claude.ai conversation exports
- [ ] Add `--milestone` flag to assign issues to GitLab milestones automatically
- [ ] Summarise weekly issues into a monthly rollup
- [ ] Add time estimate heuristics based on message count when timestamps are missing

---

## Related

-[ [rd-credit-scanner](https://github.com/yourusername/rd-credit-scanner)](https://github.com/lalunalalol/rd-credit-scanner) — scan existing GitLab/GitHub issues for R&D credits

---

## License

MIT

---

## Disclaimer

This tool is for informational purposes only and does not constitute tax advice. Always have a qualified R&D tax credit specialist review the output before filing. AI evaluation is a starting point, not a final determination.
