# TUI Patterns (Rich + prompt_toolkit)

Patterns for the terminal interface in `browse_transcripts.py`.

## Rich Table Formatting

### Let Rich Handle Truncation

Don't pre-truncate text - let Rich handle it:

```python
# BAD - hardcoded truncation ignores terminal width
table.add_row(truncate(text, 60))

# GOOD - Rich adapts to available space
table.add_column("Name", ratio=1, no_wrap=True, overflow="ellipsis")
table.add_row(text)
```

### Column Configuration

```python
table = Table()
table.add_column("#", style="cyan", width=4)
table.add_column("Date", width=10)
table.add_column("Summary", ratio=1, no_wrap=True, overflow="ellipsis")
```

## Selection Systems

### Absolute ID Pattern (WYSIWYT)

Display absolute IDs and accept absolute IDs for selection:

```python
# In render_table():
for idx, item in enumerate(self.filtered):
    abs_id = idx + 1  # 1-based
    table.add_row(str(abs_id), item.name, ...)

# In command handler:
if cmd.isdigit():
    abs_id = int(cmd)
    if 1 <= abs_id <= len(self.filtered):
        self.toggle_select(abs_id)
```

**Why**: Users type exactly what they see. No mental math required.

### Set-Based Selection

Use consistent ID system throughout:

```python
class Browser:
    def __init__(self):
        self.selected: set[int] = set()  # Absolute indices (0-based internally)

    def toggle_select(self, abs_id: int):
        """Toggle selection by absolute ID (1-based input)."""
        abs_idx = abs_id - 1
        if abs_idx in self.selected:
            self.selected.discard(abs_idx)
        else:
            self.selected.add(abs_idx)
```

## Pagination

### Page Size Considerations

When changing page size, update all related areas:

```python
self.page_size = 50  # The constant

# Help text - use "#" for absolute IDs, not "1-15"
("# or numbers", "toggle select by absolute ID")

# Remove page-relative validation if using absolute IDs
# if 1 <= idx <= self.page_size:  # Not needed

# CLI epilog
epilog = "Type a number to toggle selection (absolute ID)"
```

## prompt_toolkit Integration

### Command Input with History

```python
from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import InMemoryHistory

completer = WordCompleter(['export', 'filter', 'sort', 'help', 'quit'])
history = InMemoryHistory()

while True:
    try:
        cmd = pt_prompt('> ', completer=completer, history=history)
        handle_command(cmd)
    except KeyboardInterrupt:
        continue
    except EOFError:
        break
```

## Export Organization

### Hierarchical Export Paths

```python
def get_export_path(transcript):
    """Generate organized export path."""
    # Sanitize project name for filesystem
    project_slug = re.sub(r'[^\w\-]', '_', transcript.project_name)

    # Date prefix for sorting
    date_prefix = transcript.timestamp.strftime('%Y%m%d')

    # Use AI-generated filename or fallback
    filename_slug = transcript.filename or transcript.session_id[:8]

    return Path(f"exports/{project_slug}/{date_prefix}_{filename_slug}.md")
```

Output structure:
```
exports/
  project_name/
    20251213_short-descriptive-name.md
    20251214_another-descriptive-name.md
```
