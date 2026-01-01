# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a toolkit for processing Claude Code agent JSONL logs. It provides four main utilities:
- **format_jsonl.py** - Converts JSONL logs to readable markdown
- **summarize_transcripts.py** - Generates AI summaries using local Ollama
- **browse_transcripts.py** - Interactive TUI for browsing and exporting transcripts
- **browse_web.py** - Web-based browser with SPA interface

## Web Framework

The web browser (`browse_web.py` + `static/`) uses the VDX framework. See [FRAMEWORK.md](FRAMEWORK.md) for patterns and usage.

## Commands

Run the formatter:
```bash
python format_jsonl.py <input.jsonl> [output.md] [--show-tools] [--show-thinking] [--show-status] [--exclude-timestamps]
```

Generate AI summaries (requires Ollama running locally):
```bash
python summarize_transcripts.py [--dir ~/.claude/projects] [--force] [--dry-run]
```

Launch the interactive browser:
```bash
python browse_transcripts.py [--dir ~/.claude/projects]
```

Launch the web browser (opens in default browser):
```bash
python browse_web.py [--port 8080] [--no-browser]
```

## Dependencies

Required packages:
```bash
pip install rich prompt_toolkit requests
```

The summarizer requires Ollama running locally (`ollama serve`). Model configured in `config.json`.

## Configuration

Settings are loaded from `config.json` (copy from `config.example.json`). Key settings:
- `ollama.model` / `ollama.url` - Ollama API configuration
- `paths.claude_projects` / `paths.summary_cache` / `paths.export_dir` - File paths
- `project_name_skip_dirs` - Directory names to strip from project display names

The `config.py` module provides `get(key)` for dot-notation access and `get_path(key)` for path expansion.

## Architecture

**format_jsonl.py** is the core module:
- Parses JSONL entries and tracks tool call/result relationships
- Handles plan mode: tracks Write/Edit to plan files, shows diffs between rejected/approved plan versions
- Batches consecutive brief assistant messages into "Progress" sections
- Filters noise: commands like /usage, caveat messages, status messages
- Formats AskUserQuestion with inline answers
- Adds navigation links between plan revisions

**browse_transcripts.py** provides TUI:
- Uses `rich` for rendering tables and panels
- Uses `prompt_toolkit` for command input with history/completion
- Imports and uses `format_jsonl.format_jsonl()` directly for export
- Loads AI summaries from `~/.claude/transcript_summaries.json` cache

**summarize_transcripts.py** generates summaries:
- Extracts user messages (first message, pre-plan messages, long messages)
- Calls Ollama API to generate summary + kebab-case filename
- Caches results to `~/.claude/transcript_summaries.json`

**config.py** handles configuration:
- Loads `config.json` and merges with defaults
- Provides `get()` and `get_path()` helpers

## Data Paths

- Claude logs: `~/.claude/projects/<project-dir>/<session>.jsonl`
- Summary cache: `~/.claude/transcript_summaries.json`
- Default export dir: `./exports/<project>/`
