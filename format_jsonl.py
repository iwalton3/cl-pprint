#!/usr/bin/env python3
"""
Format Claude Code agent JSONL logs into a readable format.

Usage:
    python format_jsonl.py <input.jsonl> [output.md] [options]

Options:
    --show-tools      Show tool calls (hidden by default)
    --show-thinking   Show thinking blocks (hidden by default)
    --show-status     Show status messages like "Let me X" (hidden by default)
    --exclude-timestamps  Hide timestamps

Features:
    - Formats conversation with timestamps
    - Shows full user prompts and assistant responses
    - Extracts all plan files to plans/ folder (versioned if duplicates)
    - Formats AskUserQuestion interactions nicely with inline answers
    - Shows plan content on approval, rejected changes on rejection
    - Batches consecutive brief assistant messages
"""

import argparse
import difflib
import json
import os
import re
import sys
from collections import defaultdict
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
    """Extract text from tool_result/message content which may be string or list of blocks."""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get('type') == 'text':
                texts.append(item.get('text', ''))
        return '\n'.join(texts) if texts else str(content)
    return str(content)


def extract_answer_text(content):
    """Extract answer text from tool_result content."""
    return extract_text_content(content)


def parse_user_command(text):
    """Parse user command XML and return clean format, or None if should be filtered."""
    cmd_match = re.search(r'<command-name>([^<]+)</command-name>', text)
    if not cmd_match:
        return None, False

    cmd_name = cmd_match.group(1).strip()

    irrelevant_commands = {'/usage', '/cost', '/help', '/clear', '/compact', '/config'}
    if cmd_name in irrelevant_commands:
        return None, True

    msg_match = re.search(r'<command-message>([^<]*)</command-message>', text)
    msg = msg_match.group(1).strip() if msg_match else ''

    args_match = re.search(r'<command-args>([^<]*)</command-args>', text)
    args = args_match.group(1).strip() if args_match else ''

    if args:
        return f"**{cmd_name}** {args}", False
    elif msg and msg != cmd_name.lstrip('/'):
        return f"**{cmd_name}**: {msg}", False
    else:
        return f"**{cmd_name}**", False


def is_caveat_message(text):
    """Check if text is a caveat/system instruction message to filter out."""
    caveat_phrases = [
        'caveat: the messages below were generated',
        'do not respond to these messages',
    ]
    text_lower = text.lower()
    return any(phrase in text_lower for phrase in caveat_phrases)


def is_compaction_message(text):
    """Check if text is a session compaction/continuation message."""
    compaction_phrases = [
        'this session is being continued from a previous conversation',
        'the conversation is summarized below',
        'context was compacted',
        'conversation that ran out of context'
    ]
    text_lower = text.lower()
    return any(phrase in text_lower for phrase in compaction_phrases)


def is_status_message(text):
    """Check if text is a brief status message like 'Let me X' or 'I'll X'."""
    text = text.strip()
    if len(text) > 200:
        return False

    status_patterns = [
        r"^(Let me|I'll|I'm going to|I will|Now I'll|Now let me)\s+(check|read|look|search|explore|examine|update|write|create|modify|edit|fix|run|execute|call|use|launch|start|verify|add|remove)",
        r"^(Checking|Reading|Looking|Searching|Exploring|Examining|Updating|Writing|Creating|Modifying|Editing|Fixing|Running|Executing|Verifying|Adding|Removing)",
        r"^(Good|Great|Perfect|Excellent|Done|OK|Okay)[.!]?\s*$",
        r"^Now (I have|I'll|let me)",
        r"^(The|All|This)\s+\w+\s+(is|are|was|were|has|have)\s+(now|being|complete|done|found|included|captured|working)",
    ]

    for pattern in status_patterns:
        if re.match(pattern, text, re.IGNORECASE):
            return True
    return False


def is_brief_message(text, precedes_tool=False):
    """Check if text is brief enough to be batched."""
    text = text.strip()
    text_lower = text.lower()

    # Always batch these "action announcement" patterns if short enough
    action_patterns = [
        'now let me ', 'now update', 'now i\'ll ', 'now i will ',
        'let me ', 'i\'ll now ', 'i will now '
    ]
    is_action_announcement = any(text_lower.startswith(p) for p in action_patterns)

    # If it's an action announcement or precedes a tool, use SMS-like limit (~160 chars)
    if is_action_announcement or precedes_tool:
        if len(text) <= 200:  # Slightly more than SMS for flexibility
            return True

    # Standard brief check: under 300 chars, no headers, single paragraph
    if len(text) > 300:
        return False
    if text.startswith('#'):
        return False
    if '\n\n' in text:  # Multiple paragraphs
        return False
    return True


def format_ask_user_question_with_answer(tool_input, answer_content=None):
    """Format an AskUserQuestion tool call with its answer inline."""
    output = []
    questions = tool_input.get('questions', [])

    answer_text = extract_answer_text(answer_content) if answer_content else ""

    custom_answers = {}
    if answer_text:
        for match in re.finditer(r'"([^"]+)"="([^"]+)"', answer_text):
            custom_answers[match.group(1)[:50]] = match.group(2)

    for q in questions:
        header = q.get('header', '')
        question = q.get('question', '')
        options = q.get('options', [])
        multi = q.get('multiSelect', False)

        output.append(f"**{header}**: {question}")
        if multi:
            output.append("*(multiple selection allowed)*")
        output.append("")

        labels = [opt.get('label', '') for opt in options]
        answer_lower = answer_text.lower()

        custom_answer = None
        for q_key, ans in custom_answers.items():
            if q_key.lower() in question.lower()[:60]:
                custom_answer = ans
                break

        # Check if custom_answer matches an existing option
        matched_option_idx = None
        if custom_answer:
            for i, opt in enumerate(options):
                label = opt.get('label', '')
                if label.lower() == custom_answer.lower():
                    matched_option_idx = i
                    break

        for i, opt in enumerate(options, 1):
            label = opt.get('label', '')
            desc = opt.get('description', '')

            selected = False
            # Check if this option matches the custom_answer
            if matched_option_idx is not None and matched_option_idx == i - 1:
                selected = True
            elif answer_text and not custom_answer:
                if label.lower() in answer_lower:
                    other_labels_match = [l for l in labels if l != label and l.lower() in answer_lower]
                    if not other_labels_match or len(label) >= max(len(l) for l in other_labels_match):
                        selected = True

            if selected:
                output.append(f"{i}. <ins>**{label}**</ins>  ")
            else:
                output.append(f"{i}. **{label}**  ")
            if desc:
                output.append(f"   {desc}  ")

        # Only show custom if it didn't match an existing option
        if custom_answer and matched_option_idx is None:
            next_num = len(options) + 1
            output.append(f"{next_num}. <ins>**Custom:** {custom_answer}</ins>  ")
        output.append("")

    return '\n'.join(output)


def increase_header_levels(text):
    """Increase markdown header levels (# -> ##, ## -> ###, etc.)"""
    lines = text.split('\n')
    result = []
    for line in lines:
        if line.startswith('#'):
            result.append('#' + line)
        else:
            result.append(line)
    return '\n'.join(result)


def strip_list_number(text):
    """Strip leading list number (e.g., '1. ', '12. ') from text."""
    match = re.match(r'^(\s*)(\d+)\.\s+(.*)$', text)
    if match:
        return match.group(1) + match.group(3)  # indent + content without number
    return text


def is_only_renumbering(removed_line, added_line):
    """Check if two lines differ only by list numbering."""
    # Strip the +/- prefix
    removed = removed_line[1:] if removed_line.startswith('-') else removed_line
    added = added_line[1:] if added_line.startswith('+') else added_line

    # Check if both are numbered list items
    removed_match = re.match(r'^(\s*)(\d+)\.\s+(.*)$', removed)
    added_match = re.match(r'^(\s*)(\d+)\.\s+(.*)$', added)

    if removed_match and added_match:
        # Same indent and content, just different number
        return (removed_match.group(1) == added_match.group(1) and
                removed_match.group(3) == added_match.group(3))
    return False


def find_preceding_header(lines, line_num):
    """Find the most recent markdown header before line_num in the original content."""
    for i in range(line_num - 1, -1, -1):
        line = lines[i].strip()
        if line.startswith('#'):
            return line
    return None


def get_plan_diff(rejected_plan, approved_plan):
    """Generate a unified diff between rejected and approved plans."""
    if not rejected_plan or not approved_plan:
        return None

    rejected_lines = rejected_plan.splitlines(keepends=True)
    approved_lines = approved_plan.splitlines(keepends=True)

    # Keep original lines for header lookup
    original_lines = rejected_plan.splitlines()

    # Generate unified diff with 2 lines of context
    diff_lines = list(difflib.unified_diff(
        rejected_lines, approved_lines,
        fromfile='rejected', tofile='approved',
        lineterm='',
        n=2  # Only 2 lines of context before/after changes
    ))

    if not diff_lines:
        return None

    # Process diff lines, keeping @@ markers and adding header context
    raw_lines = []
    for line in diff_lines[2:]:  # Skip --- and +++ headers
        line = line.rstrip('\n')
        if line.startswith('@@'):
            # Parse the hunk header: @@ -start,count +start,count @@
            match = re.match(r'^(@@ -\d+,?\d* \+\d+,?\d* @@)', line)
            if match:
                hunk_info = match.group(1)
                # Get line number for header lookup
                line_match = re.match(r'^@@ -(\d+)', line)
                start_line = int(line_match.group(1)) - 1 if line_match else 0
                header = find_preceding_header(original_lines, start_line)
                raw_lines.append('')  # Blank line before section
                if header:
                    raw_lines.append(f'{hunk_info} {header}')
                else:
                    raw_lines.append(hunk_info)
            continue
        raw_lines.append(line)

    # Filter out pure renumbering changes
    # First, collect all - and + lines to find matching renumbering pairs
    removed_lines = {}  # content (without number) -> list of indices
    added_lines = {}    # content (without number) -> list of indices

    for i, line in enumerate(raw_lines):
        if line.startswith('-') and not line.startswith('---'):
            content = line[1:]  # Remove the -
            match = re.match(r'^(\s*)(\d+)\.\s+(.*)$', content)
            if match:
                key = (match.group(1), match.group(3))  # (indent, content without number)
                if key not in removed_lines:
                    removed_lines[key] = []
                removed_lines[key].append(i)
        elif line.startswith('+') and not line.startswith('+++'):
            content = line[1:]  # Remove the +
            match = re.match(r'^(\s*)(\d+)\.\s+(.*)$', content)
            if match:
                key = (match.group(1), match.group(3))  # (indent, content without number)
                if key not in added_lines:
                    added_lines[key] = []
                added_lines[key].append(i)

    # Find indices to skip (pure renumbering)
    skip_indices = set()
    for key in removed_lines:
        if key in added_lines:
            # Match removed with added (one-to-one)
            for rem_idx, add_idx in zip(removed_lines[key], added_lines[key]):
                skip_indices.add(rem_idx)
                skip_indices.add(add_idx)

    # Build result, skipping renumbering lines
    result = []
    for i, line in enumerate(raw_lines):
        if i not in skip_indices:
            result.append(line)

    # Clean up: remove orphaned context lines and empty sections
    # A context line is orphaned if it's not within 2 lines of a +/- line
    change_indices = set()
    for i, line in enumerate(result):
        if line.startswith('-') or line.startswith('+'):
            if not line.startswith('@@'):
                change_indices.add(i)

    final_result = []
    i = 0
    while i < len(result):
        line = result[i]

        # Check if this is a @@ header
        if line.startswith('@@'):
            # Look ahead to see if there's actual content before next @@ or end
            j = i + 1
            has_content = False
            while j < len(result) and not result[j].startswith('@@'):
                if j in change_indices:
                    has_content = True
                    break
                j += 1
            if has_content:
                final_result.append(line)
            i += 1
            continue

        # Empty lines: keep if near a change
        if line == '':
            near_change = any(abs(i - ci) <= 2 for ci in change_indices)
            if near_change:
                final_result.append(line)
            i += 1
            continue

        # Context lines (start with space): keep only if within 2 lines of a change
        if line.startswith(' '):
            near_change = any(abs(i - ci) <= 2 for ci in change_indices)
            if near_change:
                final_result.append(line)
            i += 1
            continue

        # Change lines (+/-): always keep
        final_result.append(line)
        i += 1

    if not final_result or all(not l.strip() or l.startswith('@@') for l in final_result):
        return None

    return '\n'.join(final_result)


def format_plan_result(content, plan_content=None, next_plan=None, is_approved=None, plan_index=None):
    """Format ExitPlanMode tool result with plan content."""
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get('type') == 'text':
                text += item.get('text', '')

    approved = is_approved if is_approved is not None else 'approved' in text.lower()
    rejected = 'reject' in text.lower() or 'denied' in text.lower()

    output = []

    if approved:
        output.append("✅ **Plan Approved**\n")
        if plan_content:
            # Show plan directly with increased header levels
            output.append(increase_header_levels(plan_content))
    elif rejected:
        output.append("❌ **Plan Rejected**\n")
        # Show rejection reason from user
        if text.strip():
            # Extract just the user's actual feedback, removing system text
            reason = text.strip()
            # Try to extract the user's actual feedback after "the user said:" pattern
            match = re.search(r'the user said:\s*(.+)', reason, re.IGNORECASE | re.DOTALL)
            if match:
                reason = match.group(1).strip()
            elif 'rejected' in reason.lower() or 'denied' in reason.lower():
                # Try other patterns
                match = re.search(r'(?:rejected|denied)[^:]*:\s*(.+)', reason, re.IGNORECASE | re.DOTALL)
                if match:
                    reason = match.group(1).strip()
            if reason and not reason.startswith("The user doesn't want"):
                output.append(f"**Reason:** {reason}\n")

        # Add placeholder for navigation links right after rejection reason
        if plan_index is not None:
            output.append(f"__NAV_REJECTED_PLAN_{plan_index}__")

        if plan_content and next_plan:
            diff_content = get_plan_diff(plan_content, next_plan)
            if diff_content:
                output.append("**Changes to next revision:**\n")
                output.append("```diff")
                output.append(diff_content)
                output.append("```")
            else:
                output.append("*(No significant content differences found)*")
        elif plan_content:
            # No future plan to compare - just note it was rejected
            output.append("*(Plan was rejected and revised)*")
    else:
        output.append(f"📋 **Plan Status:** {text[:500]}")

    return '\n'.join(output)


def parse_entries(input_path):
    """Parse JSONL file and return entries with tool tracking and plan content."""
    entries = []
    ask_user_questions = {}
    ask_user_answers = {}
    exit_plan_modes = {}
    plan_timeline = []  # List of (tool_id, plan_content, approved) in order

    current_plan_content = None

    # First pass: collect plan timeline with approval status
    with open(input_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                msg = entry.get('message', {})
                content = msg.get('content', [])
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict):
                            if item.get('type') == 'tool_use':
                                tool_name = item.get('name', '')
                                tool_id = item.get('id', '')

                                if tool_name == 'Write':
                                    inp = item.get('input', {})
                                    file_path = inp.get('file_path', '')
                                    if '/plans/' in file_path or file_path.endswith('-plan.md'):
                                        current_plan_content = inp.get('content', '')

                                elif tool_name == 'Edit':
                                    inp = item.get('input', {})
                                    file_path = inp.get('file_path', '')
                                    if '/plans/' in file_path or file_path.endswith('-plan.md'):
                                        if current_plan_content:
                                            old_str = inp.get('old_string', '')
                                            new_str = inp.get('new_string', '')
                                            if old_str and old_str in current_plan_content:
                                                current_plan_content = current_plan_content.replace(old_str, new_str, 1)

                                elif tool_name == 'ExitPlanMode':
                                    plan_timeline.append({
                                        'tool_id': tool_id,
                                        'content': current_plan_content,
                                        'approved': None  # Will be filled in
                                    })

                            elif item.get('type') == 'tool_result':
                                tool_id = item.get('tool_use_id', '')
                                result_text = str(item.get('content', ''))

                                # Check if this is ExitPlanMode result
                                for plan in plan_timeline:
                                    if plan['tool_id'] == tool_id and plan['approved'] is None:
                                        plan['approved'] = 'approved' in result_text.lower()
                                        break
            except:
                continue

    # Build exit_plan_modes with next plan info for diffing
    for i, plan in enumerate(plan_timeline):
        # For rejected plans, diff against the next plan (not necessarily approved)
        next_plan = None
        if not plan['approved'] and i + 1 < len(plan_timeline):
            next_plan = plan_timeline[i + 1]['content']

        exit_plan_modes[plan['tool_id']] = {
            'content': plan['content'],
            'next_plan': next_plan,
            'approved': plan['approved'],
            'plan_index': i
        }

    # Second pass: full parsing
    current_plan_content = None
    with open(input_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                entry['_line_num'] = line_num

                msg = entry.get('message', {})
                content = msg.get('content', [])
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict):
                            if item.get('type') == 'tool_use':
                                tool_name = item.get('name', '')
                                tool_id = item.get('id', '')

                                if tool_name == 'AskUserQuestion':
                                    ask_user_questions[tool_id] = item.get('input', {})

                            elif item.get('type') == 'tool_result':
                                tool_id = item.get('tool_use_id', '')
                                if tool_id in ask_user_questions:
                                    ask_user_answers[tool_id] = item.get('content', '')

                entry['_ask_user_questions'] = ask_user_questions.copy()
                entry['_ask_user_answers'] = ask_user_answers.copy()
                entry['_exit_plan_modes'] = exit_plan_modes
                entries.append(entry)
            except json.JSONDecodeError as e:
                entries.append({'_error': f"Line {line_num}: {e}", '_line_num': line_num})

    return entries, ask_user_questions, ask_user_answers, exit_plan_modes


def truncate_text(text, max_len=500):
    """Truncate text with indication of total length."""
    if not text or len(text) <= max_len:
        return text
    return text[:max_len] + f"\n... [{len(text)} chars total, truncated]"


def escape_code_block_content(text):
    """Escape content that will go inside a code block to prevent breaking the block."""
    if not text:
        return text
    # Replace ``` with escaped version inside code blocks
    return text.replace('```', '` ` `')


def strip_system_reminders(text):
    """Remove <system-reminder> blocks from text."""
    if not text:
        return text
    # Remove system-reminder tags and their content
    import re
    return re.sub(r'<system-reminder>.*?</system-reminder>', '', text, flags=re.DOTALL).strip()


def format_tool_input(tool_name, tool_input, truncate=True):
    """Format tool input in a readable way.

    Args:
        tool_name: Name of the tool
        tool_input: Input dict for the tool
        truncate: Whether to truncate long content
    """
    if not tool_input:
        return "(no input)"

    def maybe_truncate(text, max_len=500):
        return truncate_text(text, max_len) if truncate else text

    # Handle specific tools with cleaner formatting
    if tool_name == 'Write':
        file_path = tool_input.get('file_path', 'unknown')
        content = tool_input.get('content', '')
        # Escape content to prevent breaking code blocks
        escaped = escape_code_block_content(maybe_truncate(content, 300))
        return f"**File:** `{file_path}`\n```\n{escaped}\n```"

    elif tool_name == 'Edit':
        file_path = tool_input.get('file_path', 'unknown')
        old_string = tool_input.get('old_string', '')
        new_string = tool_input.get('new_string', '')
        # Escape content to prevent breaking code blocks
        old_escaped = escape_code_block_content(maybe_truncate(old_string, 200))
        new_escaped = escape_code_block_content(maybe_truncate(new_string, 200))
        result = f"**File:** `{file_path}`\n"
        result += f"**Old:**\n```\n{old_escaped}\n```\n"
        result += f"**New:**\n```\n{new_escaped}\n```"
        return result

    elif tool_name == 'Read':
        file_path = tool_input.get('file_path', 'unknown')
        offset = tool_input.get('offset')
        limit = tool_input.get('limit')
        result = f"**File:** `{file_path}`"
        if offset or limit:
            result += f" (offset: {offset}, limit: {limit})"
        return result

    elif tool_name == 'Bash':
        command = tool_input.get('command', '')
        desc = tool_input.get('description', '')
        result = ""
        if desc:
            result += f"**Description:** {desc}\n"
        escaped_cmd = escape_code_block_content(maybe_truncate(command, 500))
        result += f"```bash\n{escaped_cmd}\n```"
        return result

    elif tool_name == 'Grep':
        pattern = tool_input.get('pattern', '')
        path = tool_input.get('path', '.')
        output_mode = tool_input.get('output_mode', '')
        result = f"**Pattern:** `{pattern}` in `{path}`"
        if output_mode:
            result += f" (mode: {output_mode})"
        return result

    elif tool_name == 'Glob':
        pattern = tool_input.get('pattern', '')
        path = tool_input.get('path', '.')
        return f"**Pattern:** `{pattern}` in `{path}`"

    elif tool_name == 'Task':
        desc = tool_input.get('description', '')
        prompt = tool_input.get('prompt', '')
        subagent = tool_input.get('subagent_type', '')
        result = f"**Type:** {subagent}  \n**Description:** {desc}"
        if prompt:
            prompt_text = maybe_truncate(prompt, 500)
            # Format prompt as blockquote for better readability
            prompt_lines = prompt_text.split('\n')
            quoted_prompt = '\n'.join(f"> {line}" for line in prompt_lines)
            result += f"  \n**Prompt:**\n{quoted_prompt}"
        return result

    elif tool_name == 'TodoWrite':
        todos = tool_input.get('todos', [])
        if todos:
            max_todos = 10 if truncate else len(todos)
            items = [f"- [{t.get('status', '?')}] {t.get('content', '')}" for t in todos[:max_todos]]
            result = "\n".join(items)
            if truncate and len(todos) > 10:
                result += f"\n... and {len(todos) - 10} more"
            return result
        return "(empty todo list)"

    else:
        # Generic: show truncated JSON
        try:
            json_str = json.dumps(tool_input, indent=2)
            escaped = escape_code_block_content(maybe_truncate(json_str, 500))
            return f"```json\n{escaped}\n```"
        except:
            return maybe_truncate(str(tool_input), 500)


def format_tool_result(tool_name, result_content, is_error=False, truncate=True):
    """Format tool result in a readable way."""
    # Extract actual text from content blocks
    text = extract_text_content(result_content)

    # Strip system reminders from tool results (they're noise from the logging)
    text = strip_system_reminders(text)

    if not text:
        return "(empty result)"

    # Apply truncation if enabled
    def maybe_truncate(t, max_len=1000):
        return truncate_text(t, max_len) if truncate else t

    if is_error:
        escaped = escape_code_block_content(maybe_truncate(text))
        return f"```\n{escaped}\n```"

    # Task/agent results should be rendered as markdown (they contain analysis/summaries)
    if tool_name == 'Task':
        # Agent results are often markdown-formatted already
        output_text = maybe_truncate(text)
        # Indent as blockquote for visual separation
        lines = output_text.split('\n')
        return '\n'.join(f"> {line}" for line in lines)

    # File reading results - show as code
    if tool_name in ('Read', 'Bash', 'Grep', 'Glob'):
        escaped = escape_code_block_content(maybe_truncate(text))
        return f"```\n{escaped}\n```"

    # Default: show as preformatted if it looks like code/output, else as text
    if '\n' in text or text.startswith('{') or text.startswith('['):
        escaped = escape_code_block_content(maybe_truncate(text))
        return f"```\n{escaped}\n```"
    else:
        return maybe_truncate(text)


def extract_message_content(entry, show_tools=False, show_thinking=False,
                            ask_user_questions=None, ask_user_answers=None,
                            exit_plan_modes=None, tool_id_to_name=None,
                            tool_id_to_input=None,
                            truncate_tool_calls=True, truncate_tool_results=True,
                            exclude_edit_tools=False, exclude_view_tools=False,
                            show_explore_full=False, show_subagents_full=False):
    """Extract content parts from an entry. Returns (content_parts, is_brief, has_plan_result, content_type).

    tool_id_to_name: Dict that maps tool_use IDs to tool names. Passed in to track across messages.
    tool_id_to_input: Dict that maps tool_use IDs to tool inputs (for subagent type detection).
    truncate_tool_calls: If True, truncate tool inputs.
    truncate_tool_results: If True, truncate tool outputs.
    exclude_edit_tools: If True, hide Edit tool calls.
    exclude_view_tools: If True, hide Read/Grep/Glob tool calls.
    show_explore_full: If True, always show Explore agent calls in full (overrides truncation).
    show_subagents_full: If True, always show non-Explore subagent calls in full.
    content_type: 'text', 'tool_call', 'tool_result', or 'mixed' - indicates primary content type
    """
    if tool_id_to_name is None:
        tool_id_to_name = {}
    if tool_id_to_input is None:
        tool_id_to_input = {}

    if '_error' in entry:
        return [f"[ERROR] {entry['_error']}"], False, False, 'text'

    # Handle queue-operation entries (user messages sent during tool execution)
    if entry.get('type') == 'queue-operation' and entry.get('operation') == 'enqueue':
        queued_content = entry.get('content', '')
        if queued_content and queued_content.strip():
            return [queued_content], False, False, 'text'
        return [], False, False, 'text'

    msg = entry.get('message', {})
    content = msg.get('content', '')

    content_parts = []
    shown_tool_results = set()
    has_plan_result = False
    has_text = False
    has_tool_use = False
    has_tool_result = False

    if isinstance(content, str):
        if content.strip():
            if is_caveat_message(content):
                return [], False, False, 'text'
            if is_compaction_message(content):
                return ["__COMPACTION__"], False, False, 'text'
            if '<command-name>' in content:
                cmd_formatted, should_filter = parse_user_command(content)
                if should_filter:
                    return [], False, False, 'text'
                if cmd_formatted:
                    content_parts.append(cmd_formatted)
                    has_text = True
            elif '<local-command-stdout>' in content:
                return [], False, False, 'text'
            else:
                content_parts.append(content)
                has_text = True

    # Pre-check if message has tool uses (for precedes_tool logic)
    if isinstance(content, list):
        has_tool_use = any(
            isinstance(item, dict) and item.get('type') == 'tool_use'
            for item in content
        )

    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                content_parts.append(str(item))
                continue

            item_type = item.get('type', '')

            if item_type == 'text':
                text = item.get('text', '')
                if text.strip():
                    if is_caveat_message(text):
                        continue
                    if is_compaction_message(text):
                        content_parts.append("__COMPACTION__")
                        continue
                    content_parts.append(text)
                    has_text = True

            elif item_type == 'tool_use':
                tool_name = item.get('name', 'unknown')
                tool_input = item.get('input', {})
                tool_id = item.get('id', '')
                # Track tool name and input for result formatting
                tool_id_to_name[tool_id] = tool_name
                tool_id_to_input[tool_id] = tool_input

                # Check if this tool should be excluded
                if exclude_edit_tools and tool_name in ('Edit', 'Write'):
                    continue
                if exclude_view_tools and tool_name in ('Read', 'Grep', 'Glob'):
                    continue

                # Determine if this is an Explore or other subagent Task
                is_explore = False
                is_subagent = False
                if tool_name == 'Task':
                    subagent_type = tool_input.get('subagent_type', '')
                    is_explore = subagent_type == 'Explore'
                    is_subagent = bool(subagent_type) and not is_explore

                # Determine truncation for this tool call
                should_truncate_input = truncate_tool_calls
                if is_explore and show_explore_full:
                    should_truncate_input = False
                elif is_subagent and show_subagents_full:
                    should_truncate_input = False

                if tool_name == 'AskUserQuestion':
                    answer = ask_user_answers.get(tool_id) if ask_user_answers else None
                    content_parts.append("\n❓ **Question for User:**\n")
                    content_parts.append(format_ask_user_question_with_answer(tool_input, answer))
                    if answer:
                        shown_tool_results.add(tool_id)
                elif tool_name == 'ExitPlanMode':
                    content_parts.append("\n📋 **Submitting plan for approval...**")
                elif show_tools or (is_explore and show_explore_full) or (is_subagent and show_subagents_full):
                    has_tool_use = True
                    content_parts.append(f"\n📦 **Tool: {tool_name}**")
                    content_parts.append(format_tool_input(tool_name, tool_input, truncate=should_truncate_input))

            elif item_type == 'tool_result':
                tool_id = item.get('tool_use_id', '')
                is_error = item.get('is_error', False)
                result_content = item.get('content', '')
                has_tool_result = True

                if tool_id in shown_tool_results:
                    continue
                if ask_user_questions and tool_id in ask_user_questions:
                    continue

                # Check if this result's tool was excluded
                tool_name = tool_id_to_name.get(tool_id, 'unknown')
                if exclude_edit_tools and tool_name in ('Edit', 'Write'):
                    continue
                if exclude_view_tools and tool_name in ('Read', 'Grep', 'Glob'):
                    continue

                # Determine if this is an Explore or other subagent Task result
                is_explore = False
                is_subagent = False
                if tool_name == 'Task':
                    tool_input = tool_id_to_input.get(tool_id, {})
                    subagent_type = tool_input.get('subagent_type', '')
                    is_explore = subagent_type == 'Explore'
                    is_subagent = bool(subagent_type) and not is_explore

                # Determine truncation for this tool result
                should_truncate_result = truncate_tool_results
                if is_explore and show_explore_full:
                    should_truncate_result = False
                elif is_subagent and show_subagents_full:
                    should_truncate_result = False

                if exit_plan_modes and tool_id in exit_plan_modes:
                    plan_info = exit_plan_modes[tool_id]
                    content_parts.append("\n" + format_plan_result(
                        result_content,
                        plan_content=plan_info.get('content'),
                        next_plan=plan_info.get('next_plan'),
                        is_approved=plan_info.get('approved'),
                        plan_index=plan_info.get('plan_index')
                    ))
                    has_plan_result = True
                elif show_tools or (is_explore and show_explore_full) or (is_subagent and show_subagents_full):
                    status = "❌ Error" if is_error else "✅ Result"
                    content_parts.append(f"\n{status} ({tool_name}):\n")
                    content_parts.append(format_tool_result(tool_name, result_content, is_error, truncate=should_truncate_result))

            elif item_type == 'thinking':
                if show_thinking:
                    thinking_text = item.get('thinking', '')
                    if thinking_text:
                        content_parts.append("\n💭 **Thinking:**")
                        content_parts.append(thinking_text)

    # Determine if brief (pass precedes_tool if message has tool uses)
    full_text = '\n'.join(content_parts)
    is_brief = is_brief_message(full_text, precedes_tool=has_tool_use) and not has_plan_result

    # Determine content type for header selection
    if has_text and not has_tool_use and not has_tool_result:
        content_type = 'text'
    elif has_tool_use and not has_text:
        content_type = 'tool_call'
    elif has_tool_result and not has_text:
        content_type = 'tool_result'
    else:
        content_type = 'mixed'

    return content_parts, is_brief, has_plan_result, content_type


def format_jsonl(input_path, output_path=None, show_tools=False, show_thinking=False,
                 show_timestamps=True, show_status=False, title=None, description=None,
                 truncate_tool_calls=True, truncate_tool_results=True,
                 exclude_edit_tools=False, exclude_view_tools=False,
                 show_explore_full=False, show_subagents_full=False):
    """Format entire JSONL file.

    Args:
        input_path: Path to JSONL file
        output_path: Path to write markdown output (None for stdout)
        show_tools: Include tool calls in output
        show_thinking: Include thinking blocks in output
        show_timestamps: Include timestamps on messages
        show_status: Include brief status messages
        title: Custom title for the document (default: "Claude Agent Conversation Log")
        description: Description to show below title as blockquote
        truncate_tool_calls: Truncate tool inputs (default True)
        truncate_tool_results: Truncate tool outputs (default True)
        exclude_edit_tools: Hide Edit tool calls (default False)
        exclude_view_tools: Hide Read/Grep/Glob tool calls (default False)
        show_explore_full: Always show Explore agent calls in full (default False)
        show_subagents_full: Always show non-Explore subagent calls in full (default False)
    """
    output_lines = []

    # Use provided title or default
    if title:
        # Convert kebab-case to Title Case
        display_title = ' '.join(word.capitalize() for word in title.replace('-', ' ').split())
        output_lines.append(f"# {display_title}")
    else:
        output_lines.append("# Claude Agent Conversation Log")

    output_lines.append(f"**Source:** `{input_path}`\n")

    # Add description if provided
    if description:
        output_lines.append(f"> {description}\n")

    entries, ask_user_questions, ask_user_answers, exit_plan_modes = parse_entries(input_path)

    # Extract metadata from first valid entry
    for entry in entries:
        if '_error' not in entry:
            output_lines.append("## Session Metadata")
            metadata_items = [
                ("Session ID", entry.get('sessionId')),
                ("Agent ID", entry.get('agentId')),
                ("Slug", entry.get('slug')),
                ("Version", entry.get('version')),
                ("Working Directory", entry.get('cwd')),
                ("Git Branch", entry.get('gitBranch')),
            ]
            for label, value in metadata_items:
                if value:  # Only show if value exists
                    output_lines.append(f"- **{label}**: {value}")
            output_lines.append(f"- **Total Messages**: {len(entries)}")
            output_lines.append('')
            break

    output_lines.append('---\n')

    # Process entries with batching of brief assistant messages
    i = 0
    user_msg_index = 0
    plan_index_counter = 0
    user_has_plan = set()  # Track which user messages have plan results
    tool_id_to_name = {}  # Track tool_id -> tool_name across all messages
    tool_id_to_input = {}  # Track tool_id -> tool_input for subagent detection
    while i < len(entries):
        entry = entries[i]
        entry_type = entry.get('type', 'unknown')
        msg = entry.get('message', {})
        # queue-operation with enqueue are user messages
        if entry_type == 'queue-operation' and entry.get('operation') == 'enqueue':
            role = 'user'
        else:
            role = msg.get('role', entry_type)
        timestamp = format_timestamp(entry.get('timestamp'))

        content_parts, is_brief, has_plan, content_type = extract_message_content(
            entry, show_tools, show_thinking,
            ask_user_questions, ask_user_answers, exit_plan_modes, tool_id_to_name,
            tool_id_to_input,
            truncate_tool_calls, truncate_tool_results,
            exclude_edit_tools, exclude_view_tools,
            show_explore_full, show_subagents_full
        )

        if not content_parts:
            i += 1
            continue

        # Check for compaction marker
        if len(content_parts) == 1 and content_parts[0] == "__COMPACTION__":
            output_lines.append("## ♻️ Session Compacted\n")
            output_lines.append("---\n")
            i += 1
            continue

        # Check if we should batch consecutive brief assistant messages
        if role == 'assistant' and is_brief and not has_plan:
            brief_messages = ['\n'.join(content_parts).strip()]
            j = i + 1

            # Look ahead for more brief assistant messages
            while j < len(entries):
                next_entry = entries[j]
                next_msg = next_entry.get('message', {})
                next_role = next_msg.get('role', next_entry.get('type', ''))

                # Skip non-assistant entries that have no displayable content
                if next_role != 'assistant':
                    next_parts, _, _, _ = extract_message_content(
                        next_entry, show_tools, show_thinking,
                        ask_user_questions, ask_user_answers, exit_plan_modes, tool_id_to_name,
                        tool_id_to_input,
                        truncate_tool_calls, truncate_tool_results,
                        exclude_edit_tools, exclude_view_tools,
                        show_explore_full, show_subagents_full
                    )
                    if not next_parts:
                        # Empty entry (tool results, queue-operation, etc.) - skip
                        j += 1
                        continue
                    else:
                        # Has actual content - stop batching
                        break

                next_parts, next_brief, next_has_plan, _ = extract_message_content(
                    next_entry, show_tools, show_thinking,
                    ask_user_questions, ask_user_answers, exit_plan_modes, tool_id_to_name,
                    tool_id_to_input,
                    truncate_tool_calls, truncate_tool_results,
                    exclude_edit_tools, exclude_view_tools,
                    show_explore_full, show_subagents_full
                )

                if not next_parts:
                    j += 1
                    continue

                if next_brief and not next_has_plan:
                    brief_messages.append('\n'.join(next_parts).strip())
                    j += 1
                else:
                    break

            if len(brief_messages) > 1:
                # Output as batched progress section
                output_lines.append("## 🤖 Claude Progress")
                if show_timestamps and timestamp:
                    output_lines.append(f"*{timestamp}*\n")
                else:
                    output_lines.append("")

                for msg_text in brief_messages:
                    output_lines.append(msg_text)
                output_lines.append("\n---\n")
                i = j
                continue

        # Regular output - determine header based on content type
        # Use tool headers when showing tools OR when showing explore/subagent full
        show_tool_headers = show_tools or show_explore_full or show_subagents_full
        if show_tool_headers and content_type == 'tool_call':
            role_display = "📦 Tool Call"
        elif show_tool_headers and content_type == 'tool_result':
            role_display = "📦 Tool Result"
        elif role == 'user':
            role_display = "🧑 USER"
        else:
            role_display = "🤖 Claude"

        output_lines.append(f"## {role_display}")
        if show_timestamps and timestamp:
            output_lines.append(f"*{timestamp}*\n")
        else:
            output_lines.append("")

        # Add unique identifier to headers for navigation (only for user text messages)
        if role == 'user' and content_type != 'tool_result':
            # Check if this user message has a plan result
            has_plan = any('Plan Approved' in p or 'Plan Rejected' in p for p in content_parts)
            output_lines[-2] = f"## 🧑 USER #{user_msg_index}"  # Replace header with numbered version
            if has_plan:
                user_has_plan.add(user_msg_index)
            user_msg_index += 1

        output_lines.extend(content_parts)
        output_lines.append("\n---\n")
        i += 1

    result = '\n'.join(output_lines)

    # Post-process: add navigation links
    result = add_navigation_links(result, exit_plan_modes, user_has_plan)

    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(result)
        print(f"Output written to: {output_path}", file=sys.stderr)
    else:
        print(result)

    return result


def add_navigation_links(content, exit_plan_modes, user_has_plan):
    """Add navigation links after rejected/approved plans and long user sections."""
    lines = content.split('\n')

    # Build plan info: index -> approved
    plan_info = {}
    for tool_id, info in exit_plan_modes.items():
        idx = info.get('plan_index')
        if idx is not None:
            plan_info[idx] = info.get('approved', False)

    # Find the first approved plan index
    approved_indices = [idx for idx, approved in plan_info.items() if approved]
    first_approved_idx = min(approved_indices) if approved_indices else None

    # Find all plan and user header positions
    plan_positions = {}  # plan_index -> line_number
    user_positions = []  # [(line_number, user_index)]

    for i, line in enumerate(lines):
        # Match plan headers (approved or rejected)
        if '✅ **Plan Approved**' in line or '❌ **Plan Rejected**' in line:
            # Find the plan index from nearby NAV marker or count
            for j in range(i, min(i + 100, len(lines))):
                nav_match = re.match(r'^__NAV_REJECTED_PLAN_(\d+)__$', lines[j])
                if nav_match:
                    plan_positions[int(nav_match.group(1))] = i
                    break
            # For approved plans, check the order
            if '✅ **Plan Approved**' in line:
                for idx in sorted(plan_info.keys()):
                    if plan_info[idx] and idx not in plan_positions:
                        plan_positions[idx] = i
                        break

        # Match user headers
        user_match = re.match(r'^## 🧑 USER #(\d+)$', line)
        if user_match:
            user_positions.append((i, int(user_match.group(1))))

    # Replace navigation placeholders for rejected plans
    result_lines = []
    for i, line in enumerate(lines):
        match = re.match(r'^__NAV_REJECTED_PLAN_(\d+)__$', line)
        if match:
            plan_idx = int(match.group(1))
            nav_links = []

            # Link to next plan revision
            next_idx = plan_idx + 1
            if next_idx in plan_info:
                nav_links.append(f'[→ Next revision](#-user-{find_user_for_plan(plan_idx + 1, user_positions, plan_positions)})')

            # Link to first approved plan
            if first_approved_idx is not None and first_approved_idx > plan_idx:
                approved_user = find_user_for_plan(first_approved_idx, user_positions, plan_positions)
                if approved_user is not None:
                    nav_links.append(f'[✓ Approved plan](#-user-{approved_user})')

            if nav_links:
                result_lines.append('\n' + ' · '.join(nav_links) + '\n')
        else:
            result_lines.append(line)

    # Add link after approved plans to skip to next user message
    final_lines = []
    for i, line in enumerate(result_lines):
        final_lines.append(line)
        if '✅ **Plan Approved**' in line:
            # Find the next user message after this plan
            for line_num, user_idx in user_positions:
                if line_num > i:
                    final_lines.append(f'\n[⏭ Skip to next user message](#-user-{user_idx})\n')
                    break

    # Add skip links after user messages if >100 lines to next target
    # If user has plan, skip to next plan; otherwise skip to next user
    insertions = []
    for j, (start_pos, start_idx) in enumerate(user_positions):
        if start_idx in user_has_plan:
            # Find next plan position
            for plan_idx in sorted(plan_positions.keys()):
                if plan_positions[plan_idx] > start_pos:
                    # Link will be added after rejected/approved plan header
                    break
        else:
            # Find next user position
            if j + 1 < len(user_positions):
                end_pos, end_idx = user_positions[j + 1]
                if end_pos - start_pos > 100:
                    insertions.append((start_pos + 2, end_idx))

    # Apply insertions in reverse order
    for pos, target_idx in reversed(insertions):
        if pos < len(final_lines):
            final_lines.insert(pos, f'\n[⏭ Skip to next user message](#-user-{target_idx})\n')

    return '\n'.join(final_lines)


def find_user_for_plan(plan_idx, user_positions, plan_positions):
    """Find the user message index that contains a given plan."""
    if plan_idx not in plan_positions:
        return None
    plan_line = plan_positions[plan_idx]
    # Find the user message just before this plan
    for line_num, user_idx in reversed(user_positions):
        if line_num < plan_line:
            return user_idx
    return None


def main():
    parser = argparse.ArgumentParser(
        description='Format Claude Code agent JSONL logs into readable markdown.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python format_jsonl.py session.jsonl
    python format_jsonl.py session.jsonl output.md
    python format_jsonl.py session.jsonl --show-tools
    python format_jsonl.py session.jsonl output.md --show-tools --show-thinking
    python format_jsonl.py session.jsonl --exclude-timestamps
    python format_jsonl.py session.jsonl --show-explore-full
        """
    )
    parser.add_argument('input', help='Input JSONL file')
    parser.add_argument('output', nargs='?', help='Output markdown file (default: stdout)')
    parser.add_argument('--show-tools', action='store_true',
                        help='Show tool calls (hidden by default)')
    parser.add_argument('--show-thinking', action='store_true',
                        help='Show thinking blocks (hidden by default)')
    parser.add_argument('--show-status', action='store_true',
                        help='Show status messages like "Let me X" (hidden by default)')
    parser.add_argument('--exclude-timestamps', action='store_true',
                        help='Hide timestamps from output')
    # Truncation options
    parser.add_argument('--no-truncate-calls', action='store_true',
                        help='Show full tool call inputs without truncation')
    parser.add_argument('--no-truncate-results', action='store_true',
                        help='Show full tool results without truncation')
    # Exclusion options
    parser.add_argument('--exclude-edit-tools', action='store_true',
                        help='Hide Edit tool calls')
    parser.add_argument('--exclude-view-tools', action='store_true',
                        help='Hide Read/Grep/Glob tool calls')
    # Special agent options
    parser.add_argument('--show-explore-full', action='store_true',
                        help='Always show Explore agent calls in full (overrides truncation)')
    parser.add_argument('--show-subagents-full', action='store_true',
                        help='Always show non-Explore subagent calls in full')

    args = parser.parse_args()

    format_jsonl(
        args.input,
        args.output,
        show_tools=args.show_tools,
        show_thinking=args.show_thinking,
        show_timestamps=not args.exclude_timestamps,
        show_status=args.show_status,
        truncate_tool_calls=not args.no_truncate_calls,
        truncate_tool_results=not args.no_truncate_results,
        exclude_edit_tools=args.exclude_edit_tools,
        exclude_view_tools=args.exclude_view_tools,
        show_explore_full=args.show_explore_full,
        show_subagents_full=args.show_subagents_full
    )


if __name__ == '__main__':
    main()
