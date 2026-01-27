"""
Configuration loader for cl-pprint.

Loads settings from config.json if present, otherwise uses defaults.
"""

import json
import shutil
import sys
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"

# Default configuration
# Note: Claude Code uses ~/.claude on all platforms including Windows
DEFAULTS = {
    "ollama": {
        "model": "qwen3:30b-a3b-thinking-2507-q4_K_M",
        "url": "http://localhost:11434/api/generate",
        "timeout": 120,
        "temperature": 0.3,
        "max_tokens": 150
    },
    "paths": {
        "claude_projects": "~/.claude/projects",
        "summary_cache": "~/.claude/transcript_summaries.json",
        "export_dir": "./exports"
    },
    "project_name_skip_dirs": [
        "home", "working", "Users", "Desktop", "Documents",
        "source", "src", "projects", "repos"
    ],
    "dream": {
        "state_file": "~/.claude/dream_state.json",
        "sonnet_timeout": 300,
        "opus_timeout": 600
    }
}


def load_config() -> dict:
    """Load configuration from config.json, merging with defaults."""
    config = DEFAULTS.copy()

    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                user_config = json.load(f)

            # Deep merge user config into defaults
            for key, value in user_config.items():
                if key in config and isinstance(config[key], dict) and isinstance(value, dict):
                    config[key] = {**config[key], **value}
                else:
                    config[key] = value
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not load config.json: {e}")

    return config


# Load config once at import time
_config = load_config()


def get(key: str, default=None):
    """Get a config value by dot-notation key (e.g., 'ollama.model')."""
    keys = key.split('.')
    value = _config
    for k in keys:
        if isinstance(value, dict) and k in value:
            value = value[k]
        else:
            return default
    return value


def get_path(key: str) -> Path:
    """Get a path config value, expanding ~ to home directory."""
    value = get(f"paths.{key}")
    if value:
        return Path(value).expanduser()
    return None


def get_claude_cli() -> str:
    """Get the path to the Claude CLI executable.

    Checks common installation locations, then falls back to PATH.
    """
    # Check common installation locations (work on both Windows and Unix)
    possible_paths = [
        Path.home() / '.claude' / 'local' / 'claude.exe',
        Path.home() / '.claude' / 'local' / 'claude',
        Path.home() / '.local' / 'bin' / 'claude.exe',
        Path.home() / '.local' / 'bin' / 'claude',
    ]
    for path in possible_paths:
        if path.exists():
            return str(path)
    # Fall back to PATH lookup
    return shutil.which('claude') or 'claude'
