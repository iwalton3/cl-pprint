#!/usr/bin/env python3
"""
Web-based Claude transcript browser.

A simple Python server with SPA frontend for browsing Claude Code transcripts.

Usage:
    python browse_web.py [--port 8080] [--no-browser]
"""

import argparse
import json
import mimetypes
import os
import re
import socket
import webbrowser
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

# Import existing modules
from format_jsonl import format_jsonl
import config


class TranscriptInfo:
    """Metadata about a transcript file (simplified from browse_transcripts.py)."""

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
        self.summary: Optional[str] = None
        self.filename: Optional[str] = None
        self._load_metadata()

    def _parse_project_name(self, dir_name: str) -> str:
        """Convert project directory name to readable format."""
        parts = dir_name.split('-')
        if len(parts) > 1:
            skip = set(config.get('project_name_skip_dirs', []))
            meaningful = [p for p in parts if p and p not in skip]
            if meaningful:
                return '/'.join(meaningful[-2:])
        return dir_name

    def _load_metadata(self):
        """Load metadata from the JSONL file."""
        self.file_size = self.path.stat().st_size

        try:
            with open(self.path, 'r') as f:
                first_user_msg = None
                session_summary = None
                last_timestamp = None
                commands_used = []

                def is_valid_prompt(text):
                    if not text or not text.strip():
                        return False
                    if '<command-name>' in text:
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

                    ts = entry.get('timestamp')
                    if ts:
                        try:
                            parsed_ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                            if self.timestamp is None:
                                self.timestamp = parsed_ts
                            last_timestamp = parsed_ts
                        except:
                            pass

                    if i < 50:
                        if not self.slug:
                            self.slug = entry.get('slug', '')
                        if not self.git_branch:
                            self.git_branch = entry.get('gitBranch', '')
                        if not self.cwd:
                            self.cwd = entry.get('cwd', '')
                        if not self.version:
                            self.version = entry.get('version', '')

                    if entry_type == 'summary' and not session_summary:
                        summary_text = entry.get('summary', '')
                        if summary_text and len(summary_text) > 5:
                            session_summary = f"[Resumed] {summary_text[:200]}"

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

                    if i > 100 and (first_user_msg or session_summary):
                        remaining = f.read()
                        self.message_count += remaining.count('\n')
                        break

                self.end_timestamp = last_timestamp
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
        if self.timestamp:
            return self.timestamp.strftime('%b %d, %H:%M')
        return "unknown"

    @property
    def date_iso(self) -> str:
        if self.timestamp:
            return self.timestamp.isoformat()
        return ""

    @property
    def size_str(self) -> str:
        if self.file_size < 1024:
            return f"{self.file_size}B"
        elif self.file_size < 1024 * 1024:
            return f"{self.file_size // 1024}KB"
        else:
            return f"{self.file_size // (1024 * 1024)}MB"

    def to_dict(self) -> dict:
        return {
            'session_id': self.session_id,
            'project': self.project_name,
            'project_dir': self.project_dir,
            'date': self.date_iso,
            'date_str': self.date_str,
            'title': self.filename or self.session_id[:8],
            'description': self.summary or self.first_prompt,
            'first_prompt': self.first_prompt,
            'message_count': self.message_count,
            'file_size': self.file_size,
            'size_str': self.size_str,
            'duration_str': self.duration_str,
            'slug': self.slug,
            'git_branch': self.git_branch,
        }


SUMMARY_CACHE_PATH = config.get_path('summary_cache') or Path.home() / '.claude' / 'transcript_summaries.json'


def load_summaries() -> dict:
    """Load cached summaries from disk."""
    if SUMMARY_CACHE_PATH.exists():
        try:
            with open(SUMMARY_CACHE_PATH, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def scan_transcripts(base_dir: Path) -> list[TranscriptInfo]:
    """Scan for all transcript files."""
    transcripts = []

    for project_dir in base_dir.iterdir():
        if not project_dir.is_dir():
            continue

        for jsonl_file in project_dir.glob("*.jsonl"):
            if jsonl_file.name.startswith("agent-"):
                continue

            info = TranscriptInfo(jsonl_file)
            if info.first_prompt == "(empty session)":
                continue

            transcripts.append(info)

    def sort_key(t):
        if t.timestamp is None:
            return datetime.min
        if t.timestamp.tzinfo is not None:
            return t.timestamp.replace(tzinfo=None)
        return t.timestamp

    transcripts.sort(key=sort_key, reverse=True)

    summaries = load_summaries()
    for t in transcripts:
        if t.session_id in summaries:
            t.summary = summaries[t.session_id].get('summary')
            t.filename = summaries[t.session_id].get('filename')

    return transcripts


# Global transcript cache
_transcripts: list[TranscriptInfo] = []
_transcripts_by_id: dict[str, TranscriptInfo] = {}


def init_transcripts(base_dir: Path):
    """Initialize transcript cache."""
    global _transcripts, _transcripts_by_id
    print(f"Scanning transcripts in {base_dir}...")
    _transcripts = scan_transcripts(base_dir)
    _transcripts_by_id = {t.session_id: t for t in _transcripts}
    print(f"Found {len(_transcripts)} transcripts")


class TranscriptHandler(SimpleHTTPRequestHandler):
    """HTTP request handler with API endpoints."""

    def __init__(self, *args, static_dir=None, **kwargs):
        self.static_dir = static_dir or Path(__file__).parent / 'static'
        super().__init__(*args, directory=str(self.static_dir), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # API endpoints
        if path == '/api/transcripts':
            self.handle_list_transcripts()
        elif path.startswith('/api/transcript/'):
            session_id = path.split('/')[-1]
            options = self._parse_tool_options(query)
            self.handle_get_transcript(session_id, **options)
        elif path.startswith('/api/download/'):
            session_id = path.split('/')[-1]
            options = self._parse_tool_options(query)
            self.handle_download(session_id, **options)
        elif path == '/' or path == '/index.html':
            # Serve index.html
            self.path = '/index.html'
            super().do_GET()
        elif path.startswith('/view/'):
            # SPA route - serve index.html
            self.path = '/index.html'
            super().do_GET()
        else:
            # Serve static files
            super().do_GET()

    def _parse_tool_options(self, query):
        """Parse tool display options from query parameters."""
        return {
            'show_tools': query.get('show_tools', ['0'])[0] == '1',
            'show_thinking': query.get('show_thinking', ['0'])[0] == '1',
            'truncate_tool_calls': query.get('truncate_tool_calls', ['1'])[0] == '1',
            'truncate_tool_results': query.get('truncate_tool_results', ['1'])[0] == '1',
            'exclude_edit_tools': query.get('exclude_edit_tools', ['0'])[0] == '1',
            'exclude_view_tools': query.get('exclude_view_tools', ['0'])[0] == '1',
            'show_explore_full': query.get('show_explore_full', ['0'])[0] == '1',
            'show_subagents_full': query.get('show_subagents_full', ['0'])[0] == '1',
        }

    def send_json(self, data: dict, status: int = 200):
        """Send JSON response."""
        body = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message: str, status: int = 400):
        """Send JSON error response."""
        self.send_json({'error': message}, status)

    def handle_list_transcripts(self):
        """GET /api/transcripts - List all transcripts."""
        data = {
            'transcripts': [t.to_dict() for t in _transcripts]
        }
        self.send_json(data)

    def handle_get_transcript(self, session_id: str, show_tools: bool = False, show_thinking: bool = False,
                               truncate_tool_calls: bool = True, truncate_tool_results: bool = True,
                               exclude_edit_tools: bool = False, exclude_view_tools: bool = False,
                               show_explore_full: bool = False, show_subagents_full: bool = False):
        """GET /api/transcript/<id> - Get formatted transcript content."""
        transcript = _transcripts_by_id.get(session_id)
        if not transcript:
            self.send_error_json('Transcript not found', 404)
            return

        try:
            markdown = format_jsonl(
                str(transcript.path),
                output_path=None,  # Return string
                show_tools=show_tools,
                show_thinking=show_thinking,
                show_timestamps=True,
                show_status=False,
                title=transcript.filename,
                description=transcript.summary,
                truncate_tool_calls=truncate_tool_calls,
                truncate_tool_results=truncate_tool_results,
                exclude_edit_tools=exclude_edit_tools,
                exclude_view_tools=exclude_view_tools,
                show_explore_full=show_explore_full,
                show_subagents_full=show_subagents_full
            )

            self.send_json({
                'session_id': session_id,
                'markdown': markdown,
                'title': transcript.filename or transcript.session_id[:8],
                'description': transcript.summary or transcript.first_prompt,
            })
        except Exception as e:
            self.send_error_json(f'Error formatting transcript: {e}', 500)

    def handle_download(self, session_id: str, show_tools: bool = False, show_thinking: bool = False,
                        truncate_tool_calls: bool = True, truncate_tool_results: bool = True,
                        exclude_edit_tools: bool = False, exclude_view_tools: bool = False,
                        show_explore_full: bool = False, show_subagents_full: bool = False):
        """GET /api/download/<id> - Download transcript as markdown file."""
        transcript = _transcripts_by_id.get(session_id)
        if not transcript:
            self.send_error_json('Transcript not found', 404)
            return

        try:
            markdown = format_jsonl(
                str(transcript.path),
                output_path=None,
                show_tools=show_tools,
                show_thinking=show_thinking,
                show_timestamps=True,
                show_status=False,
                title=transcript.filename,
                description=transcript.summary,
                truncate_tool_calls=truncate_tool_calls,
                truncate_tool_results=truncate_tool_results,
                exclude_edit_tools=exclude_edit_tools,
                exclude_view_tools=exclude_view_tools,
                show_explore_full=show_explore_full,
                show_subagents_full=show_subagents_full
            )

            # Generate filename: YYYYMMDD_[ai-filename].md
            date_prefix = transcript.timestamp.strftime('%Y%m%d') if transcript.timestamp else 'unknown'
            filename_slug = transcript.filename or transcript.session_id[:8]
            filename = f"{date_prefix}_{filename_slug}.md"

            body = markdown.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/markdown; charset=utf-8')
            self.send_header('Content-Length', len(body))
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')

            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_error_json(f'Error formatting transcript: {e}', 500)

    def log_message(self, format, *args):
        """Custom log format."""
        print(f"[{self.log_date_time_string()}] {args[0]}")


def make_handler(static_dir: Path):
    """Create handler class with static_dir bound."""
    class Handler(TranscriptHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, static_dir=static_dir, **kwargs)
    return Handler


def find_free_port(start_port: int = 8080, max_attempts: int = 100) -> int:
    """Find a free port starting from start_port."""
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"Could not find free port in range {start_port}-{start_port + max_attempts}")


def main():
    parser = argparse.ArgumentParser(
        description='Web-based Claude transcript browser',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--port', '-p',
        default=None,
        type=int,
        help='Port to listen on (default: auto-detect free port starting at 8080)'
    )
    parser.add_argument(
        '--no-browser',
        action='store_true',
        help='Do not open browser automatically'
    )
    parser.add_argument(
        '--dir', '-d',
        default=config.get_path('claude_projects') or Path.home() / '.claude' / 'projects',
        type=Path,
        help='Base directory for Claude projects (default: ~/.claude/projects)'
    )

    args = parser.parse_args()

    if not args.dir.exists():
        print(f"Error: Directory not found: {args.dir}")
        return 1

    # Initialize transcripts
    init_transcripts(args.dir)

    # Determine static directory
    static_dir = Path(__file__).parent / 'static'
    if not static_dir.exists():
        print(f"Error: Static directory not found: {static_dir}")
        return 1

    # Find available port
    port = args.port if args.port else find_free_port(8080)

    # Create server
    handler = make_handler(static_dir)
    server = HTTPServer(('127.0.0.1', port), handler)

    url = f'http://localhost:{port}'
    print(f"\nClaude Transcript Browser")
    print(f"Serving at: {url}")
    print("Press Ctrl+C to stop\n")

    # Open browser
    if not args.no_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()

    return 0


if __name__ == '__main__':
    exit(main())
