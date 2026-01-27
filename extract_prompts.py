#!/usr/bin/env python3
"""
Extract true user messages from Claude Code JSONL logs.

Usage:
    python extract_prompts.py <input.jsonl> [output.md]
    python extract_prompts.py --dir ~/.claude/projects  # Process all sessions
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


def format_timestamp(ts_str):
    """Convert ISO timestamp to readable format."""
    if not ts_str:
        return ""
    try:
        dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except:
        return ts_str


def extract_text_content(content):
    """Extract text from content which may be string or list of blocks."""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get('type') == 'text':
                texts.append(item.get('text', ''))
        return '\n'.join(texts) if texts else ''
    return ''


def is_system_content(text):
    """Check if text is system/agent content to filter out."""
    patterns = [
        r'<system-reminder>',
        r'caveat: the messages below were generated',
        r'do not respond to these messages',
        r'this session is being continued from a previous conversation',
        r'the conversation is summarized below',
        r'context was compacted',
        r'^implement the following plan:',  # Plan injection from resumed sessions
        r'^continue with the following plan:',
        r'^resume the following plan:',
    ]
    text_lower = text.lower().strip()
    return any(re.search(p, text_lower) for p in patterns)


def extract_prose(text):
    """Extract prose content, removing code blocks, logs, and pasted content."""
    # Remove code blocks
    text = re.sub(r'```[\s\S]*?```', '', text)

    # Remove inline code
    text = re.sub(r'`[^`]+`', '', text)

    # Remove XML/HTML tags and their content (error notifications, etc.)
    text = re.sub(r'<[^>]+>[\s\S]*?</[^>]+>', '', text)
    text = re.sub(r'<[^>]+/?>', '', text)

    # Remove lines that look like stack traces or logs
    lines = text.split('\n')
    prose_lines = []
    for line in lines:
        line_stripped = line.strip()

        # Skip empty lines
        if not line_stripped:
            continue

        # Skip stack trace lines
        if re.match(r'^(at |File "|Traceback|Error:|Exception:|Caused by:|WARNING:|INFO:|DEBUG:|WARN:|ERR:|panic:|\s+at )', line_stripped, re.IGNORECASE):
            continue

        # Skip lines that look like log output (timestamps, repeated patterns)
        if re.match(r'^\d{4}-\d{2}-\d{2}|\[\d+:\d+:\d+\]|^\s*\d+\s*\||^\d+:\d+:\d+', line_stripped):
            continue

        # Skip lines that look like file paths or URLs only
        if re.match(r'^(https?://|/[\w/.-]+\.\w+|[\w/\\.-]+:\d+)$', line_stripped):
            continue

        # Skip lines that look like shell output (prompts, command output indicators)
        if re.match(r'^[\$#>!%]\s|^==>|^---+$|^\*\*\*|^>>>', line_stripped):
            continue

        # Skip lines that look like error output patterns
        if re.match(r'^(npm ERR!|error\[|warning\[|failed to|cannot |could not |unable to )', line_stripped, re.IGNORECASE):
            continue

        # Skip lines that are indented (likely code or output)
        if line.startswith('    ') or line.startswith('\t'):
            continue

        # Skip lines that are mostly special characters (code/output)
        alphanumeric = sum(1 for c in line_stripped if c.isalnum() or c.isspace())
        if len(line_stripped) > 10 and alphanumeric / len(line_stripped) < 0.5:
            continue

        # Skip lines that look like JSON/object notation
        if re.match(r'^[\[\{"\']|[\]\},"\']$', line_stripped):
            continue

        prose_lines.append(line_stripped)

    return ' '.join(prose_lines)


def has_repeated_patterns(text, threshold=5):
    """Check if text has many repeated line prefixes (indicates pasted output)."""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if len(lines) < threshold:
        return False

    # Extract first word/prefix of each line
    prefixes = []
    for line in lines:
        # Get first word or first N chars
        match = re.match(r'^(\S+)', line)
        if match:
            prefixes.append(match.group(1))

    # Count prefix frequencies
    from collections import Counter
    prefix_counts = Counter(prefixes)

    # If any prefix appears more than threshold times, it's likely pasted output
    for prefix, count in prefix_counts.most_common(3):
        if count >= threshold:
            return True

    return False


def is_substantive(text, min_words=20, max_lines=30):
    """Check if prompt has substantive prose content (not just pasted errors/code)."""
    # Quick reject: too many lines indicates pasted content
    lines = text.split('\n')
    if len(lines) > max_lines:
        return False

    # Quick reject: contains shell/terminal artifacts
    junk_literals = [
        '% Total    % Received % Xferd',  # curl output
        ' && ',  # shell command chaining
        ' \\',  # line continuation (backslash)
        '~/',  # home directory paths
        'ERROR:',  # error output
        'Error:',  # error output
        '[+] Building',  # docker build output
        'FINISHED',  # docker/build output
        'CACHED',  # docker output
        'exit code',  # command output
        'Traceback',  # python errors
        'Network isolation configured',  # iclaude output
        'Setting up user:',  # iclaude output
        'Created user',  # iclaude output
        '--dangerously',  # claude flags
        'UID ',  # user output
        'useradd:',  # command output
        'usermod:',  # command output
        '=>',  # docker/build arrow output
        'dpkg-query:',  # package manager output
        'find /usr',  # shell commands
        '[Fine-grained]',  # debug logging
        '[SLOT]',  # debug logging
        'Object {',  # browser console object output
        'Array(',  # browser console array output
        '⚠ Line',  # warning output
    ]
    for literal in junk_literals:
        if literal in text:
            return False

    # Regex patterns for more complex matches
    if re.search(r'\d{6} \$', text):  # shell prompt with job number like "263906 $"
        return False
    if re.search(r'\d+:\d+:\d+\]', text):  # timestamps like [12:34:56]
        return False
    if re.search(r'^\s*\d+\s*\|', text, re.MULTILINE):  # line-numbered output
        return False
    if re.search(r'root@[\w-]+:.*#', text):  # container shell prompts
        return False
    if re.search(r'\[https?://[^\]]+\]', text):  # browser console URLs like [https://...]
        return False
    if re.search(r'\w+/\w+\.\w+:\d+:', text):  # file:line references like core/audio.py:184:
        return False
    if re.search(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}', text):  # ISO timestamps
        return False
    if re.search(r'^\s*[\{\[]', text) and re.search(r'[\}\]]\s*$', text):  # JSON objects/arrays
        return False

    # Quick reject: repeated patterns indicate pasted output
    if has_repeated_patterns(text, threshold=5):
        return False

    # Quick reject: if most of the text is in code blocks, it's likely pasted content
    code_block_chars = sum(len(m.group(0)) for m in re.finditer(r'```[\s\S]*?```', text))
    if len(text) > 100 and code_block_chars / len(text) > 0.5:
        return False

    prose = extract_prose(text)

    # Count words in prose
    words = prose.split()
    if len(words) < min_words:
        return False

    # Check ratio of prose to total content - if prose is tiny fraction, it's mostly pasted junk
    total_words = len(text.split())
    if total_words > 40 and len(words) / total_words < 0.4:
        return False

    # Check for sentence-like structure (has some punctuation suggesting complete thoughts)
    sentences = re.split(r'[.!?]', prose)
    meaningful_sentences = [s.strip() for s in sentences if len(s.strip().split()) >= 3]

    if len(meaningful_sentences) < 2:
        return False

    # Check that prose isn't dominated by error-like words
    error_indicators = ['error', 'failed', 'exception', 'traceback', 'warning', 'errno', 'stacktrace', 'npm', 'docker', 'committed']
    error_word_count = sum(1 for w in words if w.lower() in error_indicators)
    if len(words) > 0 and error_word_count / len(words) > 0.1:
        return False

    return True


def parse_command(text):
    """Parse command XML. Returns (command, should_filter)."""
    cmd_match = re.search(r'<command-name>([^<]+)</command-name>', text)
    if not cmd_match:
        return None, False

    cmd_name = cmd_match.group(1).strip()

    # Filter out utility commands that aren't real prompts
    irrelevant_commands = {'/usage', '/cost', '/help', '/clear', '/compact', '/config', '/quit', '/exit'}
    if cmd_name in irrelevant_commands:
        return None, True

    # Extract message/args for actual commands
    msg_match = re.search(r'<command-message>([^<]*)</command-message>', text)
    msg = msg_match.group(1).strip() if msg_match else ''

    args_match = re.search(r'<command-args>([^<]*)</command-args>', text)
    args = args_match.group(1).strip() if args_match else ''

    if args:
        return f"{cmd_name} {args}", False
    elif msg and msg != cmd_name.lstrip('/'):
        return f"{cmd_name}: {msg}", False
    else:
        return cmd_name, False


def extract_user_prompts(jsonl_path, substantive_only=False):
    """Extract user prompts from a JSONL file."""
    prompts = []

    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            entry_type = entry.get('type', '')
            msg = entry.get('message', {})
            role = msg.get('role', '')
            timestamp = entry.get('timestamp', '')

            # Handle queue-operation (user messages during tool execution)
            if entry_type == 'queue-operation' and entry.get('operation') == 'enqueue':
                content = entry.get('content', '')
                if content and content.strip() and not is_system_content(content):
                    if not substantive_only or is_substantive(content):
                        prompts.append({
                            'timestamp': format_timestamp(timestamp),
                            'text': content.strip()
                        })
                continue

            # Handle regular user messages
            if role != 'user':
                continue

            content = msg.get('content', [])
            text = extract_text_content(content)

            if not text or not text.strip():
                continue

            # Filter system content
            if is_system_content(text):
                continue

            # Check for tool_result entries (not actual user prompts)
            if isinstance(content, list):
                is_tool_result = any(
                    isinstance(item, dict) and item.get('type') == 'tool_result'
                    for item in content
                )
                if is_tool_result:
                    continue

            # Handle commands
            if '<command-name>' in text:
                cmd_text, should_filter = parse_command(text)
                if should_filter:
                    continue
                if cmd_text:
                    text = cmd_text

            # Strip system reminders that might be embedded
            text = re.sub(r'<system-reminder>.*?</system-reminder>', '', text, flags=re.DOTALL)
            text = text.strip()

            if text:
                if not substantive_only or is_substantive(text):
                    prompts.append({
                        'timestamp': format_timestamp(timestamp),
                        'text': text
                    })

    return prompts


def format_prompts_markdown(prompts, source_name=None, include_timestamps=False):
    """Format prompts as markdown."""
    lines = ['# User Prompts']
    if source_name:
        lines.append(f'\nSource: `{source_name}`')
    lines.append(f'\nTotal prompts: {len(prompts)}\n')
    lines.append('---\n')

    for i, prompt in enumerate(prompts, 1):
        if include_timestamps and prompt['timestamp']:
            lines.append(f'*{prompt["timestamp"]}*\n')
        lines.append(prompt['text'])
        lines.append('\n---\n')

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Extract user prompts from Claude JSONL logs')
    parser.add_argument('input', help='Input JSONL file or directory')
    parser.add_argument('output', nargs='?', default='prompts.md', help='Output markdown file')
    parser.add_argument('--dir', action='store_true', help='Process all JSONL files in directory tree')
    parser.add_argument('--substantive', action='store_true', help='Only include prompts with substantive prose (filters out error pastes, short commands)')
    parser.add_argument('--top', type=int, default=0, help='Only include top N prompts by length')

    args = parser.parse_args()

    input_path = Path(args.input).expanduser()

    if args.dir or input_path.is_dir():
        # Process all JSONL files (exclude agent-*.jsonl subagent files)
        all_prompts = []
        for jsonl_file in sorted(input_path.rglob('*.jsonl')):
            if jsonl_file.name.startswith('agent-'):
                continue
            prompts = extract_user_prompts(jsonl_file, substantive_only=args.substantive)
            all_prompts.extend(prompts)

        if not all_prompts:
            print("No prompts found", file=sys.stderr)
            sys.exit(1)

        # Sort by length (longest first) and limit if requested
        all_prompts.sort(key=lambda p: len(p['text']), reverse=True)
        if args.top > 0:
            all_prompts = all_prompts[:args.top]

        markdown = format_prompts_markdown(all_prompts, str(input_path))
    else:
        # Process single file
        if not input_path.exists():
            print(f"File not found: {input_path}", file=sys.stderr)
            sys.exit(1)

        prompts = extract_user_prompts(input_path, substantive_only=args.substantive)

        if not prompts:
            print("No user prompts found in file", file=sys.stderr)
            sys.exit(1)

        markdown = format_prompts_markdown(prompts, input_path.name)

    output_path = Path(args.output)
    output_path.write_text(markdown, encoding='utf-8')

    # Count depends on which branch we took
    count = len(all_prompts) if (args.dir or input_path.is_dir()) else len(prompts)
    print(f"Wrote {count} prompts to {output_path}")


if __name__ == '__main__':
    main()
