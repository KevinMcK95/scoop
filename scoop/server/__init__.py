"""Optional HTTP server for the web workspace; requires the ``server`` extra.

``create_app`` is imported lazily so dependency-free parts (``jobs``) can be
used without the chat stack installed.
"""


def create_app(*args, **kwargs):
    from .app import create_app as build

    return build(*args, **kwargs)


__all__ = ["create_app"]
