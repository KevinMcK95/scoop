"""Explicit JSON projection shared by future HTTP endpoints and agent tools.

Arbitrary raw native documents are deliberately omitted from the wire projection.
Missing metadata stays null; no counts or source associations are inferred.
"""
from __future__ import annotations

from dataclasses import asdict

from .models import ADSResult


def result_payload(result: ADSResult) -> dict:
    papers = []
    for paper in result.papers:
        publication = paper.publication
        provenance = publication.provenance
        papers.append({
            "ref": asdict(publication.ref),
            "title": publication.title,
            "publication_type": publication.publication_type,
            "identifiers": dict(publication.identifiers),
            "authors": list(paper.authors),
            "abstract": paper.abstract,
            "year": paper.year,
            "venue": paper.venue,
            "citation_count": paper.citation_count,
            "provenance": {
                "archive": provenance.archive,
                "native_id": provenance.native_id,
                "query": provenance.query,
                "access_url": provenance.access_url,
                "retrieved_at": provenance.retrieved_at.isoformat(),
            },
        })
    return {
        "service": "ADS",
        "request": asdict(result.request),
        "query": result.query,
        "status": result.status.value,
        "retrieved_at": result.retrieved_at.isoformat(),
        "papers": papers,
        "total_count": result.total_count,
        "next_start": result.next_start,
        "warnings": list(result.warnings),
        "error": asdict(result.error) if result.error else None,
    }
