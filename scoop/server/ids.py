"""Conversation ids: the browser makes them, and stores on disk are keyed by them."""
import re

THREAD_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
THREAD_ID_RULE = "thread_id must be 8-64 letters, digits, '-' or '_'"
