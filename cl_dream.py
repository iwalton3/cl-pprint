#!/usr/bin/env python3
"""
cl-dream: Help Claude Code learn from past conversations.

Like sleep consolidates human memories, this tool consolidates learnings from
coding sessions by extracting lessons and updating project documentation.

Usage:
    python cl_dream.py /path/to/project1 [/path/to/project2 ...] [options]
    python cl_dream.py auto [options]  # Run on all previously-processed projects
    python cl_dream.py cleanup /path/to/project [options]  # Clean up CLAUDE.md

Workflow:
    1. Find new conversations (by mtime tracking)
    2. Generate markdown from JSONL logs
    3. Extract lessons - parallel Sonnet subprocesses
    4. Synthesize - Opus directly edits CLAUDE.md using tools
    5. Generate conversation summaries for browser
    6. (Optional) Clean up CLAUDE.md - remove stale/one-off content
    7. Cache lessons, update state
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# Summary cache (same as summarize_transcripts.py uses)
SUMMARY_CACHE_PATH = config.get_path('summary_cache') or Path.home() / '.claude' / 'transcript_summaries.json'

# Timeouts
OPUS_TIMEOUT = config.get('dream.opus_timeout') or 1800  # 30 minutes for interactive session
EXTRACTION_TIMEOUT = config.get('dream.extraction_timeout') or 300  # 5 minutes per conversation

# Parallelism
MAX_PARALLEL_EXTRACTIONS = config.get('dream.max_parallel_extractions') or 5


# =============================================================================
# State Management
# =============================================================================

def load_state() -> dict:
    """Load dream state from disk."""
    if DREAM_STATE_PATH.exists():
        try:
            with open(DREAM_STATE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {"version": 1, "projects": {}}
    return {"version": 1, "projects": {}}


def save_state(state: dict):
    """Save dream state to disk."""
    DREAM_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state['last_run'] = datetime.now().isoformat()
    with open(DREAM_STATE_PATH, 'w', encoding='utf-8') as f:
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


def discover_auto_projects(state: dict) -> list[Path]:
    """Discover projects where cl_dream was previously run.

    Returns only projects that still exist on disk.
    """
    projects = []
    for project_path_str in state.get('projects', {}).keys():
        project_path = Path(project_path_str)
        if project_path.exists() and project_path.is_dir():
            projects.append(project_path)
        else:
            console.print(f"[dim]Skipping non-existent: {project_path_str}[/dim]")
    return projects


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
        with open(metadata_path, encoding='utf-8') as f:
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
    with open(cache_dir / '_metadata.json', 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2)

    console.print(f"[dim]Cached lessons to {cache_dir}[/dim]")


# =============================================================================
# Conversation Discovery
# =============================================================================

def has_conversation_content(jsonl_path: Path, min_user_chars: int = 100) -> bool:
    """Check if a session has meaningful conversation content.

    Filters out:
    - Sessions with only file-history-snapshot entries (no actual messages)
    - Sessions with only summary entries (branch parent files)
    - Trivial sessions (user content below min_user_chars threshold)

    Args:
        jsonl_path: Path to the JSONL file
        min_user_chars: Minimum total characters of user content to be considered meaningful

    Returns:
        True if session has meaningful content worth processing
    """
    total_user_chars = 0
    has_assistant_content = False

    try:
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    entry_type = entry.get('type')

                    # Check for user messages
                    if entry_type == 'user':
                        msg = entry.get('message', {})
                        content = msg.get('content', '')
                        if isinstance(content, str):
                            total_user_chars += len(content)
                        elif isinstance(content, list):
                            for item in content:
                                if isinstance(item, dict) and item.get('type') == 'text':
                                    total_user_chars += len(item.get('text', ''))

                    # Check for assistant messages with actual content
                    elif entry_type == 'assistant':
                        msg = entry.get('message', {})
                        content = msg.get('content', [])
                        if isinstance(content, list):
                            for item in content:
                                if isinstance(item, dict):
                                    if item.get('type') == 'text' and item.get('text'):
                                        has_assistant_content = True
                                    elif item.get('type') == 'tool_use':
                                        has_assistant_content = True

                except json.JSONDecodeError:
                    continue

    except (IOError, OSError):
        return False

    # Must have both user content above threshold AND assistant response
    return total_user_chars >= min_user_chars and has_assistant_content


def find_matching_project_dirs(project_path: Path, claude_projects: Path) -> list[Path]:
    """Find Claude project directories that match a given project path.

    Returns all matching directories including subdirectory projects.
    Claude Code converts path separators AND dots to dashes in directory names.
    """
    # Claude Code converts both '/' and '.' to '-' in directory names
    path_slug = str(project_path.resolve()).replace('/', '-').replace('.', '-').lstrip('-')

    matches = []

    for dir_path in claude_projects.iterdir():
        if not dir_path.is_dir():
            continue

        dir_name = dir_path.name.lstrip('-')

        # Exact match
        if dir_name == path_slug:
            matches.append(dir_path)
        # Subdirectory match (e.g., -working-JFD-API-TestDataScripts for /working/JFD.API)
        elif dir_name.startswith(path_slug + '-'):
            matches.append(dir_path)

    # If no direct matches, try partial match using path segments
    if not matches:
        # Convert dots to dashes in path parts to match Claude's conversion
        project_parts = [p.lower().replace('.', '-') for p in project_path.resolve().parts[-3:] if p and p != '/']
        for dir_path in claude_projects.iterdir():
            if dir_path.is_dir():
                dir_parts = [p.lower() for p in dir_path.name.lstrip('-').split('-')]
                if all(p in dir_parts for p in project_parts):
                    matches.append(dir_path)

    return matches


def find_new_conversations(primary_dirs: list[Path], related_dirs: list[Path],
                           state: dict) -> list[tuple[Path, float, Path]]:
    """Find all conversation files that need processing.

    Returns:
        List of (jsonl_path, mtime, source_project) tuples
    """
    conversations = []
    claude_projects = Path.home() / '.claude' / 'projects'
    skipped_empty = 0

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
        claude_dirs = find_matching_project_dirs(proj, claude_projects)
        if claude_dirs:
            for claude_dir in claude_dirs:
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
                        # Filter out empty/trivial sessions
                        if not has_conversation_content(jsonl):
                            skipped_empty += 1
                            seen_sessions.add(session_id)
                            continue

                        conversations.append((jsonl, current_mtime, proj))
                        seen_sessions.add(session_id)
        else:
            console.print(f"[yellow]Warning: No Claude project dir found for {proj}[/yellow]")

    if skipped_empty > 0:
        console.print(f"[dim]Skipped {skipped_empty} empty/trivial sessions[/dim]")

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
# Parallel Lesson Extraction
# =============================================================================

LESSON_EXTRACTION_PROMPT = """Read the conversation transcript and extract lessons learned.

Output a markdown document with these sections:

## Session Summary
Brief context about what was accomplished in this session:
- **Task**: One-line description of what was being worked on
- **Key files modified**: List 3-5 most important files that were created/edited (look for Write/Edit tool calls)
- **Outcome**: Completed / In progress / Blocked / Abandoned

This summary helps contextualize the lessons and identify which documentation might need updates.

## Session Type
Indicate the session type:
- `Development` - Normal coding/implementation session
- `PR Review` - Reviewing a pull request (look for: "gh pr", "PR #", "review", "pull request")
- `Bug Investigation` - Investigating/debugging an issue
- `Research` - Exploring/understanding code without changes

## Mistakes Made
Errors that were caught and corrected during the session.
- What went wrong
- How it was fixed
- **File path** where the mistake occurred (if applicable)
- **Impact**: How much time was wasted? (minor / significant / major)

## Unfixed Bugs
Issues identified but NOT resolved by the end of the session.
- What the bug is
- **File path and function/line** where the bug exists
- Why it wasn't fixed (ran out of time, deferred, etc.)
- Potential fix if known

If this appears to be a PR review session, add at the top:
"⚠️ UNMERGED CODE WARNING: These bugs were identified during a PR review. The code may not have been merged. Verify file paths exist before acting on these."

## Solutions Discovered
Successful approaches that worked well.
- The problem
- The solution (with **file path** if it's code-specific)
- Why it works

## Patterns Identified
Useful coding patterns, conventions, or architectural decisions.
- Pattern name/description
- When to use it
- Example if applicable
- **Scope**: Is this project-specific or a general pattern?

## Gotchas
Non-obvious behaviors, edge cases, or quirks discovered.
- The gotcha
- When it's relevant
- **Scope**: Is this specific to this project/framework or broadly applicable?

## Documentation Impact
Note if this session changes how something works in a way that might affect existing documentation:
- What behavior or API changed?
- What existing documentation might be affected? (guess based on topic)
- Any new concepts introduced that should be documented?

Write "None" if the session was routine work that doesn't affect documentation.

**IMPORTANT - Distinguish Discussed vs Implemented:**
- Mark patterns that were DISCUSSED but may not have been IMPLEMENTED with "(discussed)"
- Mark patterns that were CLEARLY IMPLEMENTED (you see the actual code written) with "(implemented)"
- For PR reviews, assume code was DISCUSSED unless you see explicit merge confirmation
- Example: "ApiError factory methods (discussed)" vs "FileUploadFilterProvider (implemented)"

**Quality Guidelines:**
- Be specific and actionable - vague lessons aren't useful
- Include file paths and code snippets when relevant
- Skip trivial items - focus on lessons that would genuinely save time in future sessions
- Prefer concrete examples over abstract descriptions
- Note the impact: Did this bug waste hours? Is this pattern used everywhere?
- If a section has no items, write "None" for that section."""


def extract_single_lesson(conversation_path: Path, output_path: Path) -> tuple[Path, bool, str]:
    """Extract lessons from a single conversation using Sonnet.

    Returns (conversation_path, success, error_message)
    """
    try:
        # Read the conversation content
        conversation_content = conversation_path.read_text()

        # Build the prompt
        prompt = f"{LESSON_EXTRACTION_PROMPT}\n\n---\n\nConversation transcript:\n\n{conversation_content}"

        cmd = [
            config.get_claude_cli(),
            '--print',
            '--model', 'sonnet',
            '--output-format', 'text',
            '--no-session-persistence',  # Don't create history entries for lesson extraction
        ]

        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=EXTRACTION_TIMEOUT,
        )

        if result.returncode == 0 and result.stdout.strip():
            output_path.write_text(result.stdout)
            return (conversation_path, True, "")
        else:
            error = result.stderr or "Empty output"
            return (conversation_path, False, error)

    except subprocess.TimeoutExpired:
        return (conversation_path, False, f"Timeout after {EXTRACTION_TIMEOUT}s")
    except Exception as e:
        return (conversation_path, False, str(e))


def extract_lessons_parallel(conversations_dir: Path, lessons_dir: Path,
                              max_workers: int = None) -> tuple[int, int]:
    """Extract lessons from all conversations in parallel.

    Args:
        conversations_dir: Directory containing conversation markdown files
        lessons_dir: Directory to write lesson files to
        max_workers: Max parallel extractions (default: MAX_PARALLEL_EXTRACTIONS)

    Returns:
        (success_count, failure_count)
    """
    if max_workers is None:
        max_workers = MAX_PARALLEL_EXTRACTIONS

    conversation_files = list(conversations_dir.glob("*.md"))
    if not conversation_files:
        console.print("[yellow]No conversation files found[/yellow]")
        return (0, 0)

    success_count = 0
    failure_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task("Extracting lessons", total=len(conversation_files))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all extraction tasks
            futures = {}
            for conv_path in conversation_files:
                output_path = lessons_dir / conv_path.name
                future = executor.submit(extract_single_lesson, conv_path, output_path)
                futures[future] = conv_path

            # Process results as they complete
            for future in as_completed(futures):
                conv_path = futures[future]
                conv_path, success, error = future.result()

                if success:
                    success_count += 1
                    progress.update(task, advance=1,
                                    description=f"[green]✓[/green] {conv_path.stem[:30]}...")
                else:
                    failure_count += 1
                    progress.update(task, advance=1,
                                    description=f"[red]✗[/red] {conv_path.stem[:30]}...")
                    console.print(f"[red]  Failed {conv_path.name}: {error}[/red]")

    return (success_count, failure_count)


# =============================================================================
# Summary Generation (for conversation browser)
# =============================================================================

SUMMARY_GENERATION_PROMPT = """Analyze this conversation and return a JSON object with:
- "summary": A 1-2 sentence description of what the user was working on
- "filename": A short kebab-case name (3-5 words, like "fix-docker-build" or "add-dark-mode")

Focus on the USER'S INTENT, not the assistant's responses. Be concise and specific.

Conversation excerpt:
{conversation_excerpt}

Return ONLY valid JSON, no other text."""


def extract_conversation_excerpt(jsonl_path: Path, max_chars: int = 3000) -> str:
    """Extract user messages for summary generation."""
    messages = []
    total_chars = 0

    try:
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get('type') == 'user':
                        msg = entry.get('message', {})
                        content = msg.get('content', '')

                        text = None
                        if isinstance(content, str):
                            text = content
                        elif isinstance(content, list):
                            texts = []
                            for item in content:
                                if isinstance(item, dict) and item.get('type') == 'text':
                                    texts.append(item.get('text', ''))
                            text = ' '.join(texts)

                        if text and not text.startswith('<command-name>'):
                            # Clean up
                            text = text.strip()[:500]
                            if total_chars + len(text) > max_chars:
                                break
                            messages.append(f"- {text}")
                            total_chars += len(text)
                except json.JSONDecodeError:
                    continue
    except (IOError, OSError):
        pass

    return '\n'.join(messages[:10])


def generate_single_summary(jsonl_path: Path) -> tuple[Path, dict | None, str]:
    """Generate a summary for a single conversation using Sonnet.

    Returns (jsonl_path, summary_dict, error_message)
    """
    try:
        excerpt = extract_conversation_excerpt(jsonl_path)
        if not excerpt or len(excerpt) < 50:
            return (jsonl_path, None, "Insufficient content")

        prompt = SUMMARY_GENERATION_PROMPT.format(conversation_excerpt=excerpt)

        cmd = [
            config.get_claude_cli(),
            '--print',
            '--model', 'haiku',  # Use Haiku for fast/cheap summaries
            '--output-format', 'text',
            '--no-session-persistence',
        ]

        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=60,  # Short timeout for summaries
        )

        if result.returncode == 0 and result.stdout.strip():
            try:
                # Parse JSON response
                response_text = result.stdout.strip()
                # Handle markdown code blocks
                if response_text.startswith('```'):
                    response_text = response_text.split('\n', 1)[1].rsplit('```', 1)[0]
                parsed = json.loads(response_text)
                summary = parsed.get('summary', '').strip()
                filename = parsed.get('filename', '').strip()

                if summary and len(summary) > 10:
                    return (jsonl_path, {
                        'summary': summary,
                        'filename': filename if filename else None,
                        'generated_at': datetime.now().isoformat(),
                        'model': 'haiku',
                    }, "")
            except json.JSONDecodeError:
                # Use raw response as summary
                if len(result.stdout.strip()) > 10:
                    return (jsonl_path, {
                        'summary': result.stdout.strip()[:200],
                        'filename': None,
                        'generated_at': datetime.now().isoformat(),
                        'model': 'haiku',
                    }, "")

        return (jsonl_path, None, result.stderr or "Empty output")

    except subprocess.TimeoutExpired:
        return (jsonl_path, None, "Timeout")
    except Exception as e:
        return (jsonl_path, None, str(e))


def load_summary_cache() -> dict:
    """Load existing summaries from cache."""
    if SUMMARY_CACHE_PATH.exists():
        try:
            with open(SUMMARY_CACHE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_summary_cache(cache: dict):
    """Save summaries to cache file."""
    SUMMARY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SUMMARY_CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=2)


def generate_summaries_parallel(conversations: list[tuple[Path, float, Path]],
                                  max_workers: int = 5) -> int:
    """Generate summaries for conversations that don't have them.

    Returns count of new summaries generated.
    """
    cache = load_summary_cache()
    to_process = []

    for jsonl_path, mtime, source in conversations:
        session_id = jsonl_path.stem
        if session_id not in cache:
            to_process.append(jsonl_path)

    if not to_process:
        console.print("[dim]All conversations already have summaries[/dim]")
        return 0

    console.print(f"Generating summaries for {len(to_process)} conversations...")

    generated = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task("Summarizing", total=len(to_process))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(generate_single_summary, p): p for p in to_process}

            for future in as_completed(futures):
                jsonl_path, summary_dict, error = future.result()
                session_id = jsonl_path.stem

                if summary_dict:
                    cache[session_id] = summary_dict
                    generated += 1
                    # Save periodically
                    if generated % 10 == 0:
                        save_summary_cache(cache)

                progress.update(task, advance=1,
                               description=f"{jsonl_path.stem[:25]}...")

    save_summary_cache(cache)
    console.print(f"  Generated {generated} new summaries")
    return generated


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
                              temp_dir: Path) -> str:
    """Build the system prompt for Opus synthesis phase."""

    primary_list = '\n'.join(f"  - {p}" for p in primary_dirs)
    related_list = '\n'.join(f"  - {p}" for p in related_dirs) if related_dirs else "  (none)"

    return f"""You are cl-dream, helping Claude Code learn from past conversations.

## Project Configuration

**Primary projects** (CLAUDE.md will be updated):
{primary_list}

**Related projects** (conversations included, docs NOT updated):
{related_list}

**Temp directory**: {temp_dir}

## Your Task: Documentation Update

Lessons have been pre-extracted and are available at: {temp_dir}/lessons/

### Step 1: Context Priming (CRITICAL - Do This First)
Before making ANY documentation changes, understand what already exists:

For EACH primary project:
1. Read CLAUDE.md to understand current structure and conventions
2. List all files in docs/ folder: `Glob("docs/*.md")`
3. If there are MORE than 5 docs, spawn a Task subagent to summarize them:
   - Use subagent_type="Explore" with prompt: "Read all docs/*.md files and provide a summary of what each doc covers, its structure, and any conventions used"
4. If there are 5 or fewer docs, read them directly
5. Note: What topics are already documented? What's the writing style? Any existing "Common Gotchas" section?

This priming ensures new content fits with existing documentation rather than duplicating or conflicting.

### Step 2: Read and Analyze Lessons
1. Read all lesson files from {temp_dir}/lessons/
2. Build a mental model of what was worked on:
   - Note the "Session Summary" sections - what tasks were completed?
   - Note "Key files modified" - these may trigger doc reviews
   - Look for "Documentation Impact" sections - these flag potential doc updates
3. **Identify recurring issues**: If the same mistake/gotcha appears in multiple lessons, it's HIGH PRIORITY for documentation

### Step 3: Check Documentation Staleness
Before adding new content, check if existing docs need updates based on what was worked on:

1. Collect all "Key files modified" paths from lesson summaries
2. For each unique file path, grep docs/ to see if any docs reference it:
   ```
   Grep("filename.js", path="docs/")
   ```
3. If a doc references a heavily-modified file:
   - Read that doc section
   - Check if the content is still accurate given the changes
   - Update stale content or add notes about new behavior
4. Check "Documentation Impact" sections in lessons for explicit flags

This ensures existing documentation stays accurate, not just that new lessons are added.

### Step 4: Synthesize into Documentation
For EACH primary project:
   a. Match lessons to existing documentation structure
   b. **Prioritize recurring issues** - problems that appear in multiple sessions go in CLAUDE.md
   c. Update existing docs with new patterns/gotchas (prefer updating over creating)
   d. Create new docs only when topic doesn't fit anywhere and has 3+ lessons
   e. If there are verified "Unfixed Bugs", create/update BUG_REPORTS.md

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

**CRITICAL - What NOT to Add to CLAUDE.md:**

DO NOT add these types of content - they create noise and reduce the document's usefulness:

1. **One-off decisions** - Choices made for a specific feature/ticket that won't recur:
   - "We used library X for the dashboard feature" (unless X is now a project standard)
   - "The migration script handles edge case Y" (too specific)
   - Anything that starts with "For this feature..." or "In this PR..."

2. **Obvious things** any competent developer would know:
   - Basic language features ("Python uses indentation for blocks")
   - Standard library usage ("Use os.path.join for paths")
   - Generic best practices ("Write unit tests", "Use meaningful variable names")
   - Framework basics covered in official docs

3. **Temporary information**:
   - Workarounds for bugs that will be fixed
   - Notes about in-progress refactoring
   - "TODO" items (use issue tracker instead)

4. **Low-impact gotchas** that don't save significant time:
   - Minor annoyances that take <5 minutes to figure out
   - Issues that only affect rare edge cases
   - Problems with clear error messages that point to the solution

**What SHOULD go in CLAUDE.md:**
- Patterns that differ from common conventions (and WHY)
- Gotchas that have wasted significant debugging time (30+ min)
- Project-specific conventions not obvious from the code
- Architectural decisions that affect how new code should be written
- Integration points with external systems that have quirks

**Validation Before Including Lessons:**
When synthesizing lessons into CLAUDE.md or docs/:
1. Check "Session Type" tags in lesson files - be cautious with "PR Review" sessions
2. Look for "(discussed)" vs "(implemented)" markers:
   - "(implemented)" items → Include, validation step will verify
   - "(discussed)" items → SKIP unless you verify the code exists first
3. If a lesson references specific file paths, spot-check that key files exist
4. Skip lessons that reference patterns/code that clearly don't exist in the codebase
5. Patterns and gotchas are usually safe to include (they're about concepts)
6. Specific file path references need validation (they're about implementation)
7. When in doubt, EXCLUDE - validation cannot add content, only remove it

**Documentation Best Practices:**

*Single Source of Truth:*
- Each piece of information should live in ONE place
- Before adding content, check if it already exists (use Grep on docs/)
- If content exists elsewhere, either reference it or consolidate (don't duplicate)
- CLAUDE.md is for quick reference; docs/ is for detailed explanations

*Topic-Focused Documentation:*
- Each doc file should have a clear, focused purpose
- **DO NOT create catch-all files** like "lessons-learned.md" or "misc-notes.md" - they become dumping grounds
- Instead, create focused docs: "troubleshooting-auth.md", "database-patterns.md", "api-conventions.md"
- If a topic doesn't fit existing docs and isn't substantial enough for its own file, add to CLAUDE.md

*When to Create New Docs:*
- A topic has 3+ substantial lessons that don't fit existing docs
- The content would make an existing doc too long (>500 lines) or unfocused
- The topic is important enough to warrant dedicated documentation
- PREFER updating existing docs over creating new ones

*Writing Quality Documentation:*
- Start with a brief overview (1-2 sentences explaining purpose)
- Use clear headers and hierarchical structure
- Include concrete code examples, not just descriptions
- Add "When to use" context - help readers know if this doc is relevant
- Keep language direct and scannable (bullets > paragraphs)
- Include file paths and line numbers for code-specific guidance

*CLAUDE.md vs docs/:*
- CLAUDE.md: Quick reference, conventions, common gotchas (aim for ~100-300 lines)
- docs/: Detailed guides, architecture, troubleshooting, domain-specific patterns
- Don't put the same content in both - pick one location

*Documentation Index:*
At the end of CLAUDE.md, maintain a "## Documentation Index" table:
```markdown
| Doc File | When to Read |
|----------|--------------|
| `docs/architecture.md` | Before making structural changes |
| `docs/database.md` | Working with database queries or schema |
```
The "When to Read" column helps future sessions know which docs to load for specific tasks.

**BUG_REPORTS.md:**
If any lessons contain "Unfixed Bugs" sections, create or update BUG_REPORTS.md in the project root:
- Collect all unfixed bugs from the lessons
- Group by severity/area if possible
- Include: description, context, potential fix (if known), which conversation it was found in
- Format as a checklist so bugs can be tracked

**CRITICAL - Bug Verification Before Adding:**
Before adding ANY bug to BUG_REPORTS.md, you MUST verify:

1. **File path exists:** Use Glob or Read to check if the referenced file exists
   - If the file does NOT exist, skip the bug (may be from unmerged PR or deleted code)

2. **Bug has NOT been fixed:** Read the actual code to verify the bug still exists:
   - Use Grep to find the relevant function/section mentioned in the bug
   - Read that code section carefully
   - Check if the problematic pattern described in the bug is still present
   - Look for fix indicators: proper cleanup code, dispose functions, null checks, etc.
   - If the code no longer matches the bug description, SKIP it

3. **Err on the side of NOT including** - false positives (reporting fixed bugs) are worse than false negatives

Example validation workflow:
- Bug: "Memory leak in click-outside handler in template-renderer.js"
- Step 1: `Glob("**/template-renderer.js")` → File exists ✓
- Step 2: `Grep("click.*outside|setupClickOutside")` → Find the handler function
- Step 3: Read that section → Look for: Does it have a dispose function? Does it clean up listeners?
- Step 4: If code shows proper cleanup (effects.push with dispose, removeEventListener), the bug is FIXED - do not add

## Validation Step

After updating documentation, launch an INDEPENDENT validation subagent for EACH primary project.

IMPORTANT: The validation subagent has FRESH CONTEXT - it has NOT seen the lessons and will not be biased by what "should" exist. This is intentional.

For each primary project, launch a Task subagent:
- Use subagent_type="general-purpose" and model="sonnet"
- The subagent validates documentation against the actual codebase

Construct the validation prompt like this (replace PROJECT_DIR with actual path):

---
You are a documentation validator with FRESH EYES. You have NOT seen any lesson files or conversation transcripts. Your job is to verify that documentation references actually exist in the codebase.

Project directory: PROJECT_DIR

## Tasks:

1. Read CLAUDE.md
2. Read all files in docs/ folder (use Glob to find them)
3. Read BUG_REPORTS.md if it exists

4. For EACH document, verify ALL specific code references:
   - File paths mentioned (e.g., "JFD.API/Domains/...")
   - Class names (e.g., "class ApiError", "FileUploadFilterProvider")
   - Method/function names with specific signatures
   - Patterns that claim specific code exists

5. Use Glob and Grep to verify each reference:
   - `Glob("**/filename.cs")` to check file existence
   - `Grep("class ClassName")` to verify class exists
   - `Grep("public static.*MethodName")` to verify methods

6. For EACH unverified reference, use the Edit tool to:
   - REMOVE the incorrect content entirely, OR
   - If removal breaks context, REWRITE to describe the actual pattern

7. DO NOT add "[UNVERIFIED]" tags - either fix it or remove it

## Validation Checklist:
- [ ] Every file path mentioned → Glob to verify exists
- [ ] Every class name mentioned → Grep to verify exists
- [ ] Every method/function mentioned → Grep to verify exists
- [ ] Code examples → Verify the patterns match actual code
- [ ] BUG_REPORTS.md entries → Verify referenced files exist AND bugs aren't already fixed
  - Read the actual code where the bug is reported
  - Check if the problematic pattern still exists or if there's proper cleanup/fix code
  - Remove bugs that have been fixed

## Output:
Print a validation report:
1. **Verified**: References confirmed to exist (count)
2. **Removed**: Content removed because reference doesn't exist (list each)
3. **Rewritten**: Content rewritten to match actual code (list each)
4. **Files Modified**: List of documentation files that were changed
---

Wait for all validation subagents to complete before proceeding to Final Output.

## Final Output

After validation is complete, print a summary:

**Sessions Analyzed:**
- Number of lessons processed
- Brief list of what was worked on (from Session Summaries)

**Recurring Issues Found:**
- List any mistakes/gotchas that appeared in multiple sessions (these are high-value additions)

**Documentation Staleness:**
- Docs that were reviewed because they reference modified files
- Updates made to existing docs (content that was stale)

**New Content Added:**
- Changes made to each project's CLAUDE.md
- New/updated docs in docs/ folder
- Number of unfixed bugs added to BUG_REPORTS.md

**Rules Changed:**
- Any existing rules that were removed/modified (with reasons)

**Validation Results:**
- References that were verified
- Content that was removed (couldn't verify)
- Content that was rewritten (to match actual code)
"""


def run_opus_interactive(primary_dirs: list[Path], related_dirs: list[Path],
                         temp_dir: Path, dry_run: bool = False) -> bool:
    """Run Opus for documentation synthesis with tool access and streaming output.

    Opus will:
    1. Read pre-extracted lessons from temp_dir/lessons/
    2. Synthesize lessons into CLAUDE.md updates using Write/Edit tools
    3. Launch validation subagents
    4. Stream output to terminal
    """
    system_prompt = build_opus_system_prompt(primary_dirs, related_dirs, temp_dir)

    user_prompt = f"""Please proceed with the cl-dream workflow.

Lessons have been pre-extracted and are available at: {temp_dir}/lessons/

Read the lesson files and update CLAUDE.md for each primary project, then run validation."""

    if dry_run:
        console.print("\n[bold cyan]--- DRY RUN ---[/bold cyan]")
        console.print(f"Would run Opus with access to:")
        console.print(f"  Primary dirs: {[str(p) for p in primary_dirs]}")
        console.print(f"  Related dirs: {[str(p) for p in related_dirs]}")
        console.print(f"  Temp dir: {temp_dir}")
        console.print(f"\n[dim]System prompt preview:[/dim]")
        console.print(system_prompt[:1000] + "..." if len(system_prompt) > 1000 else system_prompt)
        return True

    # Build --add-dir flags for all directories Opus needs
    all_dirs = list(primary_dirs) + list(related_dirs) + [temp_dir]
    add_dir_args = []
    for d in all_dirs:
        add_dir_args.extend(['--add-dir', str(d)])

    cmd = [
        config.get_claude_cli(),
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
# CLAUDE.md Cleanup Phase
# =============================================================================

CLEANUP_SYSTEM_PROMPT = """You are a documentation quality reviewer for CLAUDE.md files.

Your job is to clean up CLAUDE.md by removing content that doesn't provide lasting value.

## Project Directory
{project_dir}

## What to REMOVE

1. **One-off decisions** that won't recur:
   - "We decided to use X for this specific feature" (unless it's a pattern)
   - Temporary workarounds that have been resolved
   - Decisions specific to a single PR or ticket

2. **Obvious things** any experienced developer would know:
   - Basic language features ("Python uses indentation")
   - Standard library usage ("Use os.path for file paths")
   - Generic best practices ("Write tests", "Use descriptive names")

3. **Stale information**:
   - References to files/functions that no longer exist
   - Patterns for code that was refactored away
   - Workarounds for bugs that were fixed

4. **Redundant content**:
   - Information duplicated in docs/ files (CLAUDE.md should reference, not repeat)
   - Multiple entries saying the same thing in different words

## What to KEEP

- Project-specific conventions that aren't obvious
- Gotchas that have bitten people multiple times
- Patterns unique to this codebase
- Information that would save significant debugging time
- Cross-cutting concerns that affect many files

## Your Workflow

1. **Read CLAUDE.md** to understand current content
2. **Check git history** to understand what's changed recently:
   - `git log --oneline -20` for recent commits
   - `git diff HEAD~20 -- CLAUDE.md` if CLAUDE.md was recently modified
3. **Review docs/ folder** to identify duplicated content
4. **Verify file references** - use Glob to check if referenced files exist
5. **Edit CLAUDE.md** to remove stale/one-off/obvious content
6. **Consolidate** remaining content for clarity

## Output Format

After making changes, provide a summary:
- **Removed**: List items removed and why
- **Kept**: Notable items that were kept
- **Suggestions**: Any structural improvements needed

Be aggressive about removing low-value content. A concise CLAUDE.md is more useful than a comprehensive one."""


def run_cleanup_phase(project_dir: Path, dry_run: bool = False) -> bool:
    """Run CLAUDE.md cleanup using Opus.

    This phase reviews and cleans up CLAUDE.md to remove:
    - One-off decisions unlikely to recur
    - Obvious information
    - Stale references to code that no longer exists
    """
    claude_md = project_dir / 'CLAUDE.md'
    if not claude_md.exists():
        console.print(f"[yellow]No CLAUDE.md found in {project_dir}[/yellow]")
        return True

    system_prompt = CLEANUP_SYSTEM_PROMPT.format(project_dir=project_dir)

    user_prompt = f"""Please review and clean up CLAUDE.md in {project_dir}.

Start by reading CLAUDE.md, then check git history and docs/ folder to understand context.
Remove one-off decisions, obvious content, and stale references.
Use the Edit tool to make changes directly."""

    if dry_run:
        console.print("\n[bold cyan]--- DRY RUN (Cleanup) ---[/bold cyan]")
        console.print(f"Would run cleanup on: {project_dir}")
        console.print(f"\n[dim]System prompt preview:[/dim]")
        console.print(system_prompt[:800] + "...")
        return True

    cmd = [
        config.get_claude_cli(),
        '--print',
        '--model', 'opus',
        '--system-prompt', system_prompt,
        '--allowedTools', 'Read,Edit,Glob,Grep,Bash',
        '--output-format', 'stream-json',
        '--verbose',
        '--add-dir', str(project_dir),
    ]

    console.print(f"\n[bold]Running CLAUDE.md cleanup for {project_dir.name}...[/bold]")

    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        process.stdin.write(user_prompt)
        process.stdin.close()

        # Stream output
        while True:
            if process.stdout:
                line = process.stdout.readline()
                if line:
                    try:
                        data = json.loads(line)
                        msg_type = data.get('type', '')

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

                        elif msg_type == 'result':
                            if data.get('subtype') == 'success':
                                console.print("\n[green]Cleanup completed successfully[/green]")
                            else:
                                console.print(f"\n[yellow]Cleanup ended: {data.get('subtype', 'unknown')}[/yellow]")

                    except json.JSONDecodeError:
                        console.print(line.rstrip())
                elif process.poll() is not None:
                    break
            else:
                break

        if process.stderr:
            stderr_output = process.stderr.read()
            if stderr_output:
                console.print(f"[dim]{stderr_output}[/dim]")

        return process.returncode == 0

    except subprocess.TimeoutExpired:
        process.kill()
        console.print(f"[red]Cleanup timed out[/red]")
        return False
    except FileNotFoundError:
        console.print("[red]Claude CLI not found[/red]")
        return False


# =============================================================================
# Main Workflow
# =============================================================================

def run_dream_workflow(primary_dirs: list[Path], related_dirs: list[Path],
                        force: bool, dry_run: bool, retry: bool,
                        keep_temp: bool, skip_summaries: bool, cleanup: bool):
    """Run the main cl-dream workflow on specified projects."""

    console.print(f"[bold]cl-dream[/bold] - Learning from past conversations\n")
    console.print(f"Primary projects: {[str(p) for p in primary_dirs]}")
    if related_dirs:
        console.print(f"Related projects: {[str(p) for p in related_dirs]}")
    console.print()

    # Check for --retry with cached lessons
    use_cached_lessons = False
    if retry:
        cached = load_cached_lessons(primary_dirs)
        if cached is None:
            console.print("[red]Error: --retry specified but no cached lessons found.[/red]")
            console.print("[yellow]Run without --retry first to generate lessons.[/yellow]")
            return False
        use_cached_lessons = True
        console.print(f"[green]Using {len(cached)} cached lessons (--retry mode)[/green]\n")

    # Load state
    state = {"version": 1, "projects": {}} if force else load_state()
    if force:
        console.print("[yellow]Force mode: reprocessing all conversations[/yellow]\n")

    # Find new conversations (skip if using cached lessons)
    conversation_data = []
    if not use_cached_lessons:
        conversation_data = find_new_conversations(primary_dirs, related_dirs, state)

        if not conversation_data:
            console.print("[green]No new conversations to process[/green]")
            # Still run cleanup if requested
            if cleanup and not dry_run:
                for proj in primary_dirs:
                    run_cleanup_phase(proj, dry_run)
            return True

        console.print(f"Found [bold]{len(conversation_data)}[/bold] conversations to process\n")

        if dry_run:
            console.print("[cyan]DRY RUN - would process:[/cyan]")
            for conv, mtime, source in conversation_data:
                mtime_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')
                console.print(f"  - {conv.name} (modified: {mtime_str}, from: {source.name})")
            console.print()

    with temp_dream_dir(keep=keep_temp) as temp_dir:
        console.print(f"[dim]Temp directory: {temp_dir}[/dim]\n")

        # Step 1: Generate markdown (skip if using cached lessons)
        if not use_cached_lessons:
            console.print("[bold]Step 1: Generating markdown...[/bold]")

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

            # Step 2: Extract lessons in parallel using Sonnet
            console.print(f"[bold]Step 2: Extracting lessons (parallel, up to {MAX_PARALLEL_EXTRACTIONS} at a time)...[/bold]")

            conversations_dir = temp_dir / 'conversations'
            lessons_dir = temp_dir / 'lessons'

            if dry_run:
                console.print(f"[cyan]DRY RUN - would extract lessons from {len(conversation_data)} conversations[/cyan]\n")
            else:
                success_count, failure_count = extract_lessons_parallel(conversations_dir, lessons_dir)
                console.print(f"  Extracted {success_count} lessons ({failure_count} failed)\n")

                if success_count == 0:
                    console.print("[red]No lessons extracted, aborting[/red]")
                    return False
        else:
            # Copy cached lessons to temp dir
            cache_dir = get_cache_dir(primary_dirs)
            for lesson_file in cache_dir.glob("*.md"):
                shutil.copy(lesson_file, temp_dir / 'lessons' / lesson_file.name)
            console.print(f"[dim]Copied cached lessons to temp directory[/dim]\n")

        # Backup CLAUDE.md files (smart backup - skip if git-tracked)
        if not dry_run:
            for proj in primary_dirs:
                claude_md = proj / 'CLAUDE.md'
                if claude_md.exists():
                    smart_backup(claude_md)

        # Step 3: Opus synthesizes lessons into documentation
        console.print("[bold]Step 3: Running Opus for documentation synthesis...[/bold]")

        success = run_opus_interactive(
            primary_dirs=primary_dirs,
            related_dirs=related_dirs,
            temp_dir=temp_dir,
            dry_run=dry_run
        )

        if success and not dry_run:
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

            # Step 4: Generate summaries for conversation browser
            if not skip_summaries and conversation_data:
                console.print("\n[bold]Step 4: Generating conversation summaries...[/bold]")
                generate_summaries_parallel(conversation_data)

            # Step 5: Optional cleanup phase
            if cleanup:
                console.print("\n[bold]Step 5: Running CLAUDE.md cleanup...[/bold]")
                for proj in primary_dirs:
                    run_cleanup_phase(proj, dry_run)

        elif not success and not dry_run:
            console.print("\n[red]Opus session failed or was interrupted[/red]")

        if keep_temp:
            console.print(f"\n[yellow]Temp directory kept: {temp_dir}[/yellow]")

    return success


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

    # Run on all previously-processed projects
    python cl_dream.py auto

    # Clean up CLAUDE.md only (no lesson extraction)
    python cl_dream.py cleanup /path/to/project

    # Preview what would be done
    python cl_dream.py . --dry-run
        """
    )

    # Check for subcommands
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Auto command - run on all previously processed projects
    auto_parser = subparsers.add_parser('auto',
        help='Run on all previously-processed projects that still exist')
    auto_parser.add_argument('--dry-run', action='store_true',
                             help='Show what would be done without making changes')
    auto_parser.add_argument('--force', action='store_true',
                             help='Reprocess all conversations (ignore state)')
    auto_parser.add_argument('--skip-summaries', action='store_true',
                             help='Skip generating conversation summaries')
    auto_parser.add_argument('--cleanup', action='store_true',
                             help='Run CLAUDE.md cleanup phase after synthesis')
    auto_parser.add_argument('--keep-temp', action='store_true',
                             help='Keep temp directory for debugging')

    # Cleanup command - just clean up CLAUDE.md
    cleanup_parser = subparsers.add_parser('cleanup',
        help='Clean up CLAUDE.md by removing stale/one-off content')
    cleanup_parser.add_argument('project_dir', type=Path,
                                help='Project directory to clean up')
    cleanup_parser.add_argument('--dry-run', action='store_true',
                                help='Show what would be done without making changes')

    # Default: process specific projects
    parser.add_argument('project_dirs', nargs='*', type=Path,
                        help='Project directories to update docs for')
    parser.add_argument('--related', nargs='*', type=Path, default=[],
                        help='Related projects (conversations included, docs NOT updated)')
    parser.add_argument('--force', action='store_true',
                        help='Reprocess all conversations (ignore state)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be done without making changes')
    parser.add_argument('--retry', action='store_true',
                        help='Skip lesson extraction, reuse cached lessons')
    parser.add_argument('--skip-summaries', action='store_true',
                        help='Skip generating conversation summaries')
    parser.add_argument('--cleanup', action='store_true',
                        help='Run CLAUDE.md cleanup phase after synthesis')
    parser.add_argument('--keep-temp', action='store_true',
                        help='Keep temp directory for debugging')

    args = parser.parse_args()

    # Handle 'auto' command
    if args.command == 'auto':
        state = load_state()
        projects = discover_auto_projects(state)

        if not projects:
            console.print("[yellow]No previously-processed projects found.[/yellow]")
            console.print("[dim]Run cl_dream.py on a project first, then use 'auto' for incremental updates.[/dim]")
            sys.exit(0)

        console.print(f"[bold]Auto mode:[/bold] Found {len(projects)} previously-processed projects\n")

        for proj in projects:
            console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
            console.print(f"[bold cyan]Processing: {proj}[/bold cyan]")
            console.print(f"[bold cyan]{'='*60}[/bold cyan]\n")

            run_dream_workflow(
                primary_dirs=[proj],
                related_dirs=[],
                force=args.force,
                dry_run=args.dry_run,
                retry=False,
                keep_temp=args.keep_temp,
                skip_summaries=args.skip_summaries,
                cleanup=args.cleanup,
            )

        return

    # Handle 'cleanup' command
    if args.command == 'cleanup':
        project_dir = args.project_dir.resolve()
        if not project_dir.exists():
            console.print(f"[red]Error: Project directory does not exist: {project_dir}[/red]")
            sys.exit(1)

        run_cleanup_phase(project_dir, args.dry_run)
        return

    # Default: process specific projects
    if not args.project_dirs:
        parser.print_help()
        sys.exit(1)

    # Validate project directories
    primary_dirs = []
    for p in args.project_dirs:
        resolved = p.resolve()
        if not resolved.exists():
            console.print(f"[red]Error: Project directory does not exist: {resolved}[/red]")
            sys.exit(1)
        primary_dirs.append(resolved)

    # Related dirs don't need to exist on disk - they may be old paths that were moved
    # but Claude still has conversation logs for them
    related_dirs = []
    for p in args.related:
        resolved = p.resolve()
        related_dirs.append(resolved)
        if not resolved.exists():
            console.print(f"[dim]Note: Related dir {resolved} doesn't exist on disk (looking for old conversations)[/dim]")

    run_dream_workflow(
        primary_dirs=primary_dirs,
        related_dirs=related_dirs,
        force=args.force,
        dry_run=args.dry_run,
        retry=args.retry,
        keep_temp=args.keep_temp,
        skip_summaries=args.skip_summaries,
        cleanup=args.cleanup,
    )


if __name__ == '__main__':
    main()
