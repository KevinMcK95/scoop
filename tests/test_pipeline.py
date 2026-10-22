"""Pure helpers of the field pipeline (scoop/pipeline/lvdb_field.py); no network."""
import unittest

try:
    import numpy as np
    from astropy.table import Table
except ModuleNotFoundError:
    Table = None


@unittest.skipUnless(Table, "astropy absent")
class LeftJoinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scoop.pipeline import lvdb_field
        cls.left_join = staticmethod(lvdb_field.left_join)

    def test_an_empty_side_keeps_every_row_and_adds_masked_columns(self):
        # A 5' cone around TRAPPIST-1 has no Gaia galaxy candidates; astropy's join refused.
        src = Table({"source_id": np.array([2635476908753563008, 2635476908753563009], dtype=np.int64),
                     "ra": [346.62, 346.63]})
        gal = Table({"source_id": np.array([], dtype=np.int64), "gal_classlabel_dsc_joint": np.array([], dtype="U10")})
        out = self.left_join(src, gal, "source_id")
        self.assertEqual(list(out["source_id"]), [2635476908753563008, 2635476908753563009])  # exact ids
        self.assertEqual(out["source_id"].dtype, np.int64)
        self.assertTrue(out["gal_classlabel_dsc_joint"].mask.all())
        empty = self.left_join(gal, src, "source_id")
        self.assertEqual(len(empty), 0)
        self.assertIn("ra", empty.colnames)

    def test_both_sides_with_rows_join_as_before(self):
        a = Table({"k": np.array([1, 2], dtype=np.int64)})
        b = Table({"k": np.array([2], dtype=np.int64), "x": [5.0]})
        out = self.left_join(a, b, "k")
        self.assertEqual(list(out["k"]), [1, 2])
        self.assertTrue(out["x"].mask[0])
        self.assertEqual(out["x"][1], 5.0)


if __name__ == "__main__":
    unittest.main()
