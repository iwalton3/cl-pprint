#!/usr/bin/env python3
"""
Interactive Claude transcript browser.

Browse, search, and export Claude Code conversation transcripts.

Usage:
    python browse_transcripts.py [--dir ~/.claude/projects]

Requirements:
    pip install rich prompt_toolkit

Features:
    - Browse transcripts organized by project
    - Search by content, date, or project name
    - Preview first user message
    - Multi-select for batch export
    - Export to markdown using format_jsonl.py
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich.prompt import Prompt, Confirm
    from rich.style import Style
    from rich.live import Live
    from rich.layout import Layout
    from rich import box
except ImportError:
    print("Please install rich: pip install rich")
    sys.exit(1)

try:
    from prompt_toolkit import prompt as pt_prompt
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.history import InMemoryHistory
except ImportError:
    print("Please install prompt_toolkit: pip install prompt_toolkit")
    sys.exit(1)

# Import the formatter and config
from format_jsonl import format_jsonl
import config


console = Console()


class TranscriptInfo:
    """Metadata about a transcript file."""

    def __init__(self, path: Path):
        self.path = path
        self.session_id = path.stem
        self.project_dir = path.parent.name
        self.project_name = self._parse_project_name(self.project_dir)
        self.timestamp: Optional[datetime] = None
        self.end_timestamp: Optional[datetime] = None
        self.first_prompt: str = ""
        self.slug: str = ""
        self.git_branch: str = ""
        self.cwd: str = ""
        self.message_count: int = 0
        self.file_size: int = 0
        self.version: str = ""
        self.summary: Optional[str] = None  # AI-generated summary from cache
        self.filename: Optional[str] = None  # AI-generated short filename from cache
        self._load_metadata()

    def _parse_project_name(self, dir_name: str) -> str:
        """Convert project directory name to readable format."""
        # Convert -home-user-Desktop-myproject to myproject
        # or -working-project to project
        parts = dir_name.split('-')
        if len(parts) > 1:
            # Find meaningful parts (skip common dirs from config)
            skip = set(config.get('project_name_skip_dirs', []))
            meaningful = [p for p in parts if p and p not in skip]
            if meaningful:
                return '/'.join(meaningful[-2:])  # Last 2 parts
        return dir_name

    def _load_metadata(self):
        """Load metadata from the JSONL file."""
        self.file_size = self.path.stat().st_size

        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                first_user_msg = None
                session_summary = None  # Fallback from compacted sessions
                last_timestamp = None

                commands_used = []  # Track commands for fallback description

                def is_valid_prompt(text):
                    """Check if text is a valid user prompt (not command/caveat)."""
                    if not text or not text.strip():
                        return False
                    if '<command-name>' in text:
                        # Extract command name for fallback
                        import re
                        match = re.search(r'<command-name>([^<]+)</command-name>', text)
                        if match and match.group(1) not in commands_used:
                            commands_used.append(match.group(1))
                        return False
                    if '<local-command-stdout>' in text:
                        return False
                    if 'caveat:' in text.lower():
                        return False
                    if 'the messages below were generated' in text.lower():
                        return False
                    return True

                for i, line in enumerate(f):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    self.message_count += 1
                    entry_type = entry.get('type')

                    # Get timestamp
                    ts = entry.get('timestamp')
                    if ts:
                        try:
                            parsed_ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                            if self.timestamp is None:
                                self.timestamp = parsed_ts
                            last_timestamp = parsed_ts
                        except:
                            pass

                    # Get metadata from first few entries
                    if i < 50:
                        if not self.slug:
                            self.slug = entry.get('slug', '')
                        if not self.git_branch:
                            self.git_branch = entry.get('gitBranch', '')
                        if not self.cwd:
                            self.cwd = entry.get('cwd', '')
                        if not self.version:
                            self.version = entry.get('version', '')

                    # Check for session summary (from compacted/resumed sessions)
                    if entry_type == 'summary' and not session_summary:
                        summary_text = entry.get('summary', '')
                        if summary_text and len(summary_text) > 5:
                            session_summary = f"[Resumed] {summary_text[:200]}"

                    # Get first user message
                    if first_user_msg is None and entry_type == 'user':
                        msg = entry.get('message', {})
                        content = msg.get('content', '')

                        if isinstance(content, str) and is_valid_prompt(content):
                            first_user_msg = content[:200]
                        elif isinstance(content, list):
                            for item in content:
                                if isinstance(item, dict) and item.get('type') == 'text':
                                    text = item.get('text', '')
                                    if is_valid_prompt(text):
                                        first_user_msg = text[:200]
                                        break

                    # For large files, stop after getting essential info
                    if i > 100 and (first_user_msg or session_summary):
                        # Still count remaining messages approximately
                        remaining = f.read()
                        self.message_count += remaining.count('\n')
                        break

                self.end_timestamp = last_timestamp
                # Use first user message, session summary, commands used, or fallback
                if first_user_msg:
                    self.first_prompt = first_user_msg
                elif session_summary:
                    self.first_prompt = session_summary
                elif commands_used:
                    self.first_prompt = f"[Commands: {', '.join(commands_used[:5])}]"
                else:
                    self.first_prompt = "(empty session)"

        except Exception as e:
            self.first_prompt = f"(error reading: {e})"

    @property
    def duration_str(self) -> str:
        """Get human-readable duration."""
        if self.timestamp and self.end_timestamp:
            delta = self.end_timestamp - self.timestamp
            minutes = int(delta.total_seconds() / 60)
            if minutes < 60:
                return f"{minutes}m"
            hours = minutes // 60
            mins = minutes % 60
            return f"{hours}h{mins}m"
        return ""

    @property
    def date_str(self) -> str:
        """Get formatted date string."""
        if self.timestamp:
            return self.timestamp.strftime('%Y-%m-%d %H:%M')
        return "unknown"

    @property
    def size_str(self) -> str:
        """Get human-readable file size."""
        if self.file_size < 1024:
            return f"{self.file_size}B"
        elif self.file_size < 1024 * 1024:
            return f"{self.file_size // 1024}KB"
        else:
            return f"{self.file_size // (1024 * 1024)}MB"


SUMMARY_CACHE_PATH = config.get_path('summary_cache') or Path.home() / '.claude' / 'transcript_summaries.json'


def load_summaries() -> dict:
    """Load cached summaries from disk."""
    if SUMMARY_CACHE_PATH.exists():
        try:
            with open(SUMMARY_CACHE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def scan_transcripts(base_dir: Path) -> list[TranscriptInfo]:
    """Scan for all transcript files."""
    transcripts = []

    console.print(f"[dim]Scanning {base_dir}...[/dim]")

    # Find all JSONL files, excluding agent-* files
    for project_dir in base_dir.iterdir():
        if not project_dir.is_dir():
            continue

        for jsonl_file in project_dir.glob("*.jsonl"):
            # Skip agent files (subagent logs)
            if jsonl_file.name.startswith("agent-"):
                continue

            info = TranscriptInfo(jsonl_file)
            # Skip empty sessions (no user interaction)
            if info.first_prompt == "(empty session)":
                continue

            transcripts.append(info)

    # Sort by timestamp (newest first)
    def sort_key(t):
        if t.timestamp is None:
            return datetime.min
        # Make timezone-naive for sorting
        if t.timestamp.tzinfo is not None:
            return t.timestamp.replace(tzinfo=None)
        return t.timestamp

    transcripts.sort(key=sort_key, reverse=True)

    # Load and apply summaries from cache
    summaries = load_summaries()
    for t in transcripts:
        if t.session_id in summaries:
            t.summary = summaries[t.session_id].get('summary')
            t.filename = summaries[t.session_id].get('filename')

    return transcripts


def truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis."""
    text = text.replace('\n', ' ').strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


class TranscriptBrowser:
    """Interactive transcript browser."""

    def __init__(self, transcripts: list[TranscriptInfo]):
        self.all_transcripts = transcripts
        self.filtered = transcripts.copy()
        self.selected: set[int] = set()  # Indices into filtered list
        self.current_page = 0
        self.page_size = 50
        self.search_term = ""
        self.project_filter = ""
        self.history = InMemoryHistory()

    @property
    def total_pages(self) -> int:
        return max(1, (len(self.filtered) + self.page_size - 1) // self.page_size)

    @property
    def current_slice(self) -> list[TranscriptInfo]:
        start = self.current_page * self.page_size
        end = start + self.page_size
        return self.filtered[start:end]

    def apply_filters(self):
        """Apply search and project filters."""
        self.filtered = []

        for t in self.all_transcripts:
            # Project filter
            if self.project_filter:
                if self.project_filter.lower() not in t.project_name.lower():
                    continue

            # Search filter
            if self.search_term:
                term = self.search_term.lower()
                summary_text = t.summary or ""
                searchable = f"{t.first_prompt} {summary_text} {t.project_name} {t.slug} {t.git_branch}".lower()
                if term not in searchable:
                    continue

            self.filtered.append(t)

        self.current_page = 0
        self.selected.clear()

    def render_table(self) -> Table:
        """Render the transcript table."""
        table = Table(
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
            expand=True,
        )

        table.add_column("", width=3)  # Selection
        table.add_column("#", width=4, justify="right")
        table.add_column("Date", width=16)
        table.add_column("Project", width=20)
        table.add_column("First Prompt", ratio=1, no_wrap=True, overflow="ellipsis")
        table.add_column("Msgs", width=5, justify="right")
        table.add_column("Size", width=6, justify="right")
        table.add_column("Dur", width=6, justify="right")

        start_idx = self.current_page * self.page_size

        for i, t in enumerate(self.current_slice):
            global_idx = start_idx + i
            is_selected = global_idx in self.selected

            sel_mark = "[green]✓[/green]" if is_selected else " "
            row_style = "on dark_green" if is_selected else None

            # Use summary if available, otherwise first prompt
            description = getattr(t, 'summary', None) or t.first_prompt
            description = description.replace('\n', ' ').strip()

            table.add_row(
                sel_mark,
                str(global_idx + 1),
                t.date_str,
                truncate(t.project_name, 20),
                description,
                str(t.message_count),
                t.size_str,
                t.duration_str,
                style=row_style
            )

        return table

    def render_status(self) -> Text:
        """Render status bar."""
        text = Text()
        text.append(f"Page {self.current_page + 1}/{self.total_pages} ", style="bold")
        text.append(f"| {len(self.filtered)} transcripts ", style="dim")

        if self.selected:
            text.append(f"| {len(self.selected)} selected ", style="green bold")

        if self.search_term:
            text.append(f"| Search: '{self.search_term}' ", style="yellow")

        if self.project_filter:
            text.append(f"| Project: '{self.project_filter}' ", style="cyan")

        return text

    def render_help(self) -> Text:
        """Render help bar."""
        text = Text()
        commands = [
            ("n/p", "next/prev page"),
            ("s", "search"),
            ("f", "filter project"),
            ("c", "clear filters"),
            ("#", "toggle select"),
            ("a", "select all page"),
            ("v", "view"),
            ("e", "export selected"),
            ("q", "quit"),
        ]
        for i, (key, desc) in enumerate(commands):
            if i > 0:
                text.append(" | ", style="dim")
            text.append(key, style="bold cyan")
            text.append(f" {desc}", style="dim")
        return text

    def display(self):
        """Display the current view."""
        console.clear()

        console.print(Panel(
            "[bold]Claude Transcript Browser[/bold]",
            style="blue"
        ))

        console.print(self.render_table())
        console.print()
        console.print(self.render_status())
        console.print(self.render_help())
        console.print()

    def toggle_select(self, abs_id: int):
        """Toggle selection of an item by absolute ID (1-based)."""
        idx = abs_id - 1  # Convert 1-based ID to 0-based index
        if 0 <= idx < len(self.filtered):
            if idx in self.selected:
                self.selected.remove(idx)
            else:
                self.selected.add(idx)

    def select_all_page(self):
        """Select/deselect all on current page."""
        start = self.current_page * self.page_size
        end = min(start + self.page_size, len(self.filtered))
        page_indices = set(range(start, end))

        # If all selected, deselect all; otherwise select all
        if page_indices.issubset(self.selected):
            self.selected -= page_indices
        else:
            self.selected |= page_indices

    def view_transcript(self, abs_id: int):
        """View a transcript's first prompt in detail."""
        idx = abs_id - 1  # Convert 1-based ID to 0-based index
        if 0 <= idx < len(self.filtered):
            t = self.filtered[idx]
            console.clear()
            console.print(Panel(
                f"[bold]{t.project_name}[/bold] - {t.date_str}",
                title="Transcript Details"
            ))
            console.print(f"[dim]File:[/dim] {t.path}")
            console.print(f"[dim]Session:[/dim] {t.session_id}")
            console.print(f"[dim]Slug:[/dim] {t.slug}")
            console.print(f"[dim]Branch:[/dim] {t.git_branch}")
            console.print(f"[dim]CWD:[/dim] {t.cwd}")
            console.print(f"[dim]Messages:[/dim] {t.message_count}")
            console.print(f"[dim]Size:[/dim] {t.size_str}")
            console.print(f"[dim]Duration:[/dim] {t.duration_str}")
            console.print()
            console.print(Panel(t.first_prompt, title="First Prompt"))
            console.print()
            Prompt.ask("[dim]Press Enter to continue[/dim]")

    def export_selected(self, output_dir: Path):
        """Export selected transcripts."""
        if not self.selected:
            console.print("[yellow]No transcripts selected![/yellow]")
            Prompt.ask("[dim]Press Enter to continue[/dim]")
            return

        output_dir.mkdir(parents=True, exist_ok=True)

        console.print(f"\n[bold]Exporting {len(self.selected)} transcripts to {output_dir}[/bold]\n")

        # Get export options
        show_tools = Confirm.ask("Include tool calls?", default=False)
        show_thinking = Confirm.ask("Include thinking blocks?", default=False)

        exported = []
        for idx in sorted(self.selected):
            t = self.filtered[idx]

            # Create project subdirectory
            project_slug = re.sub(r'[^\w\-]', '_', t.project_name)
            project_dir = output_dir / project_slug
            project_dir.mkdir(parents=True, exist_ok=True)

            # Generate output filename: YYYYMMDD_short-description.md
            date_prefix = t.timestamp.strftime('%Y%m%d') if t.timestamp else 'unknown'
            filename_slug = t.filename or t.session_id[:8]
            output_name = f"{date_prefix}_{filename_slug}.md"
            output_path = project_dir / output_name

            console.print(f"  Exporting: {t.project_name}/{output_name}")

            try:
                format_jsonl(
                    str(t.path),
                    str(output_path),
                    show_tools=show_tools,
                    show_thinking=show_thinking,
                    show_timestamps=True,
                    show_status=False,
                    title=t.filename,
                    description=t.summary
                )
                exported.append(output_path)
            except Exception as e:
                console.print(f"    [red]Error: {e}[/red]")

        console.print(f"\n[green]Exported {len(exported)} transcripts to {output_dir}[/green]")
        Prompt.ask("[dim]Press Enter to continue[/dim]")

    def run(self):
        """Run the interactive browser."""
        output_dir = config.get_path('export_dir') or Path.cwd() / "exports"

        # Get unique project names for completion
        project_names = list(set(t.project_name for t in self.all_transcripts))
        project_completer = WordCompleter(project_names, ignore_case=True)

        while True:
            self.display()

            try:
                cmd = pt_prompt(
                    "Command: ",
                    history=self.history,
                ).strip().lower()
            except (KeyboardInterrupt, EOFError):
                break

            if not cmd:
                continue

            if cmd == 'q' or cmd == 'quit':
                break

            elif cmd == 'n' or cmd == 'next':
                if self.current_page < self.total_pages - 1:
                    self.current_page += 1

            elif cmd == 'p' or cmd == 'prev':
                if self.current_page > 0:
                    self.current_page -= 1

            elif cmd == 's' or cmd == 'search':
                try:
                    self.search_term = pt_prompt("Search: ", history=self.history)
                    self.apply_filters()
                except (KeyboardInterrupt, EOFError):
                    pass

            elif cmd == 'f' or cmd == 'filter':
                try:
                    self.project_filter = pt_prompt(
                        "Project filter: ",
                        completer=project_completer,
                        history=self.history
                    )
                    self.apply_filters()
                except (KeyboardInterrupt, EOFError):
                    pass

            elif cmd == 'c' or cmd == 'clear':
                self.search_term = ""
                self.project_filter = ""
                self.apply_filters()

            elif cmd == 'a' or cmd == 'all':
                self.select_all_page()

            elif cmd == 'e' or cmd == 'export':
                try:
                    dir_input = pt_prompt(
                        f"Export directory [{output_dir}]: ",
                        history=self.history
                    ).strip()
                    if dir_input:
                        output_dir = Path(dir_input).expanduser()
                    self.export_selected(output_dir)
                except (KeyboardInterrupt, EOFError):
                    pass

            elif cmd.startswith('v') and len(cmd) > 1:
                # View specific item: v1, v2, etc.
                try:
                    idx = int(cmd[1:])
                    self.view_transcript(idx)
                except ValueError:
                    pass

            elif cmd == 'v':
                # Prompt for which to view
                try:
                    idx_str = pt_prompt("View # (by ID): ")
                    idx = int(idx_str)
                    self.view_transcript(idx)
                except (ValueError, KeyboardInterrupt, EOFError):
                    pass

            elif cmd.isdigit():
                # Toggle selection by absolute ID
                abs_id = int(cmd)
                if 1 <= abs_id <= len(self.filtered):
                    self.toggle_select(abs_id)

            elif '-' in cmd:
                # Range selection by absolute IDs: 1-5
                try:
                    start, end = cmd.split('-')
                    for abs_id in range(int(start), int(end) + 1):
                        if 1 <= abs_id <= len(self.filtered):
                            self.selected.add(abs_id - 1)  # Convert to 0-based index
                except (ValueError, KeyError):
                    pass


def main():
    parser = argparse.ArgumentParser(
        description='Interactive Claude transcript browser',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands in browser:
    n/p         Next/previous page
    s           Search transcripts
    f           Filter by project
    c           Clear filters
    #           Toggle selection by absolute ID (e.g., 42)
    #-#         Range select by absolute IDs (e.g., 1-5)
    a           Select/deselect all on page
    v#          View transcript details (e.g., v42)
    e           Export selected to markdown
    q           Quit

Examples:
    python browse_transcripts.py
    python browse_transcripts.py --dir /custom/path/to/projects
        """
    )
    parser.add_argument(
        '--dir', '-d',
        default=config.get_path('claude_projects') or Path.home() / '.claude' / 'projects',
        type=Path,
        help='Base directory for Claude projects (default: ~/.claude/projects)'
    )

    args = parser.parse_args()

    if not args.dir.exists():
        console.print(f"[red]Directory not found: {args.dir}[/red]")
        sys.exit(1)

    console.print("[bold blue]Claude Transcript Browser[/bold blue]\n")

    with console.status("Scanning transcripts..."):
        transcripts = scan_transcripts(args.dir)

    if not transcripts:
        console.print("[yellow]No transcripts found![/yellow]")
        sys.exit(0)

    console.print(f"Found [green]{len(transcripts)}[/green] transcripts\n")

    browser = TranscriptBrowser(transcripts)
    browser.run()

    console.print("\n[dim]Goodbye![/dim]")


if __name__ == '__main__':
    main()
