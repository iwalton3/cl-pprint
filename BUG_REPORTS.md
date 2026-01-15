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
