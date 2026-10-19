from __future__ import annotations

import asyncio
import unittest
from datetime import datetime

from scoop import AdapterPage, Cone, Provenance, Record, SearchRequest, SearchStatus
from scoop.adapters import Adapter, AdapterError, record_from_mapping
from scoop.handlers import make_handler


def normalizer(native_record, request, *, query, retrieved_at):
    return record_from_mapping(
        archive="TEST",
        kind="observation",
        native_id=native_record["id"],
        data=native_record,
        query=query,
        retrieved_at=retrieved_at,
        raw=native_record,
    )


def make_fake_adapter(pages=(), error=None):
    remaining_pages = list(pages)
    calls = []

    async def fetch_page(request, cursor=None):
        calls.append(cursor)
        if error is not None:
            raise error
        return remaining_pages.pop(0)

    return fetch_page, calls


def handler(fetch_page, *, timeout_seconds=60.0):
    return make_handler(
        adapter=Adapter(archive="TEST", fetch_page=fetch_page, normalize=normalizer),
        timeout_seconds=timeout_seconds,
    )


class HandlerContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_pages_and_normalizes_records(self):
        fetch_page, calls = make_fake_adapter(
            [
                AdapterPage(({"id": "a"},), next_cursor="next", total_count=2, query="q1"),
                AdapterPage(({"id": "b"},), total_count=2, query="q2"),
            ]
        )
        report = await handler(fetch_page)(SearchRequest("observations"))
        self.assertEqual(report.status, SearchStatus.SUCCESS)
        self.assertEqual([r.provenance.native_id for r in report.records], ["a", "b"])
        self.assertEqual(calls, [None, "next"])
        self.assertEqual(report.total_count, 2)
        self.assertEqual(report.query, "q2")

    async def test_empty_is_not_failure(self):
        fetch_page, _ = make_fake_adapter([AdapterPage((), query="empty query")])
        report = await handler(fetch_page)(SearchRequest("observations"))
        self.assertEqual(report.status, SearchStatus.EMPTY)
        self.assertEqual(report.records, ())

    async def test_adapter_failure_is_reported(self):
        fetch_page, _ = make_fake_adapter(error=AdapterError("service down", query="actual query"))
        report = await handler(fetch_page)(SearchRequest("observations"))
        self.assertEqual(report.status, SearchStatus.FAILED)
        self.assertEqual(report.error, "service down")
        self.assertEqual(report.query, "actual query")

    async def test_page_limit_marks_results_partial(self):
        fetch_page, _ = make_fake_adapter(
            [AdapterPage(({"id": "a"},), next_cursor="next", query="q")]
        )
        report = await handler(fetch_page)(SearchRequest("observations", max_pages=1))
        self.assertEqual(report.status, SearchStatus.PARTIAL)
        self.assertEqual(len(report.records), 1)
        self.assertTrue(report.warnings)

    async def test_timeout_is_reported(self):
        async def slow_fetch(request, cursor=None):
            await asyncio.sleep(0.05)
            return AdapterPage(())

        report = await handler(slow_fetch, timeout_seconds=0.001)(
            SearchRequest("observations")
        )
        self.assertEqual(report.status, SearchStatus.TIMED_OUT)


class ModelValidationTests(unittest.TestCase):
    def test_cone_and_request_validate_units_and_limits(self):
        with self.assertRaises(ValueError):
            Cone(360, 0, 1)
        with self.assertRaises(ValueError):
            SearchRequest("observations", page_size=0)

    def test_provenance_requires_timezone_aware_timestamp(self):
        with self.assertRaises(ValueError):
            Provenance("TEST", "id", "query", retrieved_at=datetime(2026, 1, 1))

