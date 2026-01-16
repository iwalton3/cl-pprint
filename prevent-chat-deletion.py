#!/usr/bin/env python3
"""Set cleanupPeriodDays to 99999 in ~/.claude/settings.json to prevent chat deletion."""

import json
import os

def main():
    settings_path = os.path.expanduser("~/.claude/settings.json")

    # Load existing settings or start fresh
    if os.path.exists(settings_path):
        with open(settings_path, "r") as f:
            settings = json.load(f)
    else:
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        settings = {}

    # Set the cleanup period
    settings["cleanupPeriodDays"] = 99999

    # Write back
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)

    print(f"Updated {settings_path}")
    print(f"  cleanupPeriodDays = 99999")

if __name__ == "__main__":
    main()
