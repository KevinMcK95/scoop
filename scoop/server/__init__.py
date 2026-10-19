"""Optional HTTP server for the web workspace; requires the ``server`` extra."""

from .app import create_app

__all__ = ["create_app"]
