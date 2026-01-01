# cl-pprint

A toolkit for processing Claude Code agent JSONL logs. Browse, search, summarize, and export conversation transcripts to readable markdown.

## Features

- **Format** - Convert JSONL logs to readable markdown with plan diffs, navigation links, and batched progress sections
- **Summarize** - Generate AI summaries using local Ollama
- **Browse** - Interactive TUI for searching, filtering, and batch exporting transcripts

## Installation

```bash
pip install rich prompt_toolkit requests
```

For AI summarization, install [Ollama](https://ollama.ai/) and pull a model:

```bash
ollama pull qwen3:30b-a3b-thinking-2507-q4_K_M
```

## Configuration

Copy the example config and customize:

```bash
cp config.example.json config.json
```

Configuration options:

| Key | Description |
|-----|-------------|
| `ollama.model` | Ollama model for summarization |
| `ollama.url` | Ollama API endpoint |
| `ollama.timeout` | Request timeout in seconds |
| `ollama.temperature` | Model temperature (0.0-1.0) |
| `ollama.max_tokens` | Maximum tokens to generate |
| `paths.claude_projects` | Directory containing Claude project logs |
| `paths.summary_cache` | Path to summary cache file |
| `paths.export_dir` | Default export directory |
| `project_name_skip_dirs` | Directory names to skip when parsing project names |

## Usage

### Format a single transcript

```bash
python format_jsonl.py session.jsonl output.md
```

Options:
- `--show-tools` - Include tool calls
- `--show-thinking` - Include thinking blocks
- `--show-status` - Include brief status messages
- `--exclude-timestamps` - Hide timestamps

### Generate AI summaries

```bash
# Summarize all transcripts (uses cache)
python summarize_transcripts.py

# Force re-summarize
python summarize_transcripts.py --force

# Preview without calling Ollama
python summarize_transcripts.py --dry-run
```

### Interactive browser

```bash
python browse_transcripts.py
```

Browser commands:

| Key | Action |
|-----|--------|
| `n` / `p` | Next / previous page |
| `s` | Search transcripts |
| `f` | Filter by project |
| `c` | Clear filters |
| `#` | Toggle selection (e.g., `42`) |
| `#-#` | Range select (e.g., `1-5`) |
| `a` | Select/deselect all on page |
| `v#` | View transcript details (e.g., `v42`) |
| `e` | Export selected to markdown |
| `q` | Quit |

## Output Format

Exported markdown includes:

- Session metadata (ID, branch, working directory)
- User messages with timestamps
- Assistant responses (brief messages batched as "Progress" sections)
- Plan approval/rejection with diffs between revisions
- Navigation links to skip between plan versions
- AskUserQuestion dialogs with inline answers

## Data Locations

- Claude logs: `~/.claude/projects/<project>/<session>.jsonl`
- Summary cache: `~/.claude/transcript_summaries.json`
- Exports: `./exports/<project>/`

## License

MIT
