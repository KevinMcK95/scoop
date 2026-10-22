import math
import unittest

from scoop.science.spatial import Footprint, footprint_contains, footprint_overlaps_circle, parse_stcs, polygon_contains_circle, separation_deg

SQUARE = "POLYGON ICRS 260.0 57.9 260.1 57.9 260.1 58.0 260.0 58.0"


class SpatialTests(unittest.TestCase):
    def test_separation(self):
        self.assertAlmostEqual(separation_deg(10, 0, 10.1, 0), 0.1, places=9)
        self.assertAlmostEqual(separation_deg(359.99, 0, 0.01, 0), 0.02, places=9)
        self.assertAlmostEqual(separation_deg(0, 89.9, 180, 89.9), 0.2, places=6)
        self.assertEqual(separation_deg(5, 5, 5, 5), 0)

    def test_parse_skips_frames_and_flattens_unions(self):
        shapes = parse_stcs("UNION ( POLYGON J2000 1 2 3 4 5 6 CIRCLE ICRS GEOCENTER 7 8 0.1 ) POLYGON 1 2")
        self.assertEqual(shapes, [("polygon", [1, 2, 3, 4, 5, 6]), ("circle", [7, 8, 0.1])])
        self.assertEqual(parse_stcs(""), [])

    def test_point_in_footprint(self):
        self.assertTrue(footprint_contains(SQUARE, 260.05, 57.95))
        self.assertFalse(footprint_contains(SQUARE, 260.15, 57.95))
        self.assertTrue(footprint_contains("CIRCLE 10 0 0.1", 10.05, 0))
        across = "POLYGON 359.95 -0.05 0.05 -0.05 0.05 0.05 359.95 0.05"
        self.assertTrue(footprint_contains(across, 0.0, 0.0))
        self.assertFalse(footprint_contains(across, 180.0, 0.0))

    def test_footprint_prefilter_agrees_with_exact_test(self):
        fp = Footprint(SQUARE)
        self.assertTrue(fp.contains(260.05, 57.95))
        self.assertFalse(fp.contains(260.15, 57.95))
        self.assertFalse(fp.near(10, 0))
        self.assertFalse(Footprint("garbage").contains(260.05, 57.95))
        across = Footprint("POLYGON 359.95 -0.05 0.05 -0.05 0.05 0.05 359.95 0.05")
        self.assertLess(min(across.center[0], 360 - across.center[0]), 1e-9)
        self.assertTrue(across.contains(0.0, 0.0))

    def test_footprints_overlap_each_other(self):
        square = Footprint(SQUARE)
        shifted = Footprint("POLYGON 260.08 57.98 260.18 57.98 260.18 58.08 260.08 58.08")
        apart = Footprint("POLYGON 261.0 57.9 261.1 57.9 261.1 58.0 261.0 58.0")
        inner = Footprint("POLYGON 260.04 57.94 260.06 57.94 260.06 57.96 260.04 57.96")
        # A thin cross: edges cross although no vertex lies inside the square.
        bar = Footprint("POLYGON 259.95 57.94 260.15 57.94 260.15 57.96 259.95 57.96")
        circle = Footprint("CIRCLE 260.12 57.95 0.02")
        self.assertTrue(square.overlaps(shifted))
        self.assertFalse(square.overlaps(apart))
        self.assertTrue(square.overlaps(inner) and inner.overlaps(square))
        self.assertTrue(square.overlaps(bar))
        self.assertTrue(square.overlaps(circle))
        self.assertFalse(apart.overlaps(circle))
        self.assertFalse(Footprint("").overlaps(square))

    def test_circle_overlap_and_containment(self):
        # 0.0201 deg east of the square's east edge (at dec 57.95, 1 deg RA ~ 0.531 deg).
        ra = 260.1 + 0.02 / math.cos(math.radians(57.95))
        self.assertFalse(footprint_overlaps_circle(SQUARE, ra, 57.95, 0.019))
        self.assertTrue(footprint_overlaps_circle(SQUARE, ra, 57.95, 0.021))
        poly = [[260.0, 57.9], [260.1, 57.9], [260.1, 58.0], [260.0, 58.0]]
        self.assertTrue(polygon_contains_circle(poly, 260.05, 57.95, 0.02))
        self.assertFalse(polygon_contains_circle(poly, 260.05, 57.95, 0.03))
        self.assertFalse(polygon_contains_circle(poly, 261.0, 57.95, 0.01))


if __name__ == "__main__":
    unittest.main()
