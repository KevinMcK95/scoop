from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from scoop.domain import (
    DataProduct,
    EntityRef,
    Measurement,
    ObjectProfile,
    Observation,
    Publication,
    QueryPlan,
)
from scoop.models import Cone, Provenance, Record


def provenance(native_id="id", archive="TEST", **kwargs):
    return Provenance(archive, native_id, "query", **kwargs)


def ref(kind, native_id="id", archive="TEST"):
    return EntityRef(kind, archive, native_id)


class QueryPlanTests(unittest.TestCase):
    def test_target_and_region_plan(self):
        region = Cone(10, -20, 0.5)
        plan = QueryPlan(
            "Find M31 observations", ("M31",), region,
            capabilities=("observations",),
        )
        self.assertEqual(plan.targets, ("M31",))
        self.assertIs(plan.region, region)
        self.assertEqual(plan.capabilities, ("observations",))
        with self.assertRaises(FrozenInstanceError):
            plan.original_text = "changed"

    def test_topic_only_and_other_intent(self):
        plan = QueryPlan("Papers about dark matter", literature_topics=("dark matter",))
        self.assertEqual(plan.targets, ())
        self.assertIsNone(plan.region)
        self.assertEqual(plan.literature_topics, ("dark matter",))
        self.assertEqual(plan.capabilities, ())
        QueryPlan("Search here", region=Cone(0, 0, 1))
        QueryPlan("List publications", capabilities=("publications",))

    def test_invalid_plan(self):
        for kwargs in (
            {},
            {"targets": (" ",)},
            {"literature_topics": ("",)},
            {"capabilities": ("",)},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                QueryPlan("request", **kwargs)
        with self.assertRaises(ValueError):
            QueryPlan(" ", targets=("M31",))


class EntityRefTests(unittest.TestCase):
    def test_composite_identity_and_record_compatibility(self):
        identity = ref("object")
        self.assertEqual(identity, ref("object"))
        self.assertEqual(len({identity, ref("object")}), 1)
        for different in (ref("observation"), ref("object", "other"), ref("object", archive="OTHER")):
            self.assertNotEqual(identity, different)
        record = Record("object", provenance(), {})
        self.assertEqual(
            identity,
            EntityRef(record.kind, record.provenance.archive, record.provenance.native_id),
        )
        with self.assertRaises(FrozenInstanceError):
            identity.native_id = "changed"

    def test_identity_is_exact_without_normalization(self):
        identity = ref("object", "Source")
        self.assertNotEqual(identity, ref("object", "source"))
        self.assertNotEqual(identity, ref("object", "Source "))
        self.assertNotEqual(identity, ref("object", "Source", archive="test"))

    def test_required_identity_strings(self):
        for args in (("", "TEST", "id"), ("object", " ", "id"), ("object", "TEST", "")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                EntityRef(*args)


class MeasurementAndObjectTests(unittest.TestCase):
    def test_competing_measurements_are_retained_without_conversion(self):
        first = Measurement("distance", 10.0, "pc", provenance("source-1"), 0.5)
        second = Measurement("distance", 0.02, "kpc", provenance("source-2"), 0.0)
        profile = ObjectProfile(ref("object"), provenance(), "Star", ("Alias",), (first, second, first))
        self.assertEqual(profile.aliases, ("Alias",))
        self.assertEqual(profile.measurements, (first, second, first))
        self.assertEqual(second.unit, "kpc")
        self.assertEqual(second.value, 0.02)
        self.assertEqual(first.provenance.native_id, "source-1")
        self.assertIsNone(Measurement("offset", -1.0, "arcsec", provenance()).uncertainty)

    def test_invalid_measurements(self):
        for field, values in (
            ("value", (float("nan"), float("inf"), -float("inf"))),
            ("uncertainty", (-0.1, float("nan"), float("inf"), -float("inf"))),
            ("quantity", ("", " ")),
            ("unit", ("", " ")),
        ):
            for value in values:
                kwargs = dict(quantity="flux", value=1.0, unit="Jy", provenance=provenance())
                kwargs[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    Measurement(**kwargs)

    def test_object_provenance_must_match_native_identity(self):
        for source in (provenance("other"), provenance(archive="OTHER")):
            with self.subTest(source=source), self.assertRaises(ValueError):
                ObjectProfile(ref("object"), source, "Star")

    def test_invalid_object(self):
        for kwargs in (
            {"ref": ref("observation"), "name": "Star"},
            {"ref": ref("object"), "name": " "},
            {"ref": ref("object"), "name": "Star", "aliases": ("",)},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ObjectProfile(provenance=provenance(), **kwargs)


class NativeEntityTests(unittest.TestCase):
    def test_observation(self):
        region = Cone(10, 20, 1)
        obs = Observation(ref("observation"), provenance(), "Telescope", "Camera", region)
        self.assertIs(obs.region, region)
        self.assertEqual(obs.instrument, "Camera")
        minimal = Observation(ref("observation"), provenance(), "Telescope")
        self.assertIsNone(minimal.instrument)
        self.assertIsNone(minimal.region)

    def test_product_download_requires_explicit_url(self):
        source = provenance(access_url="https://example.org/metadata")
        product = DataProduct(ref("product"), source, "image")
        self.assertFalse(product.downloadable)
        self.assertEqual(product.observation_refs, ())
        linked = DataProduct(
            ref("product"), source, "image", "https://example.org/image.fits",
            (ref("observation", "obs", "OTHER"),), "application/fits",
        )
        self.assertTrue(linked.downloadable)
        self.assertEqual(linked.observation_refs[0].archive, "OTHER")

    def test_publication_and_identifier_copy(self):
        identifiers = {"doi": "10.1234/example", "bibcode": "native-code"}
        publication = Publication(ref("publication"), provenance(), "A title", identifiers)
        self.assertEqual(publication.publication_type, "paper")
        identifiers["doi"] = "changed"
        self.assertEqual(publication.identifiers["doi"], "10.1234/example")
        with self.assertRaises(TypeError):
            publication.identifiers["doi"] = "changed"
        self.assertEqual(Publication(ref("publication"), provenance(), "Title").identifiers, {})

    def test_kinds_and_provenance_must_match(self):
        for kind, factory in (
            ("observation", lambda r, p: Observation(r, p, "Telescope")),
            ("product", lambda r, p: DataProduct(r, p, "image")),
            ("publication", lambda r, p: Publication(r, p, "Title")),
        ):
            for identity in (ref("object"), ref(kind, "other"), ref(kind, archive="OTHER")):
                with self.subTest(kind=kind, identity=identity), self.assertRaises(ValueError):
                    factory(identity, provenance())

    def test_required_and_optional_strings(self):
        factories = (
            lambda: Observation(ref("observation"), provenance(), " "),
            lambda: Observation(ref("observation"), provenance(), "Telescope", instrument=""),
            lambda: DataProduct(ref("product"), provenance(), ""),
            lambda: DataProduct(ref("product"), provenance(), "image", access_url=" "),
            lambda: DataProduct(ref("product"), provenance(), "image", media_type=""),
            lambda: DataProduct(ref("product"), provenance(), "image", observation_refs=(ref("object"),)),
            lambda: Publication(ref("publication"), provenance(), " "),
            lambda: Publication(ref("publication"), provenance(), "Title", publication_type=""),
            lambda: Publication(ref("publication"), provenance(), "Title", {"": "id"}),
            lambda: Publication(ref("publication"), provenance(), "Title", {"doi": " "}),
        )
        for factory in factories:
            with self.subTest(factory=factory), self.assertRaises(ValueError):
                factory()
