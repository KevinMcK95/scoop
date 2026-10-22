from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from scoop.literature.records import EntityRef, Provenance, Publication


def provenance(native_id="id", archive="TEST", **kwargs):
    return Provenance(archive, native_id, "query", **kwargs)


def ref(kind, native_id="id", archive="TEST"):
    return EntityRef(kind, archive, native_id)


class EntityRefTests(unittest.TestCase):
    def test_composite_identity(self):
        identity = ref("object")
        self.assertEqual(identity, ref("object"))
        self.assertEqual(len({identity, ref("object")}), 1)
        for different in (ref("observation"), ref("object", "other"), ref("object", archive="OTHER")):
            self.assertNotEqual(identity, different)
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


class PublicationTests(unittest.TestCase):
    def test_publication_and_identifier_copy(self):
        identifiers = {"doi": "10.1234/example", "bibcode": "native-code"}
        publication = Publication(ref("publication"), provenance(), "A title", identifiers)
        self.assertEqual(publication.publication_type, "paper")
        identifiers["doi"] = "changed"
        self.assertEqual(publication.identifiers["doi"], "10.1234/example")
        with self.assertRaises(TypeError):
            publication.identifiers["doi"] = "changed"
        self.assertEqual(Publication(ref("publication"), provenance(), "Title").identifiers, {})

    def test_kind_and_provenance_must_match(self):
        for identity in (ref("object"), ref("publication", "other"), ref("publication", archive="OTHER")):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                Publication(identity, provenance(), "Title")

    def test_required_strings(self):
        factories = (
            lambda: Publication(ref("publication"), provenance(), " "),
            lambda: Publication(ref("publication"), provenance(), "Title", publication_type=""),
            lambda: Publication(ref("publication"), provenance(), "Title", {"": "id"}),
            lambda: Publication(ref("publication"), provenance(), "Title", {"doi": " "}),
        )
        for factory in factories:
            with self.subTest(factory=factory), self.assertRaises(ValueError):
                factory()

    def test_provenance_needs_identity_and_an_aware_time(self):
        from datetime import datetime

        for kwargs in ({"native_id": " "}, {"archive": ""}, {"retrieved_at": datetime(2026, 1, 1)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                provenance(**kwargs)


if __name__ == "__main__":
    unittest.main()
