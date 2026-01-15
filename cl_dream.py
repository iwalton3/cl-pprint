#!/usr/bin/env python3
"""
cl-dream: Help Claude Code learn from past conversations.

Like sleep consolidates human memories, this tool consolidates learnings from
coding sessions by extracting lessons and updating project documentation.

Usage:
    python cl_dream.py /path/to/project1 [/path/to/project2 ...] [options]

Workflow:
    1. Find new conversations (by mtime tracking)
    2. Generate markdown from JSONL logs
    3. Extract lessons - Opus launches parallel Sonnet subagents
    4. Synthesize - Opus directly edits CLAUDE.md using tools
    5. Cache lessons, update state
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
except ImportError:
    print("Please install rich: pip install rich")
    sys.exit(1)

import config
from format_jsonl import format_jsonl

console = Console()

# State tracking
DREAM_STATE_PATH = config.get('dream.state_file') or Path.home() / '.claude' / 'dream_state.json'
if isinstance(DREAM_STATE_PATH, str):
    DREAM_STATE_PATH = Path(DREAM_STATE_PATH).expanduser()

# Lessons cache
LESSONS_CACHE_DIR = Path.home() / '.claude' / 'dream_lessons'

# Timeouts
OPUS_TIMEOUT = config.get('dream.opus_timeout') or 1800  # 30 minutes for interactive session


# =============================================================================
# State Management
# =============================================================================

def load_state() -> dict:
    """Load dream state from disk."""
    if DREAM_STATE_PATH.exists():
        try:
            with open(DREAM_STATE_PATH, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {"version": 1, "projects": {}}
    return {"version": 1, "projects": {}}


def save_state(state: dict):
    """Save dream state to disk."""
    DREAM_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state['last_run'] = datetime.now().isoformat()
    with open(DREAM_STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2)


def mark_processed(state: dict, project_dir: str, session_id: str, mtime: float):
    """Mark a session as processed for a project."""
    if project_dir not in state['projects']:
        state['projects'][project_dir] = {
            'processed_sessions': {},
            'last_processed': None
        }

    # Handle migration from old list format
    if isinstance(state['projects'][project_dir].get('processed_sessions'), list):
        old_sessions = state['projects'][project_dir]['processed_sessions']
        state['projects'][project_dir]['processed_sessions'] = {s: 0 for s in old_sessions}

    state['projects'][project_dir]['processed_sessions'][session_id] = mtime
    state['projects'][project_dir]['last_processed'] = datetime.now().isoformat()


# =============================================================================
# Lessons Cache (for --retry)
# =============================================================================

def get_cache_key(project_dirs: list[Path]) -> str:
    """Generate a cache key from project directories."""
    paths_str = '|'.join(sorted(str(p.resolve()) for p in project_dirs))
    return hashlib.md5(paths_str.encode()).hexdigest()[:12]


def get_cache_dir(project_dirs: list[Path]) -> Path:
    """Get the cache directory for a set of projects."""
    return LESSONS_CACHE_DIR / get_cache_key(project_dirs)


def load_cached_lessons(project_dirs: list[Path]) -> list[Path] | None:
    """Load cached lessons if they exist.

    Returns list of lesson file paths, or None if no valid cache.
    """
    cache_dir = get_cache_dir(project_dirs)
    metadata_path = cache_dir / '_metadata.json'

    if not metadata_path.exists():
        return None

    try:
        with open(metadata_path) as f:
            metadata = json.load(f)

        # Verify projects match
        cached_projects = set(metadata.get('project_dirs', []))
        current_projects = set(str(p.resolve()) for p in project_dirs)
        if cached_projects != current_projects:
            return None

        # Return all lesson files
        lesson_files = list(cache_dir.glob("*.md"))
        if not lesson_files:
            return None

        console.print(f"[dim]Loaded {len(lesson_files)} cached lessons from {cache_dir}[/dim]")
        return lesson_files

    except (json.JSONDecodeError, IOError):
        return None


def save_lessons_cache(project_dirs: list[Path], lessons_dir: Path):
    """Save lessons to cache for future --retry runs."""
    cache_dir = get_cache_dir(project_dirs)

    # Clear and recreate cache dir
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    cache_dir.mkdir(parents=True)

    # Copy lesson files
    for lesson_file in lessons_dir.glob("*.md"):
        shutil.copy(lesson_file, cache_dir / lesson_file.name)

    # Save metadata
    metadata = {
        'project_dirs': [str(p.resolve()) for p in project_dirs],
        'created_at': datetime.now().isoformat(),
        'lesson_count': len(list(lessons_dir.glob("*.md")))
    }
    with open(cache_dir / '_metadata.json', 'w') as f:
        json.dump(metadata, f, indent=2)

    console.print(f"[dim]Cached lessons to {cache_dir}[/dim]")


# =============================================================================
# Conversation Discovery
# =============================================================================

def find_matching_project_dir(project_path: Path, claude_projects: Path) -> Path | None:
    """Find the Claude project directory that matches a given project path."""
    path_slug = str(project_path.resolve()).replace('/', '-').lstrip('-')

    # Direct match
    for dir_path in claude_projects.iterdir():
        if dir_path.is_dir():
            if dir_path.name.lstrip('-') == path_slug:
                return dir_path

    # Partial match using last few path segments
    project_parts = [p.lower() for p in project_path.resolve().parts[-3:]]
    for dir_path in claude_projects.iterdir():
        if dir_path.is_dir():
            dir_parts = [p.lower() for p in dir_path.name.split('-')]
            if all(p in dir_parts for p in project_parts if p):
                return dir_path

    return None


def find_new_conversations(primary_dirs: list[Path], related_dirs: list[Path],
                           state: dict) -> list[tuple[Path, float, Path]]:
    """Find all conversation files that need processing.

    Returns:
        List of (jsonl_path, mtime, source_project) tuples
    """
    conversations = []
    claude_projects = Path.home() / '.claude' / 'projects'

    if not claude_projects.exists():
        console.print(f"[yellow]Warning: Claude projects directory not found: {claude_projects}[/yellow]")
        return []

    # Collect processed sessions from all primary projects
    all_processed = {}
    for proj in primary_dirs:
        project_key = str(proj.resolve())
        project_state = state.get('projects', {}).get(project_key, {})
        processed = project_state.get('processed_sessions', {})
        if isinstance(processed, list):
            processed = {s: 0 for s in processed}
        all_processed.update(processed)

    # Find conversations from all directories
    all_dirs = primary_dirs + related_dirs
    seen_sessions = set()

    for proj in all_dirs:
        claude_dir = find_matching_project_dir(proj, claude_projects)
        if claude_dir:
            console.print(f"[dim]Found Claude project dir: {claude_dir.name}[/dim]")
            for jsonl in claude_dir.glob("*.jsonl"):
                if jsonl.name.startswith("agent-"):
                    continue

                session_id = jsonl.stem
                if session_id in seen_sessions:
                    continue

                current_mtime = jsonl.stat().st_mtime
                last_processed_mtime = all_processed.get(session_id)

                if last_processed_mtime is None or current_mtime > last_processed_mtime:
                    conversations.append((jsonl, current_mtime, proj))
                    seen_sessions.add(session_id)
        else:
            console.print(f"[yellow]Warning: No Claude project dir found for {proj}[/yellow]")

    conversations.sort(key=lambda x: x[1])
    return conversations


# =============================================================================
# Temporary Directory Management
# =============================================================================

@contextmanager
def temp_dream_dir(keep: bool = False):
    """Create temporary directory for dream processing.

    Args:
        keep: If True, don't delete temp dir (for debugging)
    """
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    temp_base = Path(tempfile.gettempdir()) / f"cl-dream-{timestamp}"
    temp_base.mkdir(parents=True, exist_ok=True)

    (temp_base / 'conversations').mkdir()
    (temp_base / 'conversations_full').mkdir()
    (temp_base / 'lessons').mkdir()

    try:
        yield temp_base
    finally:
        if not keep:
            shutil.rmtree(temp_base, ignore_errors=True)


# =============================================================================
# Markdown Generation
# =============================================================================

def generate_condensed_markdown(jsonl_path: Path, output_path: Path):
    """Generate condensed markdown focusing on dialogue and Explore agents."""
    import io
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    try:
        format_jsonl(
            str(jsonl_path),
            str(output_path),
            show_tools=False,
            show_thinking=False,
            show_timestamps=False,
            show_status=False,
            show_explore_full=True,
            truncate_tool_calls=True,
            truncate_tool_results=True,
            exclude_edit_tools=True,
            exclude_view_tools=True,
        )
    finally:
        sys.stderr = old_stderr


def generate_full_markdown(jsonl_path: Path, output_path: Path):
    """Generate full markdown with all details."""
    import io
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    try:
        format_jsonl(
            str(jsonl_path),
            str(output_path),
            show_tools=True,
            show_thinking=True,
            show_timestamps=True,
            show_status=True,
            truncate_tool_calls=False,
            truncate_tool_results=False,
        )
    finally:
        sys.stderr = old_stderr


# =============================================================================
# Git Integration
# =============================================================================

def is_git_tracked(file_path: Path) -> bool:
    """Check if a file is tracked by git."""
    try:
        result = subprocess.run(
            ['git', 'ls-files', '--error-unmatch', str(file_path)],
            cwd=file_path.parent,
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def smart_backup(file_path: Path) -> Path | None:
    """Backup file only if not git-tracked.

    Returns backup path if backup was created, None otherwise.
    """
    if not file_path.exists():
        return None

    if is_git_tracked(file_path):
        console.print(f"[dim]{file_path.name} is git-tracked, skipping backup[/dim]")
        return None

    backup_dir = file_path.parent / '.claude'
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f"{file_path.name}.backup.{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy(file_path, backup_path)
    console.print(f"[dim]Backed up to: {backup_path}[/dim]")
    return backup_path


# =============================================================================
# Opus Interactive Session
# =============================================================================

def build_opus_system_prompt(primary_dirs: list[Path], related_dirs: list[Path],
                              temp_dir: Path, use_cached_lessons: bool) -> str:
    """Build the system prompt for Opus."""

    primary_list = '\n'.join(f"  - {p}" for p in primary_dirs)
    related_list = '\n'.join(f"  - {p}" for p in related_dirs) if related_dirs else "  (none)"

    lesson_extraction_instructions = ""
    if not use_cached_lessons:
        lesson_extraction_instructions = f"""
## Phase 1: Lesson Extraction

You have conversation transcripts in: {temp_dir}/conversations/

First, list the conversation files using Glob to see what's available.

Then, for each conversation file, launch a Task subagent to extract lessons:
- Use subagent_type="general-purpose" and model="sonnet"
- Launch multiple subagents IN PARALLEL for efficiency (use a single message with multiple Task tool calls)

For each subagent, construct a prompt like this (replace CONVERSATION_PATH and OUTPUT_PATH with actual paths):

---
Read the conversation transcript and extract lessons learned.

Conversation file: CONVERSATION_PATH

Output a markdown document with these sections:

## Mistakes Made
Errors that were caught and corrected during the session.
- What went wrong
- How it was fixed

## Unfixed Bugs
Issues identified but NOT resolved by the end of the session.
- What the bug is
- Why it wasn't fixed (ran out of time, deferred, etc.)
- Potential fix if known

## Solutions Discovered
Successful approaches that worked well.
- The problem
- The solution
- Why it works

## Patterns Identified
Useful coding patterns, conventions, or architectural decisions.
- Pattern name/description
- When to use it
- Example if applicable

## Gotchas
Non-obvious behaviors, edge cases, or quirks discovered.
- The gotcha
- When it's relevant

Be specific and actionable. Include file paths and code snippets when relevant.
Skip trivial items. Focus on lessons that would genuinely help future sessions.

Write your output to: OUTPUT_PATH
---

Output paths should be: {temp_dir}/lessons/{{conversation-filename}}.md

IMPORTANT: Wait for ALL subagents to complete before proceeding to Phase 2.
You can check completion by reading the lessons directory to verify files were created.
"""

    return f"""You are cl-dream, helping Claude Code learn from past conversations.

## Project Configuration

**Primary projects** (CLAUDE.md will be updated):
{primary_list}

**Related projects** (conversations included, docs NOT updated):
{related_list}

**Temp directory**: {temp_dir}
{lesson_extraction_instructions}
## Phase 2: Documentation Update

After lessons are extracted (or if using cached lessons from {temp_dir}/lessons/):

1. Read all lesson files from {temp_dir}/lessons/
2. For EACH primary project:
   a. Read its current CLAUDE.md (if exists)
   b. Read its codebase structure (use Glob/Read)
   c. Synthesize relevant lessons into documentation updates
   d. Use Write or Edit tools to update CLAUDE.md directly
   e. Create/update docs/ folder with detailed documentation
   f. If there are "Unfixed Bugs" in any lessons, create/update BUG_REPORTS.md

**Multi-Project Rules:**
- Projects are inter-related and share context
- Lessons from one project may inform understanding of another
- BUT documentation updates should be PROJECT-SPECIFIC:
  - Each CLAUDE.md focuses on its own codebase
  - Cross-cutting concerns go where they're most relevant
  - Avoid duplicating lessons across multiple CLAUDE.md files
  - Reference other project docs if needed (e.g., "See backend/CLAUDE.md for API details")

**Rules for CLAUDE.md updates:**
- Keep it focused and scannable (~100-200 lines ideal)
- Use headers, bullets, code examples
- Most important: frequent mistakes, key concepts, conventions
- New lessons are primarily ADDITIONS to existing rules
- Existing rules can be ENHANCED (clarified, expanded with examples)
- Existing rules can be REMOVED only with very high confidence that they are:
  * Factually incorrect
  * Outdated (code they reference no longer exists)
  * Contradicted by multiple lessons

**docs/ folder:**
Create the docs/ folder if it doesn't exist. Use it for detailed documentation that doesn't fit in CLAUDE.md:
- Create new docs when a topic has enough depth to warrant its own file
- Update existing docs when new lessons add to that topic
- Revamp/reorganize docs when they become outdated or unclear
- Good candidates for docs/:
  * Architecture overviews (architecture.md)
  * API documentation (api.md)
  * Complex subsystem guides (e.g., parsing.md, state-management.md)
  * Troubleshooting guides (troubleshooting.md)
  * Setup/configuration guides (setup.md)
- Name files descriptively in lowercase-kebab-case.md
- Each doc should be self-contained but can reference others
- Remove docs that are completely obsolete

**docs/ index in CLAUDE.md:**
At the end of CLAUDE.md, include a "## Documentation" section that indexes all docs/ files:
- List each doc file with a brief description
- Include guidance on WHEN to read each doc into context, e.g.:
  * "Read docs/parsing.md when working on the JSONL parser or format_jsonl.py"
  * "Read docs/troubleshooting.md when debugging unexpected behavior"
  * "Read docs/architecture.md before making structural changes"
- This helps future Claude sessions know which docs to load for specific tasks
- Keep descriptions concise (one line each)

**BUG_REPORTS.md:**
If any lessons contain "Unfixed Bugs" sections, create or update BUG_REPORTS.md in the project root:
- Collect all unfixed bugs from the lessons
- Group by severity/area if possible
- Include: description, context, potential fix (if known), which conversation it was found in
- If a bug report already exists and the bug appears to be fixed (check codebase), remove it
- Format as a checklist so bugs can be tracked

## Final Output

After updating all files, print a summary:
- Number of lessons processed
- Changes made to each project's CLAUDE.md
- New/updated docs in docs/ folder
- Number of unfixed bugs added to BUG_REPORTS.md
- Any rules that were removed/modified (with reasons)
"""


def run_opus_interactive(primary_dirs: list[Path], related_dirs: list[Path],
                         temp_dir: Path, use_cached_lessons: bool,
                         dry_run: bool = False) -> bool:
    """Run Opus interactively with tool access and streaming output.

    Opus will:
    1. (unless cached) Launch parallel Task subagents to extract lessons
    2. Directly edit CLAUDE.md using Write/Edit tools
    3. Stream output to terminal
    """
    system_prompt = build_opus_system_prompt(primary_dirs, related_dirs, temp_dir, use_cached_lessons)

    # Build user prompt - start with text that won't be interpreted as a path
    if use_cached_lessons:
        user_prompt = f"""Please proceed with the cl-dream workflow.

Cached lessons are available at: {temp_dir}/lessons/

Skip Phase 1 (lesson extraction) and proceed directly to Phase 2: Documentation Update.

Read the lesson files and update CLAUDE.md for each primary project."""
    else:
        user_prompt = f"""Please proceed with the cl-dream workflow.

Conversation transcripts are located at: {temp_dir}/conversations/

Begin Phase 1: Launch parallel Task subagents to extract lessons from each conversation.
Then proceed to Phase 2: Update CLAUDE.md for each primary project."""

    if dry_run:
        console.print("\n[bold cyan]--- DRY RUN ---[/bold cyan]")
        console.print(f"Would run Opus with access to:")
        console.print(f"  Primary dirs: {[str(p) for p in primary_dirs]}")
        console.print(f"  Related dirs: {[str(p) for p in related_dirs]}")
        console.print(f"  Temp dir: {temp_dir}")
        console.print(f"  Use cached lessons: {use_cached_lessons}")
        console.print(f"\n[dim]System prompt preview:[/dim]")
        console.print(system_prompt[:1000] + "..." if len(system_prompt) > 1000 else system_prompt)
        return True

    # Build --add-dir flags for all directories Opus needs
    all_dirs = list(primary_dirs) + list(related_dirs) + [temp_dir]
    add_dir_args = []
    for d in all_dirs:
        add_dir_args.extend(['--add-dir', str(d)])

    cmd = [
        'claude',
        '--print',
        '--model', 'opus',
        '--system-prompt', system_prompt,
        '--allowedTools', 'Read,Write,Edit,Glob,Grep,Task',
        '--output-format', 'stream-json',  # Stream output as it happens
        '--verbose',
        *add_dir_args,
    ]

    console.print("\n[bold]Starting Opus session...[/bold]")
    console.print("[dim]Opus will extract lessons and update documentation.[/dim]\n")

    try:
        # Use Popen for real-time output streaming
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # Send prompt and close stdin
        process.stdin.write(user_prompt)
        process.stdin.close()

        # Stream and parse JSON output for display
        while True:
            if process.stdout:
                line = process.stdout.readline()
                if line:
                    try:
                        data = json.loads(line)
                        msg_type = data.get('type', '')

                        # Handle assistant messages (text and tool use)
                        if msg_type == 'assistant' and 'message' in data:
                            content = data['message'].get('content', [])
                            for item in content:
                                if isinstance(item, dict):
                                    if item.get('type') == 'text':
                                        text = item.get('text', '')
                                        if text:
                                            console.print(text)
                                    elif item.get('type') == 'tool_use':
                                        tool_name = item.get('name', 'unknown')
                                        console.print(f"[dim]>>> Using tool: {tool_name}[/dim]")
                                elif isinstance(item, str):
                                    console.print(item)

                        # Handle result (final output)
                        elif msg_type == 'result':
                            if data.get('subtype') == 'success':
                                console.print("\n[green]Session completed successfully[/green]")
                            else:
                                console.print(f"\n[yellow]Session ended: {data.get('subtype', 'unknown')}[/yellow]")

                    except json.JSONDecodeError:
                        # Not JSON, print as-is
                        console.print(line.rstrip())
                elif process.poll() is not None:
                    break
            else:
                break

        # Print any stderr
        if process.stderr:
            stderr_output = process.stderr.read()
            if stderr_output:
                console.print(f"[dim]{stderr_output}[/dim]")

        return process.returncode == 0
    except subprocess.TimeoutExpired:
        process.kill()
        console.print(f"[red]Opus session timed out after {OPUS_TIMEOUT}s[/red]")
        return False
    except FileNotFoundError:
        console.print("[red]Claude CLI not found. Is it installed and in PATH?[/red]")
        return False


# =============================================================================
# Main Workflow
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Help Claude Code learn from past conversations',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Single project
    python cl_dream.py /path/to/my-project

    # Multiple related projects (both get docs updated)
    python cl_dream.py /path/to/frontend /path/to/backend

    # Include conversations from shared lib without updating its docs
    python cl_dream.py /path/to/frontend --related /path/to/shared-lib

    # Retry synthesis with cached lessons
    python cl_dream.py /path/to/project --retry

    # Preview what would be done
    python cl_dream.py . --dry-run
        """
    )
    parser.add_argument('project_dirs', nargs='+', type=Path,
                        help='Project directories to update docs for')
    parser.add_argument('--related', nargs='*', type=Path, default=[],
                        help='Related projects (conversations included, docs NOT updated)')
    parser.add_argument('--force', action='store_true',
                        help='Reprocess all conversations (ignore state)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be done without making changes')
    parser.add_argument('--retry', action='store_true',
                        help='Skip lesson extraction, reuse cached lessons')
    parser.add_argument('--keep-temp', action='store_true',
                        help='Keep temp directory for debugging')

    args = parser.parse_args()

    # Validate project directories
    primary_dirs = []
    for p in args.project_dirs:
        resolved = p.resolve()
        if not resolved.exists():
            console.print(f"[red]Error: Project directory does not exist: {resolved}[/red]")
            sys.exit(1)
        primary_dirs.append(resolved)

    related_dirs = [p.resolve() for p in args.related if p.resolve().exists()]

    console.print(f"[bold]cl-dream[/bold] - Learning from past conversations\n")
    console.print(f"Primary projects: {[str(p) for p in primary_dirs]}")
    if related_dirs:
        console.print(f"Related projects: {[str(p) for p in related_dirs]}")
    console.print()

    # Check for --retry with cached lessons
    use_cached_lessons = False
    if args.retry:
        cached = load_cached_lessons(primary_dirs)
        if cached is None:
            console.print("[red]Error: --retry specified but no cached lessons found.[/red]")
            console.print("[yellow]Run without --retry first to generate lessons.[/yellow]")
            sys.exit(1)
        use_cached_lessons = True
        console.print(f"[green]Using {len(cached)} cached lessons (--retry mode)[/green]\n")

    # Load state
    state = {} if args.force else load_state()
    if args.force:
        console.print("[yellow]Force mode: reprocessing all conversations[/yellow]\n")

    # Find new conversations (skip if using cached lessons)
    conversation_data = []
    if not use_cached_lessons:
        conversation_data = find_new_conversations(primary_dirs, related_dirs, state)

        if not conversation_data:
            console.print("[green]No new conversations to process[/green]")
            return

        console.print(f"Found [bold]{len(conversation_data)}[/bold] conversations to process\n")

        if args.dry_run:
            console.print("[cyan]DRY RUN - would process:[/cyan]")
            for conv, mtime, source in conversation_data:
                mtime_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')
                console.print(f"  - {conv.name} (modified: {mtime_str}, from: {source.name})")
            console.print()

    with temp_dream_dir(keep=args.keep_temp) as temp_dir:
        console.print(f"[dim]Temp directory: {temp_dir}[/dim]\n")

        # Phase 1: Generate markdown (skip if using cached lessons)
        if not use_cached_lessons:
            console.print("[bold]Phase 1: Generating markdown...[/bold]")

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console
            ) as progress:
                task = progress.add_task("Converting", total=len(conversation_data))

                for conv, mtime, source in conversation_data:
                    condensed_path = temp_dir / 'conversations' / f"{conv.stem}.md"
                    full_path = temp_dir / 'conversations_full' / f"{conv.stem}.md"

                    generate_condensed_markdown(conv, condensed_path)
                    generate_full_markdown(conv, full_path)

                    progress.update(task, advance=1, description=f"Converting {conv.stem[:20]}...")

            console.print(f"  Generated {len(conversation_data)} markdown files\n")
        else:
            # Copy cached lessons to temp dir
            cache_dir = get_cache_dir(primary_dirs)
            for lesson_file in cache_dir.glob("*.md"):
                shutil.copy(lesson_file, temp_dir / 'lessons' / lesson_file.name)
            console.print(f"[dim]Copied cached lessons to temp directory[/dim]\n")

        # Backup CLAUDE.md files (smart backup - skip if git-tracked)
        if not args.dry_run:
            for proj in primary_dirs:
                claude_md = proj / 'CLAUDE.md'
                if claude_md.exists():
                    smart_backup(claude_md)

        # Phase 2 & 3: Opus extracts lessons and updates docs
        console.print("[bold]Running Opus for lesson extraction and documentation update...[/bold]")

        success = run_opus_interactive(
            primary_dirs=primary_dirs,
            related_dirs=related_dirs,
            temp_dir=temp_dir,
            use_cached_lessons=use_cached_lessons,
            dry_run=args.dry_run
        )

        if success and not args.dry_run:
            # Cache lessons for future --retry (unless already using cached)
            if not use_cached_lessons:
                lessons_dir = temp_dir / 'lessons'
                if list(lessons_dir.glob("*.md")):
                    save_lessons_cache(primary_dirs, lessons_dir)

            # Update state for all primary projects
            for proj in primary_dirs:
                project_key = str(proj)
                for conv, mtime, source in conversation_data:
                    mark_processed(state, project_key, conv.stem, mtime)

            save_state(state)
            console.print(f"\n[green]Done! State saved to: {DREAM_STATE_PATH}[/green]")

        elif not success and not args.dry_run:
            console.print("\n[red]Opus session failed or was interrupted[/red]")

        if args.keep_temp:
            console.print(f"\n[yellow]Temp directory kept: {temp_dir}[/yellow]")


if __name__ == '__main__':
    main()
