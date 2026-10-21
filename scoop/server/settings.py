"""Model and archive credentials and the analysis kernel, set from the workspace's Settings tab.

A value saved from the tab wins; otherwise the server's environment value at
startup is used; otherwise the setting is off. Removing a saved value falls
back to the environment again. Secrets stay on the server: they are saved to a
file only the user can read (mode 600) and never sent back to the browser, which
only learns whether each is set, its last four characters, and where it came from.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

DEFAULT_PATH = Path.home() / ".config" / "scoop" / "settings.json"
FIELDS = ("model", "base_url", "api_key", "ads_token", "kernel", "kernel_cwd")
SECRETS = ("api_key", "ads_token")
MODEL_ID = re.compile(r"[\w.:/@+-]{1,120}")
KERNEL_NAME = re.compile(r"[\w.-]{1,64}")  # a Jupyter kernel spec name


def _validate(name: str, value: str) -> None:
    if not isinstance(value, str) or len(value) > 4000 or any(c in value for c in "\r\n\0"):
        raise ValueError(f"{name} must be a single-line string")
    if name == "model" and value and not MODEL_ID.fullmatch(value):
        raise ValueError("model must be a model id like openai/gpt-6-luna")
    if name == "base_url" and value and not re.fullmatch(r"https://[^\s/]+(/[^\s]*)?", value):
        raise ValueError("base_url must be an https:// URL")
    if name == "kernel" and value and not KERNEL_NAME.fullmatch(value):
        raise ValueError("kernel must be a Jupyter kernel name like so or python3, or off")
    if name == "kernel_cwd" and value and not value.startswith(("/", "~")):
        raise ValueError("kernel_cwd must be an absolute path (or start with ~)")


class Settings:
    def __init__(self, path: Path = DEFAULT_PATH, defaults: dict | None = None) -> None:
        self.path = Path(path)
        self.defaults: dict[str, str] = {k: v for k, v in (defaults or {}).items() if k in FIELDS and v}
        self.saved: dict[str, str] = {}
        try:
            stored = json.loads(self.path.read_text())
            self.saved = {k: v for k, v in stored.items() if k in FIELDS and isinstance(v, str) and v}
        except (OSError, ValueError):
            pass

    @property
    def values(self) -> dict[str, str]:
        return {**self.defaults, **self.saved}

    def get(self, name: str) -> str | None:
        return self.saved.get(name) or self.defaults.get(name) or None

    def update(self, changes: dict) -> None:
        """Apply changes: a string sets, "" clears, a missing key is kept."""
        if not isinstance(changes, dict) or set(changes) - set(FIELDS):
            raise ValueError(f"settings are {FIELDS}")
        for name, value in changes.items():
            _validate(name, value)
        for name, value in changes.items():
            if value:
                self.saved[name] = value.strip()
            else:
                self.saved.pop(name, None)  # back to the environment value, if any
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Create with owner-only permissions before writing any secret.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(self.saved, handle)  # only what was set here; never the environment
        os.chmod(self.path, 0o600)

    def source(self, name: str) -> str | None:
        return "settings" if name in self.saved else "environment" if name in self.defaults else None

    def public(self) -> dict:
        """What the browser may see: no secret values, and where each value comes from."""
        view = {name: self.get(name) for name in FIELDS if name not in SECRETS}
        for name in SECRETS:
            value = self.get(name)
            view[f"{name}_set"] = bool(value)
            view[f"{name}_hint"] = f"…{value[-4:]}" if value and len(value) >= 8 else None
        view["sources"] = {name: self.source(name) for name in FIELDS}
        return view


__all__ = ["DEFAULT_PATH", "Settings"]
