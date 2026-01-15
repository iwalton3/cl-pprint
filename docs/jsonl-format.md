# JSONL Log Format Reference

This document details the structure of Claude Code JSONL conversation logs.

## File Location

- **Unix/macOS**: `~/.claude/projects/<project-dir>/<session>.jsonl`
- **Windows**: `%APPDATA%\Claude\projects\<project-dir>\<session>.jsonl`

## Entry Types

### User/Assistant Messages

Standard conversation messages with content arrays:

```json
{
  "type": "user",
  "message": {
    "content": [
      {"type": "text", "text": "Hello, can you help me?"}
    ]
  }
}
```

```json
{
  "type": "assistant",
  "message": {
    "content": [
      {"type": "text", "text": "Of course! What do you need?"},
      {"type": "tool_use", "id": "tool_123", "name": "Read", "input": {"file_path": "/path/to/file"}}
    ]
  }
}
```

### Tool Results

Tool results come in subsequent user messages:

```json
{
  "type": "user",
  "message": {
    "content": [
      {"type": "tool_result", "tool_use_id": "tool_123", "content": [{"type": "text", "text": "file contents..."}]}
    ]
  }
}
```

**Important**: Tool calls and results are separate entries. Match them by `tool_use_id`.

### Queue Operations

User messages sent during agent processing:

```json
{
  "type": "queue-operation",
  "operation": "enqueue",
  "content": "Stop and do this instead"
}
```

**Note**: Content is a direct string, NOT a content array like regular messages.

### Summary Entries

Compacted/resumed sessions or branching metadata:

```json
{
  "type": "summary",
  "data": {
    "type": "summary",
    "summary": "Description of conversation content"
  },
  "leafUuid": "abc123-def456"
}
```

**Branching**: Parent files with only `type: "summary"` entries are pointers to leaf conversation files.

### File History Snapshots

Metadata only - no conversation content:

```json
{
  "type": "file-history-snapshot",
  "timestamp": "2025-12-30T10:00:00Z",
  "files": [
    {"path": "format_jsonl.py", "version": 3}
  ]
}
```

## Empty/Metadata-Only Sessions

Not all JSONL files contain actual conversation content. Sessions may be:
- Created but immediately closed
- Branching metadata pointing to leaf files
- Only file-history-snapshot entries

**Detection**:
```python
def has_conversation_content(entries):
    """Check if session has actual messages."""
    for entry in entries:
        entry_type = entry.get('type') or entry.get('data', {}).get('type')
        if entry_type in ('user', 'assistant'):
            return True
    return False
```

## Processing Tips

1. **Tool ID Tracking**: Maintain a `tool_id_to_name` dict across messages
2. **System Reminders**: Strip `<system-reminder>` blocks from tool results using non-greedy regex
3. **Content Extraction**: Handle both array and direct string content formats
4. **Branching Detection**: Check for files containing only summary entries with `leafUuid`
