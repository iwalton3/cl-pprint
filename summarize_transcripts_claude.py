#!/usr/bin/env python3
"""
Generate AI summaries for Claude transcripts using Claude Code CLI.

Similar to summarize_transcripts.py but uses Claude Haiku via CLI instead of Ollama.
This avoids requiring a local Ollama installation.

Usage:
    python summarize_transcripts_claude.py [--dir ~/.claude/projects] [--force]

Options:
    --dir       Base directory for Claude projects
    --force     Re-summarize all transcripts (ignore cache)
    --dry-run   Show what would be processed without calling Claude
    --parallel  Number of parallel requests (default: 3)
"""

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
except ImportError:
    print("Please install rich: pip install rich")
    sys.exit(1)

import config

console = Console()

SUMMARY_CACHE_PATH = config.get_path('summary_cache') or Path.home() / '.claude' / 'transcript_summaries.json'
CLAUDE_TIMEOUT = 60  # 60 seconds per summarization
MAX_PARALLEL = 3  # Default parallel requests


def load_cache() -> dict:
    """Load existing summaries from cache."""
    if SUMMARY_CACHE_PATH.exists():
        try:
            with open(SUMMARY_CACHE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_cache(cache: dict):
    """Save summaries to cache file."""
    SUMMARY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SUMMARY_CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=2)


def is_valid_user_message(text: str) -> bool:
    """Check if text is a valid user message (not command/system)."""
    if not text or not text.strip():
        return False
    if '<command-name>' in text:
        return False
    if '<local-command-stdout>' in text:
        return False
    if 'caveat:' in text.lower():
        return False
    if 'the messages below were generated' in text.lower():
        return False
    if text.strip().startswith('<tool_result>'):
        return False
    return True


def extract_user_messages(jsonl_path: Path) -> list[str]:
    """
    Extract relevant user messages from a transcript.

    Includes:
    - The initial user message
    - Messages before any plan mode (before ExitPlanMode)
    - Any message over 250 characters
    - Session summary from compacted/resumed sessions (as fallback)
    """
    messages = []
    session_summary = None  # Fallback from compacted sessions
    seen_exit_plan = False
    is_first_user_msg = True

    try:
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get('type')

                # Check for session summary (from compacted/resumed sessions)
                if entry_type == 'summary' and not session_summary:
                    summary_text = entry.get('summary', '')
                    if summary_text and len(summary_text) > 5:
                        session_summary = summary_text

                # Check for ExitPlanMode tool use
                if entry_type == 'assistant':
                    msg = entry.get('message', {})
                    content = msg.get('content', [])
                    if isinstance(content, list):
                        for item in content:
                            if isinstance(item, dict) and item.get('type') == 'tool_use':
                                if item.get('name') == 'ExitPlanMode':
                                    seen_exit_plan = True

                # Extract user messages
                if entry_type == 'user':
                    msg = entry.get('message', {})
                    content = msg.get('content', '')

                    text = None
                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        # Get text parts, skip tool results
                        texts = []
                        for item in content:
                            if isinstance(item, dict) and item.get('type') == 'text':
                                texts.append(item.get('text', ''))
                        text = ' '.join(texts)

                    if text and is_valid_user_message(text):
                        # Include if: first message, before plan mode, or >250 chars
                        include = (
                            is_first_user_msg or
                            not seen_exit_plan or
                            len(text.strip()) > 250
                        )

                        if include:
                            # Clean up the text
                            cleaned = text.strip()
                            # Remove excessive whitespace
                            cleaned = re.sub(r'\s+', ' ', cleaned)
                            # Limit individual message length
                            if len(cleaned) > 1000:
                                cleaned = cleaned[:1000] + "..."
                            messages.append(cleaned)

                        is_first_user_msg = False

    except Exception as e:
        console.print(f"[red]Error reading {jsonl_path}: {e}[/red]")

    # If no user messages found, use session summary as fallback
    if not messages and session_summary:
        messages.append(f"[Resumed session] {session_summary}")

    return messages


def get_session_id(jsonl_path: Path) -> str:
    """Get session ID from file path."""
    return jsonl_path.stem


def call_claude(messages: list[str]) -> tuple[Optional[str], Optional[str]]:
    """Call Claude CLI (Haiku) to generate summary and filename.

    Returns:
        Tuple of (summary, filename) where summary is a detailed description
        and filename is a short kebab-case name for the file.
    """
    if not messages:
        return None, None

    # Build prompt - request both summary and filename
    messages_text = "\n".join(f"- {msg[:400]}" for msg in messages[:5])  # Limit to 5 messages

    prompt = f"""Analyze these user messages from a coding session and return JSON with:
- "summary": A 1-2 sentence description of what the user wanted to accomplish
- "filename": A short kebab-case name (3-5 words, like "fix-docker-build" or "add-dark-mode")

Messages:
{messages_text}

Return ONLY valid JSON, no other text."""

    try:
        cmd = [
            config.get_claude_cli(),
            '--print',
            '--model', 'haiku',
            '--output-format', 'text',
            '--no-session-persistence',
        ]

        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT,
        )

        if result.returncode != 0:
            error = result.stderr or "Unknown error"
            return None, None

        response_text = result.stdout.strip()

        # Parse JSON response
        summary = None
        filename = None

        # Try to extract JSON from response
        # Sometimes Claude wraps JSON in markdown code blocks
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
        if json_match:
            response_text = json_match.group(1)

        try:
            parsed = json.loads(response_text)
            summary = parsed.get('summary', '').strip()
            filename = parsed.get('filename', '').strip()
        except json.JSONDecodeError:
            # Fallback: try to use response as summary
            if len(response_text) > 10 and len(response_text) < 500:
                summary = response_text

        # Clean up summary
        if summary:
            # Take first sentence/line only
            if '\n' in summary:
                summary = summary.split('\n')[0].strip()

            # Remove common prefixes
            prefixes_to_remove = [
                'Summary:', 'The user', 'This conversation',
            ]
            for prefix in prefixes_to_remove:
                if summary.lower().startswith(prefix.lower()):
                    summary = summary[len(prefix):].strip()
                    while summary and summary[0] in ':.,;- ':
                        summary = summary[1:].strip()

            # Capitalize first letter
            if summary and summary[0].islower():
                summary = summary[0].upper() + summary[1:]

            # Validate summary
            if len(summary) < 10:
                summary = None

        # Clean up filename
        if filename:
            # Convert to kebab-case, remove invalid chars
            filename = re.sub(r'[^a-zA-Z0-9\-]', '-', filename.lower())
            filename = re.sub(r'-+', '-', filename)  # Collapse multiple dashes
            filename = filename.strip('-')
            # Limit length
            if len(filename) > 50:
                filename = filename[:50].rsplit('-', 1)[0]
            # Validate
            if len(filename) < 3:
                filename = None

        return summary, filename

    except subprocess.TimeoutExpired:
        return None, None
    except FileNotFoundError:
        console.print("[red]Error: Claude CLI not found. Is it installed and in PATH?[/red]")
        return None, None
    except Exception as e:
        return None, None


def process_single_transcript(path: Path, cache: dict) -> tuple[str, dict | None]:
    """Process a single transcript and return (session_id, result_dict or None).

    Thread-safe function for parallel processing.
    """
    session_id = get_session_id(path)

    # Extract messages
    messages = extract_user_messages(path)

    if not messages:
        return session_id, None

    # Call Claude
    summary, filename = call_claude(messages)

    if summary:
        return session_id, {
            "summary": summary,
            "filename": filename,  # May be None if generation failed
            "generated_at": datetime.now().isoformat(),
            "model": "claude-haiku",
            "message_count": len(messages)
        }

    return session_id, None


def find_transcripts(base_dir: Path) -> list[Path]:
    """Find all transcript files."""
    transcripts = []

    for project_dir in base_dir.iterdir():
        if not project_dir.is_dir():
            continue

        for jsonl_file in project_dir.glob("*.jsonl"):
            # Skip agent files (subagent logs)
            if jsonl_file.name.startswith("agent-"):
                continue
            transcripts.append(jsonl_file)

    return transcripts


def main():
    parser = argparse.ArgumentParser(
        description='Generate AI summaries for Claude transcripts using Claude CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--dir', '-d',
        default=config.get_path('claude_projects') or Path.home() / '.claude' / 'projects',
        type=Path,
        help='Base directory for Claude projects'
    )
    parser.add_argument(
        '--force', '-f',
        action='store_true',
        help='Re-summarize all transcripts (ignore cache)'
    )
    parser.add_argument(
        '--dry-run', '-n',
        action='store_true',
        help='Show what would be processed without calling Claude'
    )
    parser.add_argument(
        '--parallel', '-p',
        type=int,
        default=MAX_PARALLEL,
        help=f'Number of parallel requests (default: {MAX_PARALLEL})'
    )

    args = parser.parse_args()

    if not args.dir.exists():
        console.print(f"[red]Directory not found: {args.dir}[/red]")
        sys.exit(1)

    console.print(f"[bold blue]Claude Transcript Summarizer (Claude CLI)[/bold blue]")
    console.print(f"[dim]Model: claude-haiku[/dim]")
    console.print(f"[dim]Cache: {SUMMARY_CACHE_PATH}[/dim]")
    console.print(f"[dim]Parallel requests: {args.parallel}[/dim]\n")

    # Load cache
    cache = load_cache() if not args.force else {}

    # Find transcripts
    console.print("[dim]Scanning for transcripts...[/dim]")
    transcripts = find_transcripts(args.dir)
    console.print(f"Found [green]{len(transcripts)}[/green] transcripts\n")

    # Filter to ones needing processing
    to_process = []
    for path in transcripts:
        session_id = get_session_id(path)
        if session_id not in cache:
            to_process.append(path)

    if not to_process:
        console.print("[green]All transcripts already summarized![/green]")
        return

    console.print(f"Need to summarize: [yellow]{len(to_process)}[/yellow] transcripts")

    if args.dry_run:
        console.print("\n[dim]Dry run - would process:[/dim]")
        for path in to_process[:10]:
            console.print(f"  {path.parent.name}/{path.name}")
        if len(to_process) > 10:
            console.print(f"  ... and {len(to_process) - 10} more")
        return

    # Process transcripts in parallel
    console.print()
    processed = 0
    errors = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task("Summarizing...", total=len(to_process))

        with ThreadPoolExecutor(max_workers=args.parallel) as executor:
            # Submit all tasks
            futures = {}
            for path in to_process:
                future = executor.submit(process_single_transcript, path, cache)
                futures[future] = path

            # Process results as they complete
            for future in as_completed(futures):
                path = futures[future]
                project_name = path.parent.name

                progress.update(task, description=f"[cyan]{project_name[:30]}[/cyan]")

                try:
                    session_id, result = future.result()

                    if result:
                        cache[session_id] = result
                        processed += 1
                    else:
                        errors += 1
                except Exception as e:
                    console.print(f"[red]Error processing {path.name}: {e}[/red]")
                    errors += 1

                progress.advance(task)

                # Save periodically
                if processed % 10 == 0 and processed > 0:
                    save_cache(cache)

    # Final save
    save_cache(cache)

    console.print()
    console.print(f"[green]Summarized {processed} transcripts[/green]")
    if errors:
        console.print(f"[yellow]Errors/skipped: {errors}[/yellow]")
    console.print(f"[dim]Cache saved to: {SUMMARY_CACHE_PATH}[/dim]")


if __name__ == '__main__':
    main()
