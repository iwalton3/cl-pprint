# Bug Reports

Unfixed bugs identified during development sessions.

## Windows Path Compatibility

**Severity**: Medium - Affects Windows users
**Status**: Unfixed (session interrupted)
**Found in**: Session 8a74d955-f9b3-4a5f-9118-8e4ef39c9d6a

### Description

The default paths in `config.py` use Unix-style paths (`~/.claude/projects`) which are incorrect for Windows systems. Claude Code stores data in `%APPDATA%\Claude` on Windows.

### Affected Files

- `config.py` - Lines 21-24 (DEFAULTS["paths"])
- `browse_web.py` - Lines 209, 465 (uses config.get_path())

### Current Behavior

```python
DEFAULTS = {
    "paths": {
        "claude_projects": "~/.claude/projects",
        "summary_cache": "~/.claude/transcript_summaries.json",
        # ...
    }
}
```

### Expected Behavior

```python
def _get_claude_data_dir():
    """Get platform-appropriate Claude data directory."""
    if sys.platform == 'win32':
        appdata = os.environ.get('APPDATA')
        if appdata:
            return Path(appdata) / 'Claude'
        return Path.home() / 'AppData' / 'Roaming' / 'Claude'
    return Path.home() / '.claude'

def _get_default_paths():
    data_dir = _get_claude_data_dir()
    return {
        "claude_projects": str(data_dir / "projects"),
        "summary_cache": str(data_dir / "transcript_summaries.json"),
        # ...
    }
```

### Workaround

Windows users can manually set correct paths in `config.json`:

```json
{
  "paths": {
    "claude_projects": "%APPDATA%/Claude/projects",
    "summary_cache": "%APPDATA%/Claude/transcript_summaries.json"
  }
}
```

### Notes

- `Path.home()` works cross-platform but directory conventions differ
- Windows: `%APPDATA%` (typically `C:\Users\<username>\AppData\Roaming`)
- macOS: `~/Library/Application Support` or `~/.appname`
- Linux: `~/.appname`

---

## Windows UTF-8 Encoding (FIXED)

**Severity**: High - Prevented file reading on Windows
**Status**: Fixed
**Found in**: Beta tester report (2025-01)

### Description

Windows uses `charmap` (cp1252) as the default encoding when opening files, not UTF-8. JSONL files with non-ASCII characters caused errors:

```
'charmap' codec can't decode byte 0x8f in position 4810: character maps to <undefined>
```

### Fix Applied

Added explicit `encoding='utf-8'` to all file open() calls in:
- `format_jsonl.py` (3 locations)
- `browse_transcripts.py` (2 locations)
- `browse_web.py` (2 locations)
- `summarize_transcripts.py` (3 locations)
- `summarize_transcripts_claude.py` (3 locations)
- `cl_dream.py` (5 locations)
- `config.py` (1 location)
- `prevent-chat-deletion.py` (2 locations)
