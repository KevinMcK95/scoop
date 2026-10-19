import unittest

from scoop import (
    DataProduct, EntityRef, HandlerReport, Provenance, QueryPlan, QueryResult,
    SearchOutcome, SearchRequest, SearchStatus,
)


class QueryResultTests(unittest.TestCase):
    def test_inventory_does_not_require_parsing_chat_into_a_plan(self):
        result = QueryResult()
        self.assertIsNone(result.plan)
        self.assertEqual(result.searches, ())

    def test_archive_failures_and_empty_results_remain_distinct(self):
        outcomes = tuple(
            SearchOutcome(
                SearchRequest("observations"),
                HandlerReport(status=status, archive="TEST"),
            )
            for status in SearchStatus
        )
        result = QueryResult(QueryPlan("M31", targets=("M31",)), searches=outcomes)
        self.assertEqual(len(result.searches), 5)
        self.assertEqual(
            {s.report.status for s in result.incomplete_searches},
            {SearchStatus.PARTIAL, SearchStatus.FAILED, SearchStatus.TIMED_OUT},
        )
        self.assertEqual(result.searches[1].report.status, SearchStatus.EMPTY)

    def test_no_searches_is_not_a_completeness_claim(self):
        result = QueryResult(QueryPlan("M31", targets=("M31",)))
        self.assertEqual(result.searches, ())
        self.assertEqual(result.incomplete_searches, ())
        self.assertFalse(hasattr(result, "complete"))

    def test_products_have_filtered_views_without_deduplication(self):
        def product(kind):
            return DataProduct(
                EntityRef("product", "TEST", kind),
                Provenance("TEST", kind, "q"), kind,
            )
        image, spectrum, table = map(product, ("image", "spectrum", "table"))
        result = QueryResult(
            QueryPlan("M31", targets=("M31",)),
            products=(image, spectrum, table, image),
        )
        self.assertEqual(result.images, (image, image))
        self.assertEqual(result.spectra, (spectrum,))
        self.assertFalse(result.images[0].downloadable)

    def test_outcome_requires_archive_identity(self):
        with self.assertRaises(ValueError):
            SearchOutcome(SearchRequest("images"), HandlerReport(SearchStatus.EMPTY))
