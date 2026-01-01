#!/usr/bin/env python3
"""
Generate AI summaries for Claude transcripts using local Ollama.

Summarizes transcripts based on user messages. Caches results to avoid re-processing.
Configure the Ollama model and URL in config.json (see config.example.json).

Usage:
    python summarize_transcripts.py [--dir ~/.claude/projects] [--force]

Options:
    --dir       Base directory for Claude projects
    --force     Re-summarize all transcripts (ignore cache)
    --dry-run   Show what would be processed without calling Ollama
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    print("Please install requests: pip install requests")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
except ImportError:
    print("Please install rich: pip install rich")
    sys.exit(1)

import config

console = Console()

SUMMARY_CACHE_PATH = config.get_path('summary_cache') or Path.home() / '.claude' / 'transcript_summaries.json'
OLLAMA_MODEL = config.get('ollama.model')
OLLAMA_URL = config.get('ollama.url')


def load_cache() -> dict:
    """Load existing summaries from cache."""
    if SUMMARY_CACHE_PATH.exists():
        try:
            with open(SUMMARY_CACHE_PATH, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_cache(cache: dict):
    """Save summaries to cache file."""
    SUMMARY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SUMMARY_CACHE_PATH, 'w') as f:
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
        with open(jsonl_path, 'r') as f:
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


def call_ollama(messages: list[str]) -> tuple[Optional[str], Optional[str]]:
    """Call Ollama API to generate summary and filename.

    Returns:
        Tuple of (summary, filename) where summary is a detailed description
        and filename is a short kebab-case name for the file.
    """
    if not messages:
        return None, None

    # Build prompt - request both summary and filename
    messages_text = "\n".join(f"- {msg[:400]}" for msg in messages[:5])  # Limit to 5 messages

    prompt = f"""Analyze these user messages and return JSON with:
- "summary": A 1-2 sentence description of what the user wanted
- "filename": A short kebab-case name (3-5 words, like "fix-docker-build" or "add-dark-mode")

Messages:
{messages_text}

JSON:"""

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "think": False,  # Disable thinking mode for direct output
                "format": "json",
                "options": {
                    "num_predict": config.get('ollama.max_tokens', 150),
                    "temperature": config.get('ollama.temperature', 0.3),
                }
            },
            timeout=config.get('ollama.timeout', 120)
        )
        response.raise_for_status()

        result = response.json()
        response_text = result.get('response', '').strip()

        # Parse JSON response
        summary = None
        filename = None
        try:
            parsed = json.loads(response_text)
            summary = parsed.get('summary', '').strip()
            filename = parsed.get('filename', '').strip()
        except json.JSONDecodeError:
            # Fallback: use response as summary
            summary = response_text

        # Clean up summary
        if summary:
            # Take first sentence/line only
            if '\n' in summary:
                summary = summary.split('\n')[0].strip()

            # Remove thinking preamble patterns
            preamble_patterns = [
                r'^(Hmm,?\s*)',
                r'^(Okay,?\s*)',
                r'^(So,?\s*)',
                r'^(Well,?\s*)',
                r'^(Let me see,?\s*)',
                r'^(Let\'s break this down\.?\s*)',
                r'^(The user (is |was |wants? |has ))',
                r'^(They (are |want |wanted |have ))',
                r'^(We are given[^.]*\.?\s*)',
                r'^(Their messages\.?\s*)',
                r'^(A series of messages[^.]*\.?\s*)',
            ]
            for pattern in preamble_patterns:
                summary = re.sub(pattern, '', summary, flags=re.IGNORECASE)

            # Remove common prefixes
            prefixes_to_remove = [
                'Summary:', 'This conversation', 'asking about', 'asking for', 'asking me to',
                'Figure out what they wanted', 'Figure out what the user wanted',
                'Figure out what the original user wanted',
                'Let me look at', 'Based on', 'Looking at',
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

    except requests.exceptions.ConnectionError:
        console.print("[red]Error: Cannot connect to Ollama. Is it running?[/red]")
        console.print("[dim]Start with: ollama serve[/dim]")
        return None, None
    except requests.exceptions.Timeout:
        console.print("[yellow]Request timed out[/yellow]")
        return None, None
    except Exception as e:
        console.print(f"[red]Ollama error: {e}[/red]")
        return None, None


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
        description='Generate AI summaries for Claude transcripts',
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
        help='Show what would be processed without calling Ollama'
    )

    args = parser.parse_args()

    if not args.dir.exists():
        console.print(f"[red]Directory not found: {args.dir}[/red]")
        sys.exit(1)

    console.print(f"[bold blue]Claude Transcript Summarizer[/bold blue]")
    console.print(f"[dim]Model: {OLLAMA_MODEL}[/dim]")
    console.print(f"[dim]Cache: {SUMMARY_CACHE_PATH}[/dim]\n")

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

    # Process transcripts
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

        for path in to_process:
            session_id = get_session_id(path)
            project_name = path.parent.name

            progress.update(task, description=f"[cyan]{project_name[:30]}[/cyan]")

            # Extract messages
            messages = extract_user_messages(path)

            if not messages:
                progress.advance(task)
                continue

            # Call Ollama
            summary, filename = call_ollama(messages)

            if summary:
                cache[session_id] = {
                    "summary": summary,
                    "filename": filename,  # May be None if generation failed
                    "generated_at": datetime.now().isoformat(),
                    "model": OLLAMA_MODEL,
                    "message_count": len(messages)
                }
                processed += 1

                # Save periodically
                if processed % 10 == 0:
                    save_cache(cache)
            else:
                errors += 1

            progress.advance(task)

    # Final save
    save_cache(cache)

    console.print()
    console.print(f"[green]Summarized {processed} transcripts[/green]")
    if errors:
        console.print(f"[yellow]Errors: {errors}[/yellow]")
    console.print(f"[dim]Cache saved to: {SUMMARY_CACHE_PATH}[/dim]")


if __name__ == '__main__':
    main()
