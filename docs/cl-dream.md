# cl_dream.py - Lesson Extraction Guide

Tool for extracting lessons from Claude Code conversations and updating project documentation.

## Architecture

### Two-Phase Design

The tool uses a two-phase architecture to optimize for both cost and quality:

**Phase 1: Parallel Extraction (Sonnet)**
- Spawns subprocess calls to Claude CLI with Sonnet model
- Each conversation processed independently in parallel
- Extracts lessons using structured prompt format
- Outputs to temporary lesson files (`/tmp/cl-dream-*/lessons/*.md`)
- Configurable parallelism via `dream.max_parallel_extractions` (default: 5)

**Phase 2: Synthesis (Opus)**
- Single Opus session with clean context
- Reads all lesson files
- Synthesizes patterns across sessions
- Updates CLAUDE.md and docs/ files directly

### Why Two Phases?

Running many subagents within an Opus session causes context bloat from orchestration overhead. By extracting to files first:
- Each Sonnet subprocess gets clean context
- Opus starts fresh with only the results
- Better synthesis quality from uncluttered context
- Cheaper execution (Sonnet for mechanical extraction)

## Usage

```bash
# Single project
python cl_dream.py /path/to/project

# Multiple related projects
python cl_dream.py /path/to/frontend /path/to/backend

# Include historical paths for moved projects
python cl_dream.py /path/to/project --related /old/path/to/project

# Skip Phase 1 (reuse existing lesson files)
python cl_dream.py /path/to/project --retry

# Preview without changes
python cl_dream.py /path/to/project --dry-run
```

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
