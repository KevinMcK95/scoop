"""Harvesting candidate datasets from the literature, through ADS.

A survey's data release is announced in a paper before it appears anywhere a
machine can see it, so the literature is the earliest reliable signal that a new
dataset exists. ADS indexes arXiv eprints alongside journal articles, so one
client covers both; ``entdate`` (the date a record entered ADS) gives the
month-sized window a scheduled scan needs, which ``year`` alone does not.

The channels below are the queries, kept as data so a reviewer can read what the
scout actually asks for. Each is deliberately broad: triage filters, not the
query, because a missed release costs a month and a false positive costs a few
hundred tokens.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date

from ..literature.models import SearchRequest
from ..models import SearchStatus

PAGE_SIZE = 50  # the ADS client's maximum


@dataclass(frozen=True)
class Channel:
    name: str
    query: str
    doctype: str | None = None


CHANNELS: tuple[Channel, ...] = (
    Channel("data_release",
            'title:("data release" OR "early data release" OR "public data release" '
            'OR "first data release" OR "internal data release")'),
    Channel("catalog_paper",
            'title:(catalog OR catalogue) AND abs:("publicly available" OR "we release" '
            'OR "we make" OR "value-added")'),
    Channel("survey_release",
            'abs:("data release") AND title:(survey OR archive OR "sky survey" OR pipeline)'),
    # doctype:catalog is how ADS labels the catalogue records themselves (VizieR, CDS).
    Channel("vizier_catalog", 'abs:(survey OR catalog OR catalogue)', doctype="catalog"),
)


@dataclass(frozen=True)
class Candidate:
    """One paper that might announce a dataset SCOOP should know about."""

    bibcode: str
    title: str
    channel: str
    authors: tuple[str, ...] = ()
    abstract: str | None = None
    year: int | None = None
    venue: str | None = None

    @property
    def url(self) -> str:
        return f"https://ui.adsabs.harvard.edu/abs/{self.bibcode}"

    def brief(self, *, abstract_chars: int = 1200) -> dict:
        """The candidate as the model sees it: enough to judge, not the whole paper."""
        abstract = (self.abstract or "")[:abstract_chars]
        return {"bibcode": self.bibcode, "title": self.title, "channel": self.channel,
                "year": self.year, "venue": self.venue,
                "first_author": self.authors[0] if self.authors else None,
                "abstract": abstract or None, "url": self.url}


def _windowed(channel: Channel, since: date, until: date) -> str:
    return f'{channel.query} entdate:["{since.isoformat()}" TO "{until.isoformat()}"]'


async def _channel(ads, channel: Channel, since: date, until: date, limit: int,
                   ) -> tuple[list[Candidate], list[str]]:
    found: list[Candidate] = []
    warnings: list[str] = []
    start = 0
    while len(found) < limit:
        request = SearchRequest(query=_windowed(channel, since, until),
                                page_size=min(PAGE_SIZE, limit - len(found)), start=start,
                                sort_by="date", database="astronomy", doctype=channel.doctype)
        result = await ads.search(request)
        if result.status in (SearchStatus.FAILED, SearchStatus.TIMED_OUT):
            reason = result.error.message if result.error else "unknown error"
            warnings.append(f"channel {channel.name}: {reason}")
            break
        warnings.extend(f"channel {channel.name}: {warning}" for warning in result.warnings)
        for paper in result.papers[:limit - len(found)]:  # a server may return more than asked
            found.append(Candidate(
                bibcode=paper.publication.ref.native_id, title=paper.publication.title,
                channel=channel.name, authors=paper.authors, abstract=paper.abstract,
                year=paper.year, venue=paper.venue))
        if result.next_start is None:
            break
        start = result.next_start
    return found, warnings


async def harvest(ads, *, since: date, until: date, limit_per_channel: int = 50,
                  channels: tuple[Channel, ...] = CHANNELS, skip: frozenset[str] = frozenset(),
                  ) -> tuple[tuple[Candidate, ...], tuple[str, ...]]:
    """Candidates entered in ADS in ``[since, until]``, deduplicated by bibcode.

    ``skip`` holds bibcodes already judged in an earlier scan. A channel that
    fails is reported in the warnings and does not stop the others, so one bad
    query still leaves a usable scan.
    """
    if since > until:
        raise ValueError("since must not be after until")
    results = await asyncio.gather(*(_channel(ads, channel, since, until, limit_per_channel)
                                     for channel in channels))
    candidates: dict[str, Candidate] = {}
    warnings: list[str] = []
    for found, channel_warnings in results:
        warnings.extend(channel_warnings)
        for candidate in found:
            if candidate.bibcode not in skip:
                candidates.setdefault(candidate.bibcode, candidate)
    return tuple(candidates.values()), tuple(warnings)
