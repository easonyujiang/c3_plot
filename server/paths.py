"""Normalize explicit game paths across Windows and WSL without disk scanning."""
import os
from pathlib import Path
import re


def native_path(value):
    text = os.fspath(value)
    if os.name == 'posix' and re.match(r'^[A-Za-z]:[\\/]', text):
        drive, tail = text[0].lower(), text[3:].replace('\\', '/')
        mounted = Path('/mnt') / drive
        if mounted.is_dir():
            return str(mounted / tail)
    return text
