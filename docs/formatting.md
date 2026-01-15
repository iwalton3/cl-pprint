# Formatting Guide

This document covers Markdown formatting patterns and gotchas for `format_jsonl.py`.

## Markdown Gotchas

### Indentation Triggers Code Blocks

4+ spaces at the start of a line triggers code block formatting in Markdown:

```python
# BAD - will render as code block
"    Description: This is indented too much"

# GOOD - use 2-3 spaces
"   Description: This works correctly"
```

### Triple Backticks in Content

File content containing ``` will break outer code blocks. Always escape:

```python
def escape_code_fences(text):
    """Escape triple backticks to prevent markdown code block breakage."""
    return text.replace('```', '` ` `')
```

### VS Code Anchor Format

VS Code auto-generates anchors from headers:
- Format: `#-lowercase-with-dashes`
- Leading `#` becomes `#-`
- Spaces become single dashes
- Special characters are stripped

Example: `## 🧑 USER #2` → anchor `#-user-2`

Custom `<a id="...">` tags do NOT work for navigation in VS Code preview.

### GFM Underline

GitHub Flavored Markdown requires `<ins>` not `<u>` for underline.

## Plan Tracking

### State Accumulation Pattern

Track Write and Edit operations to reconstruct plan state:

```python
plan_states = {}  # filename → current content

for entry in entries:
    if is_write_tool(entry):
        plan_states[filename] = content
    elif is_edit_tool(entry):
        plan_states[filename] = apply_edit(plan_states[filename], edit)
```

### Unified Diff Format

Standard diff format with section context:

```diff
@@ -66,10 +66,45 @@ ## IndexedDB Schema
- Old line
+ New line
  Context line
```

### Filtering Renumbering Changes

Detect and filter list renumbering noise:

```python
def is_renumber_only(old_line, new_line):
    """Check if diff pair is just list renumbering."""
    old_stripped = re.sub(r'^\d+\.\s*', '', old_line)
    new_stripped = re.sub(r'^\d+\.\s*', '', new_line)
    return old_stripped == new_stripped
```

## Message Batching

### Brief Message Detection

Batch consecutive brief assistant messages into "Progress" sections:

```python
def is_brief_message(text, char_limit=160):
    """Check if message should be batched."""
    if len(text) > char_limit:
        return False
    brief_patterns = ["Now let me", "Let me", "Now update"]
    return any(text.startswith(p) for p in brief_patterns)
```

### Skip Non-Content Entries

When batching, skip over:
- Empty user entries (tool results)
- Queue-operation entries
- File-history-snapshot entries

## Tool Display Options

### Option Hierarchy

Specific options override general ones:

```python
# Check if this is an Explore agent
is_explore = (tool_name == 'Task' and 'Explore' in description)

# Specific options override truncation
should_truncate = (
    options.get('truncate_calls', True) and
    not (is_explore and options.get('show_explore_full', False))
)

# Specific options override visibility
should_show = (
    options.get('show_tools', False) or
    (is_explore and options.get('show_explore_full', False))
)
```

### Tool Input Formatting

Show only essential info per tool type:
- **Write/Edit**: file path + first 200-300 chars of content
- **Read/Grep/Glob**: just the path/pattern
- **Bash**: description + truncated command
- **Task**: type + description + truncated prompt
