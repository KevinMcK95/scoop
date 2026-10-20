"""Wall-clock time limits for archive queries, so one slow archive can't hang a run."""
from __future__ import annotations

import socket
import threading
import time

# Any socket opened without its own timeout gives up after this many seconds.
# This covers clients (e.g. the Gaia TAP client) that don't take a timeout argument.
SOCKET_TIMEOUT = 120


class TimeLimitExceeded(Exception):
    pass


def set_socket_timeout(seconds: float = SOCKET_TIMEOUT):
    socket.setdefaulttimeout(seconds)


def call_with_timeout(fn, seconds: float):
    """Run ``fn()`` and return its result, or raise ``TimeLimitExceeded``.

    ``fn`` runs in a daemon thread. A thread can't be killed, so a hung query
    keeps running in the background, but it no longer blocks the caller and
    doesn't keep the process alive at exit.
    """
    box = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as exc:  # re-raised in the caller
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise TimeLimitExceeded(f"no answer after {seconds:.0f} s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def is_network_error(exc: BaseException) -> bool:
    """Connection resets, timeouts, HTTP and service errors: worth retrying."""
    if isinstance(exc, (OSError, TimeoutError)):  # includes ConnectionError and requests' errors
        return True
    name = type(exc).__name__
    return any(word in name for word in ("Timeout", "Connection", "Service", "HTTP"))


def with_retries(fn, attempts: int = 3, delay: float = 10, sleep=time.sleep):
    """Call ``fn()``, retrying network errors with doubling delays (10 s, 20 s by default)."""
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            if attempt == attempts - 1 or not is_network_error(exc):
                raise
            sleep(delay * 2**attempt)
