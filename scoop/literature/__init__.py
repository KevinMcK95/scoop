"""Shared literature values; importing these does not require httpx.

Import ADSClient from scoop.literature.ads when the literature extra is installed.
This package deliberately does not register chat tools or perform I/O on import.
"""
from .models import (
    ADSError, ADSResult, BibliographicPaper, CitationsRequest, PaperRequest,
    SearchRequest,
)

__all__ = [
    "ADSError", "ADSResult", "BibliographicPaper", "CitationsRequest",
    "PaperRequest", "SearchRequest",
]
