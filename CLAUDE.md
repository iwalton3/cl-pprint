# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a toolkit for processing Claude Code agent JSONL logs. It provides five main utilities:
- **format_jsonl.py** - Converts JSONL logs to readable markdown
- **summarize_transcripts.py** - Generates AI summaries using local Ollama
- **browse_transcripts.py** - Interactive TUI for browsing and exporting transcripts
- **browse_web.py** - Web-based browser with SPA interface
- **cl_dream.py** - Extracts lessons from conversations and updates documentation (two-phase architecture)

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

Extract lessons and update documentation:
```bash
python cl_dream.py run /path/to/project [--related /path/to/related] [--retry] [--dry-run]

# Run incrementally on all previously-processed projects
python cl_dream.py auto [--cleanup]

# Clean up CLAUDE.md only (remove stale/one-off content)
python cl_dream.py cleanup /path/to/project
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

**Note**: Claude Code uses `~/.claude/` on all platforms including Windows.

## Architecture

**format_jsonl.py** is the core module:
- Parses JSONL entries and tracks tool call/result relationships via `tool_id_to_name` dict
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
- Uses absolute IDs for selection (what you see is what you type)

**summarize_transcripts.py** generates summaries:
- Extracts user messages (first message, pre-plan messages, long messages >250 chars)
- Calls Ollama API to generate summary + kebab-case filename
- Caches results to `~/.claude/transcript_summaries.json`

**config.py** handles configuration:
- Loads `config.json` and merges with defaults
- Provides `get()` and `get_path()` helpers

**cl_dream.py** uses multi-phase architecture:
- Phase 1: Parallel subprocess calls to Claude CLI (Sonnet) extract lessons from each conversation
- Phase 1.5: Exploration analysis - aggregates ALL historical file access and Explore prompts into heatmap + theme summary
- Phase 2: Single Opus session synthesizes lessons and updates documentation with clean context
- Phase 3: Generate conversation summaries for the browser (uses Haiku)
- Phase 4 (optional): CLAUDE.md cleanup - removes stale/one-off content (uses Opus)
- `auto` subcommand: discovers previously-processed projects and runs incrementally
- `cleanup` subcommand: runs only the cleanup phase on a project
- Uses `--retry` flag to skip Phase 1 if lesson files already exist

**Exploration analysis** identifies documentation gaps by analyzing what Claude repeatedly explores:
- File access heatmap: files with >30% session access rate go in "Key Locations" table
- Explore prompt themes: recurring questions become documentation (5+ times → CLAUDE.md, 2-4 times → docs/)

**Cleanup phase guidelines** - optimizes CLAUDE.md (~100-300 lines target):
- Key question: "Would this help me get productive in this project faster?"
- REMOVE: One-off details, generic advice, easily discoverable things, verbose explanations
- KEEP: Commands, architecture, unique patterns, gotchas, file hints
- Reference docs/ instead of duplicating content
- ADD "Required Reading (VERY IMPORTANT)" section when content is externalized to docs

## Data Paths

- Claude logs: `~/.claude/projects/<project-dir>/<session>.jsonl`
- Summary cache: `~/.claude/transcript_summaries.json`
- Default export dir: `./exports/<project>/`

## JSONL Entry Types

Not all session files contain actual conversation content. Handle these cases:

| Entry Type | Description | Has Content |
|------------|-------------|-------------|
| `user` / `assistant` | Normal messages with `content` arrays | Yes |
| `queue-operation` | User message sent during agent processing. Content is direct string in `entry.content`, not array | Yes |
| `summary` | Compacted/resumed session. Content in `entry.data.summary`. Leaf conversations referenced via `leafUuid` | Sometimes |
| `file-history-snapshot` | File version tracking metadata | No |

**Branching conversations**: Parent files with only `type: "summary"` entries contain no actual messages - they're pointers to leaf conversation files via `leafUuid`.

**Empty sessions**: Sessions can exist with no user interaction (file-history-snapshots only). Filter these when browsing or generating summaries. Three types of low-value sessions to filter:
1. File-history-only (no messages at all)
2. Summary-only branch parents (pointers to leaf conversations)
3. Trivial test prompts (<100 chars of user content)

The `has_conversation_content()` pattern checks both user message length AND assistant response presence.

## Format Options

Tool display is controlled by multiple flags that interact:
- `show_tools` - Master switch for tool calls/results
- `truncate_calls` / `truncate_results` - Limit content to 500 chars
- `exclude_edit_tools` - Hide Write/Edit/NotebookEdit
- `exclude_view_tools` - Hide Read/Grep/Glob
- `show_explore_full` / `show_subagents_full` - Override truncation for agents

Specific tool options override general ones (e.g., `show_explore_full=True` shows Explore agents even when `show_tools=False`).

## Known Gotchas

### Markdown Formatting
- **4+ spaces triggers code blocks**: Use 2-3 spaces for indentation in lists
- **Triple backticks in content**: Use `escape_code_fences()` to replace ``` with ` ` `
- **VS Code anchor format**: Headers become `#-lowercase-with-dashes` anchors (custom `<a id>` tags don't work)
- **GFM underline**: Use `<ins>` not `<u>` for underline

### Ollama API
- **Thinking models loop forever**: Always include `"think": False` for direct output
- **Preamble contamination**: Use `"format": "json"` to force structured output

### VDX Framework
- **Store functions**: Export standalone functions, not store methods
- **Boolean attributes**: Use `checked="${value}"` not `.checked="${value}"`
- **Hash router hijacks anchors**: Add click handler for in-page anchor links
- **marked.js needs custom renderer**: Configure heading renderer to generate `id` attributes

### Rich TUI
- **Don't pre-truncate**: Let Rich handle with `no_wrap=True, overflow="ellipsis"`

### Tool Processing
- **Tool IDs don't persist across messages**: Use shared `tool_id_to_name` dict passed through all extraction calls
- **System reminders in results**: Strip with non-greedy regex `<system-reminder>.*?</system-reminder>` (greedy `.*` will match too much)

### Subprocess / CLI
- **Long prompts interpreted as file paths**: Pass prompts via stdin, not command-line arguments
- **`subprocess.run()` with `input=` buffers output**: Use `Popen` with stdin piped if you need streaming
- **Claude CLI `--print` doesn't stream**: Use `--output-format stream-json` for incremental output

### Plan Tracking
- **State accumulation**: Track both Write and Edit operations to reconstruct plan state at each ExitPlanMode
- **Renumbering noise**: Filter diff lines that are just list item renumbering (strip numbers, compare text)

### Cross-Platform
- **Claude data directory**: `~/.claude/` on all platforms (Windows, Linux, macOS)
- **Path slug conversion**: Claude converts paths to directory names by replacing both `/` and `\` with `-`, and `.` with `-`, plus removing `:` from drive letters (e.g., `C:\Projects\JFD.API` → `-C-Projects-JFD-API`)
- **Path expansion**: Use `Path.expanduser()` for `~` expansion (works on Windows too)

### Claude Code Conventions
- **Directory naming**: Path separators (`/` and `\`), dots (`.`), and colons (`:`) are converted to `-` in project directory names (e.g., `/working/JFD.API` → `-working-JFD-API`, `C:\Projects\JFD.API` → `-C-Projects-JFD-API`)
- **Subdirectory projects**: Running `claude code` from subdirectories creates separate project directories that won't match parent directory searches
- **Related directories**: Use `--related` flag for moved projects - historical paths don't need to exist on disk

### CLAUDE.md Content Quality
When adding content to CLAUDE.md, **DO NOT include**:
- **One-off decisions**: "We chose X for feature Y" unless it establishes a pattern
- **Obvious things**: Standard language features, common library usage, generic best practices
- **Stale information**: References to code that no longer exists or has been refactored
- **Redundant content**: Information already in docs/ files (reference instead of repeat)

**DO include**:
- Project-specific conventions that aren't obvious from the code
- Gotchas that have caused problems multiple times
- Patterns unique to this codebase
- Information that would save significant debugging time

A concise CLAUDE.md (~100-200 lines) is more useful than a comprehensive one. Use `cl_dream.py cleanup` periodically to remove low-value content.

## Documentation

Detailed documentation for specific topics:

| File | When to read |
|------|--------------|
| [docs/jsonl-format.md](docs/jsonl-format.md) | Working on JSONL parsing, handling new entry types, debugging empty sessions |
| [docs/formatting.md](docs/formatting.md) | Modifying markdown output, plan diff display, message batching logic |
| [docs/ollama-integration.md](docs/ollama-integration.md) | Working on summarization, changing prompts, debugging API issues |
| [docs/tui-patterns.md](docs/tui-patterns.md) | Modifying browse_transcripts.py, Rich tables, selection systems |
| [docs/web-browser.md](docs/web-browser.md) | Modifying browse_web.py, VDX components, format options UI |
| [docs/cl-dream.md](docs/cl-dream.md) | Modifying cl_dream.py, lesson extraction, two-phase architecture |
| [FRAMEWORK.md](FRAMEWORK.md) | Any VDX component work in static/ directory |
| [BUG_REPORTS.md](BUG_REPORTS.md) | Before starting work, to see known issues |
