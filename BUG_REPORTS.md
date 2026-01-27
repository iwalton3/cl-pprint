# Bug Reports

Unfixed bugs identified during development sessions.

## Windows Path Slug Conversion (FIXED)

**Severity**: High - Prevented project discovery on Windows
**Status**: Fixed
**Found in**: Session 2025-01

### Description

The path-to-directory-slug conversion in `cl_dream.py` only converted forward slashes (`/`) to dashes, but Windows paths use backslashes (`\`). This caused "No Claude project dir found" errors on Windows.

**Note**: Claude Code uses `~/.claude/` on all platforms (Windows, Linux, macOS), so the data directory path is NOT the issue. The issue was only in the path slug conversion when matching project directories.

### Original Buggy Code

```python
# Only replaced forward slashes, missing backslashes
path_slug = str(project_path.resolve()).replace('/', '-').replace('.', '-').lstrip('-')
```

### Fix Applied

```python
# Now handles both path separators and Windows drive letters
resolved_path = str(project_path.resolve())
path_slug = resolved_path.replace('\\', '/').replace('/', '-').replace('.', '-')
path_slug = path_slug.replace(':', '')  # Remove drive colon (C: -> C)
path_slug = path_slug.lstrip('-')
```

### Files Modified

- `cl_dream.py` - Path slug conversion in `find_matching_project_dirs()`
- `cl_dream.py` - Partial path matching filter for Windows drive letters
- `format_jsonl.py` - Plan file detection now normalizes path separators

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
