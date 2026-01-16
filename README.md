# cl-pprint

A toolkit for working with Claude Code conversation logs. Browse transcripts, extract lessons learned, and export to readable formats.

## What Can You Do?

### Learn from past conversations with `cl_dream.py`

Extract insights and lessons from your Claude Code sessions and automatically update your project documentation. Uses a two-phase architecture: parallel Sonnet calls extract lessons from each conversation, then a single Opus session synthesizes them into documentation updates.

> **Requires Claude Max subscription.** This script spawns multiple Claude CLI subprocesses and uses Opus for synthesis. Not recommended for Pro or lower tiers due to rate limits and cost.

```bash
# Process recent conversations for a project
python cl_dream.py /path/to/project

# Include related directories (for moved/renamed projects)
python cl_dream.py /path/to/project --related /old/path

# Skip extraction if lesson files exist, just synthesize
python cl_dream.py /path/to/project --retry
```

**When to use:** After completing a complex feature or debugging session. Captures what worked, what didn't, and updates CLAUDE.md with project-specific guidance.

### Browse transcripts in your browser with `browse_web.py`

A web-based interface for searching, viewing, and exporting transcripts. Launches a local server and opens in your default browser.

```bash
python browse_web.py
python browse_web.py --port 8080 --no-browser
```

**When to use:** When you want a visual interface to explore conversations, search across projects, or share transcript exports with others.

### Browse transcripts in terminal with `browse_transcripts.py`

Interactive TUI for power users who prefer staying in the terminal.

```bash
python browse_transcripts.py
```

**When to use:** Quick lookups, batch exports, keyboard-driven workflow.

### Format a single transcript with `format_jsonl.py`

Convert a JSONL log file to readable markdown.

```bash
python format_jsonl.py session.jsonl output.md
python format_jsonl.py session.jsonl --show-tools --show-thinking
```

**When to use:** Exporting a specific conversation, creating documentation, or debugging what happened in a session.

### Generate AI summaries with `summarize_transcripts_claude.py`

Creates short summaries for all transcripts using Claude Haiku via Claude Code. No GPU required, lightweight on usage limits.

```bash
python summarize_transcripts_claude.py
python summarize_transcripts_claude.py --force  # Re-summarize all
python summarize_transcripts_claude.py --dry-run  # Preview only
```

**When to use:** Before browsing, to make it easier to find relevant conversations. Recommended for most users.

### Generate AI summaries with `summarize_transcripts.py` (Ollama)

Alternative summarizer using local Ollama. Requires a local GPU and Ollama setup.

```bash
python summarize_transcripts.py
python summarize_transcripts.py --force  # Re-summarize all
```

**When to use:** If you prefer local inference or want to avoid using your Claude Code usage limits.

## Installation

```bash
pip install -r requirements.txt
```

The Claude-based tools require [Claude Code](https://claude.ai/code) to be installed and authenticated.

For the Ollama-based summarizer (optional), install [Ollama](https://ollama.ai/) and pull a model:

```bash
ollama pull qwen3:30b-a3b-thinking-2507-q4_K_M
```

## Configuration

Copy the example config and customize:

```bash
cp config.example.json config.json
```

Key settings:

| Key | Description |
|-----|-------------|
| `ollama.model` | Model for summarization (e.g., `qwen3:30b-a3b`) |
| `ollama.url` | Ollama API endpoint |
| `paths.claude_projects` | Where Claude stores logs (default: `~/.claude/projects`) |
| `paths.export_dir` | Where to save exported markdown |

## TUI Browser Commands

| Key | Action |
|-----|--------|
| `n` / `p` | Next / previous page |
| `s` | Search transcripts |
| `f` | Filter by project |
| `#` | Toggle selection (e.g., `42`) |
| `#-#` | Range select (e.g., `1-5`) |
| `v#` | View transcript details |
| `e` | Export selected to markdown |
| `q` | Quit |

## Format Options

The formatter and exporters support these flags:

| Flag | Effect |
|------|--------|
| `--show-tools` | Include tool calls and results |
| `--show-thinking` | Include Claude's thinking blocks |
| `--show-status` | Include brief status messages |
| `--exclude-timestamps` | Hide timestamps |

## Data Locations

| Path | Contents |
|------|----------|
| `~/.claude/projects/` | Claude Code conversation logs (JSONL) |
| `~/.claude/transcript_summaries.json` | Cached AI summaries |
| `./exports/` | Exported markdown files |

## License

MIT
