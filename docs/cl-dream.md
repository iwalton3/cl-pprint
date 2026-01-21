# cl_dream.py - Lesson Extraction Guide

Tool for extracting lessons from Claude Code conversations and updating project documentation.

## Architecture

### Multi-Phase Design

The tool uses a multi-phase architecture to optimize for both cost and quality:

**Phase 1: Parallel Extraction (Sonnet)**
- Spawns subprocess calls to Claude CLI with Sonnet model
- Each conversation processed independently in parallel
- Extracts lessons using structured prompt format
- Outputs to temporary lesson files (`/tmp/cl-dream-*/lessons/*.md`)
- Configurable parallelism via `dream.max_parallel_extractions` (default: 5)

**Phase 1.5: Exploration Analysis (Algorithmic + Sonnet)**
- Analyzes ALL historical sessions (not just new ones)
- Extracts tool call patterns: Read, Glob, Grep, Task(Explore)
- Generates file access heatmap (which files are read most often)
- Aggregates Explore agent prompts (what questions Claude keeps asking)
- Sonnet summarizes explore prompts into themes and knowledge gaps
- Output: `exploration_analysis.md` fed to synthesis phase

**Phase 2: Synthesis (Opus)**
- Single Opus session with clean context
- Reads exploration analysis FIRST (big picture of missing knowledge)
- Reads all lesson files
- Synthesizes patterns across sessions
- Adds **Key Locations** section based on file heatmap
- Adds documentation for frequently explored themes
- Updates CLAUDE.md and docs/ files directly

**Phase 3: Summary Generation (Haiku)**
- Generates conversation summaries for the browser
- Uses Haiku for fast/cheap processing
- Writes to `~/.claude/transcript_summaries.json`
- Compatible with `browse_transcripts.py` and `browse_web.py`

**Phase 4: Cleanup (Opus, optional)**
- Reviews CLAUDE.md for stale/low-value content
- Uses git history to understand recent changes
- Removes one-off decisions, obvious things, stale references
- Run with `--cleanup` flag or standalone via `cl_dream.py cleanup`

### Why Multi-Phase?

Running many subagents within an Opus session causes context bloat from orchestration overhead. By extracting to files first:
- Each Sonnet subprocess gets clean context
- Opus starts fresh with only the results
- Better synthesis quality from uncluttered context
- Cheaper execution (Sonnet for mechanical extraction)

## Usage

```bash
# Single project
python cl_dream.py run /path/to/project

# Multiple related projects
python cl_dream.py run /path/to/frontend /path/to/backend

# Include historical paths for moved projects
python cl_dream.py run /path/to/project --related /old/path/to/project

# Skip Phase 1 (reuse existing lesson files)
python cl_dream.py run /path/to/project --retry

# Preview without changes
python cl_dream.py run /path/to/project --dry-run

# Auto mode: run on all previously-processed projects
python cl_dream.py auto

# Auto mode with cleanup
python cl_dream.py auto --cleanup

# Cleanup only (no lesson extraction)
python cl_dream.py cleanup /path/to/project

# Skip summary generation
python cl_dream.py run /path/to/project --skip-summaries
```

### Auto Mode

The `auto` subcommand discovers projects from `~/.claude/dream_state.json` where cl_dream was previously run and processes them incrementally:

- Only processes projects that still exist on disk
- Skips projects that have been moved or deleted
- Runs the full workflow on each project sequentially
- Useful for periodic maintenance: `cl_dream.py auto --cleanup`

### Cleanup Mode

The `cleanup` subcommand runs only Phase 4 on a project:

- Reviews CLAUDE.md content quality
- Checks git history for context
- Removes stale, one-off, or obvious content
- Can be run standalone or as part of full workflow with `--cleanup`

## Configuration

Settings in `config.json`:

```json
{
  "dream": {
    "max_parallel_extractions": 5,
    "extraction_timeout": 300,
    "opus_timeout": 600
  }
}
```

## Lesson File Format

Each extracted lesson follows this structure:

```markdown
## Session Summary
- **Task**: What the user asked for
- **Key files modified**: Files changed in the session
- **Outcome**: Completed/In Progress/Abandoned

## Session Type
Development | Bug Investigation | PR Review | ...

## Mistakes Made
(List of errors made during implementation)

## Unfixed Bugs
(Bugs discovered but not fixed in the session)

## Solutions Discovered
(Problems solved and why the solution works)

## Patterns Identified
(Reusable patterns for similar problems)

## Gotchas
(Non-obvious issues and when they're relevant)

## Documentation Impact
(What existing docs might need updates)
```

## Empty Session Filtering

The tool filters low-value sessions before processing:

1. **File-history-only**: Sessions with no messages
2. **Summary-only**: Branch parent files pointing to leaf conversations
3. **Trivial prompts**: Sessions with <100 chars of user content

The `has_conversation_content(path, min_user_chars=100)` function checks both user message length AND assistant response presence.

## Project Directory Matching

Claude Code converts paths to directory names:
- `/` characters become `-`
- `.` characters become `-`
- Example: `/working/JFD.API` → `-working-JFD-API`

The tool automatically includes subdirectory projects (e.g., `-working-project-e2e-tests` when processing `-working-project`).

## Batching for Parallel Subagents

When Phase 2 launches subagents for validation, it processes them in batches of 3-5 to prevent:
- Overwhelming the parent agent with async completion notifications
- Race conditions where synthesis starts before extraction completes
- Message interleaving that confuses decision-making

## Error Handling

- Subprocess timeout: 5 minutes per conversation extraction
- Failed extractions are logged but don't stop the pipeline
- Use `--retry` to skip Phase 1 when iterating on Phase 2 prompts

## Exploration Analysis

The exploration analysis phase examines ALL historical sessions to identify patterns in what Claude explores:

### File Access Heatmap
Tracks which files are most frequently read via the Read tool. Files with >30% access rate should be documented in the Key Locations section of CLAUDE.md.

### Explore Agent Prompts
Aggregates all prompts sent to Task subagents with `subagent_type="Explore"`. These represent questions Claude needed answered to understand the codebase - recurring themes indicate documentation gaps.

### Frequency-Based Documentation Placement
- **5+ occurrences**: Add to CLAUDE.md (high-frequency need, must be immediately visible)
- **2-4 occurrences**: Add to docs/ (moderate frequency, warrants detailed explanation)
- **1 occurrence**: Skip (one-off, not worth documenting)

### Key Locations Section
The synthesis phase adds a "Key Locations" table to CLAUDE.md:

```markdown
## Key Locations

| To find... | Look in... |
|------------|------------|
| JSONL parsing | format_jsonl.py |
| Main workflow | cl_dream.py → run_dream_workflow() |
| Config loading | config.py (get, get_path helpers) |
```

This prevents expensive Explore() calls by giving Claude a quick reference for where things are.

### Tool Call Extraction
The `extract_tool_calls()` function parses JSONL to find:
- `Read` tool calls → file paths accessed
- `Glob` tool calls → file patterns searched
- `Grep` tool calls → content patterns searched
- `Task` tool calls with `subagent_type="Explore"` → questions asked

Note: Subagent internal tool calls aren't logged in the parent session, so we can't see what files an Explore agent read - only the prompt that was sent to it.
