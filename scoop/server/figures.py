"""Figures and sky images the agent's tools returned, kept per conversation.

The chat stream sends the browser a short URL instead of the image itself, so a
conversation's figures survive page reloads and reopening it from History. Files
are only readable by the user (directory 700, files 600); deleting a conversation
in History deletes its figures.
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import uuid
from pathlib import Path

DEFAULT_DIR = Path.home() / ".config" / "scoop" / "figures"
THREAD_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
NAME = re.compile(r"[A-Za-z0-9_-]{1,80}\.(png|jpg)")
KINDS = {"image/png": "png", "image/jpeg": "jpg"}
MEDIA = {"png": "image/png", "jpg": "image/jpeg"}
MAX_BYTES = 8_000_000


class FigureStore:
    def __init__(self, directory: Path = DEFAULT_DIR) -> None:
        self.directory = Path(directory)

    def save(self, thread_id: str, call_id: str, data_url: str) -> str | None:
        """Store a data: URL image; returns its /api/figures URL, or None if it is not one."""
        match = re.fullmatch(r"data:(image/png|image/jpeg);base64,([A-Za-z0-9+/=]+)", data_url)
        if not match or not THREAD_ID.fullmatch(thread_id):
            return None
        data = base64.b64decode(match.group(2))
        if not data or len(data) > MAX_BYTES:
            return None
        stem = re.sub(r"[^A-Za-z0-9_-]", "", call_id)[:76] or uuid.uuid4().hex
        name = f"{stem}.{KINDS[match.group(1)]}"
        folder = self.directory / thread_id
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        fd = os.open(folder / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return f"/api/figures/{thread_id}/{name}"

    def path(self, thread_id: str, name: str) -> tuple[Path, str] | None:
        """The stored file and its media type, if both names are valid and it exists."""
        if not THREAD_ID.fullmatch(thread_id) or not NAME.fullmatch(name):
            return None
        path = self.directory / thread_id / name
        return (path, MEDIA[name.rsplit(".", 1)[1]]) if path.is_file() else None

    def delete(self, thread_id: str) -> bool:
        if not THREAD_ID.fullmatch(thread_id):
            return False
        folder = self.directory / thread_id
        if folder.is_dir():
            shutil.rmtree(folder)
        return True


__all__ = ["DEFAULT_DIR", "FigureStore"]
