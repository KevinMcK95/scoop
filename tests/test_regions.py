import math
import unittest

try:
    import numpy as np
    from scoop.science.regions import GreatCircleFrame, Region, adql_condition, separation
except ModuleNotFoundError as exc:
    if exc.name != "numpy":
        raise
    np = None

try:
    import astropy.units as u
    import gala.coordinates as gc
    from astropy.coordinates import SkyCoord
except Exception:  # gala is optional (it comes with galstreams)
    gc = None


@unittest.skipIf(np is None, "numpy absent")
class FrameTests(unittest.TestCase):
    def test_round_trip_and_pm_rotation(self):
        f = GreatCircleFrame.from_pole_ra0(34.6, 29.7, 200.5, disambiguate=(200.5, 58.3))
        ra, dec = np.array([150.0, 200.0, 210.0]), np.array([30.0, 58.0, 55.0])
        p1, p2 = f.to_frame(ra, dec)
        back = f.to_icrs(p1, p2)
        self.assertTrue(np.allclose(separation(ra, dec, *back), 0, atol=1e-9))
        pmra, pmdec = np.array([-5.0, -8.0, 1.0]), np.array([-2.0, -4.0, 3.0])
        a, b = f.pm_to_frame(ra, dec, pmra, pmdec)
        self.assertTrue(np.allclose(np.hypot(a, b), np.hypot(pmra, pmdec)))  # a rotation keeps |mu|
        self.assertTrue(np.allclose(f.pm_from_frame(ra, dec, a, b), (pmra, pmdec)))

    @unittest.skipIf(gc is None, "gala absent")
    def test_matches_gala_from_pole_ra0(self):
        pole = SkyCoord(34.5987, 29.7331, unit="deg")
        mid = SkyCoord(200.486, 58.311, unit="deg")
        gala_frame = gc.GreatCircleICRSFrame.from_pole_ra0(pole=pole, ra0=mid.ra, origin_disambiguate=mid)
        ours = GreatCircleFrame.from_pole_ra0(34.5987, 29.7331, 200.486, disambiguate=(200.486, 58.311))
        ra, dec = np.array([124.0, 160.0, 200.0, 219.5]), np.array([-8.0, 40.0, 58.3, 58.2])
        pmra, pmdec = np.array([-2.4, -6.0, -8.4, -8.4]), np.array([-7.9, -9.0, -4.0, -1.4])
        c = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, pm_ra_cosdec=pmra * u.mas / u.yr,
                     pm_dec=pmdec * u.mas / u.yr, distance=[8, 8, 8, 8] * u.kpc,
                     radial_velocity=[0, 0, 0, 0] * u.km / u.s).transform_to(gala_frame)
        p1, p2 = ours.to_frame(ra, dec)
        self.assertTrue(np.allclose(p1, c.phi1.wrap_at(180 * u.deg).deg, atol=1e-8))
        self.assertTrue(np.allclose(p2, c.phi2.deg, atol=1e-8))
        m1, m2 = ours.pm_to_frame(ra, dec, pmra, pmdec)
        self.assertTrue(np.allclose(m1, c.pm_phi1_cosphi2.to_value(u.mas / u.yr), atol=1e-8))
        self.assertTrue(np.allclose(m2, c.pm_phi2.to_value(u.mas / u.yr), atol=1e-8))


@unittest.skipIf(np is None, "numpy absent")
class RegionTests(unittest.TestCase):
    def test_box_is_the_requested_size(self):
        side = math.sqrt(0.5)  # 0.5 deg2
        box = Region.from_spec({"kind": "box", "ra": 150.12, "dec": 2.21, "width_deg": side, "height_deg": side})
        self.assertAlmostEqual(box.area_deg2, 0.5, places=3)
        inside = box.contains([150.12, 150.12 + 0.34 / math.cos(math.radians(2.21)), 150.12 + 0.37], [2.21, 2.21, 2.21])
        self.assertEqual(list(inside), [True, True, False])
        part, = box.parts()
        self.assertIn("POLYGON('ICRS'", adql_condition(part))
        self.assertLess(part["cone"][2], 0.52)

    def test_box_across_ra_zero(self):
        box = Region.from_spec({"kind": "box", "ra": 0.1, "dec": 0.0, "width_deg": 1.0, "height_deg": 1.0})
        self.assertEqual(list(box.contains([359.8, 0.5, 1.0], [0.0, 0.2, 0.0])), [True, True, False])

    def test_band_along_a_curved_track(self):
        # A track bending away from a great circle, 40 deg long, 1 deg wide.
        ra = np.linspace(140, 180, 41)
        dec = 20 + 0.002 * (ra - 160) ** 2
        band = Region.from_spec({"kind": "band", "track": [[a, d] for a, d in zip(ra, dec)], "width_deg": 1.0,
                                 "name": "test"})
        self.assertAlmostEqual(band.area_deg2, 40 * math.cos(math.radians(20)), delta=2.0)
        self.assertTrue(band.contains([160.0], [20.3])[0])
        self.assertFalse(band.contains([160.0], [20.7])[0])
        self.assertTrue(band.contains([175.0], [20 + 0.002 * 225 - 0.4])[0])  # follows the curve
        parts = band.parts()
        self.assertEqual(len(parts), 4)  # 10 deg pieces
        # Every inside point is inside some part's bounding cone.
        p = np.random.default_rng(1)
        sra, sdec = p.uniform(138, 182, 4000), p.uniform(17, 24, 4000)
        inside = band.contains(sra, sdec)
        covered = np.zeros(len(sra), bool)
        for part in parts:
            covered |= separation(sra, sdec, part["cone"][0], part["cone"][1]) <= part["cone"][2]
        self.assertTrue(np.all(covered[inside]))
        again = Region.from_spec(band.to_spec())  # reproducible from its spec
        self.assertTrue(np.array_equal(again.contains(sra, sdec), inside))

    def test_small_cones_cover_bands_and_big_boxes(self):
        ra = np.linspace(140, 180, 41)
        band = Region.from_spec({"kind": "band", "track": [[a, 20 + 0.002 * (a - 160) ** 2] for a in ra],
                                 "width_deg": 2.0})
        box = Region.from_spec({"kind": "box", "ra": 150.0, "dec": 30.0, "width_deg": 6.0, "height_deg": 4.0})
        rng = np.random.default_rng(2)
        for region, (r0, r1, d0, d1) in ((band, (138, 182, 16, 25)), (box, (145, 155, 27, 33))):
            cones = region.cover_cones()
            sra, sdec = rng.uniform(r0, r1, 20000), rng.uniform(d0, d1, 20000)
            inside = region.contains(sra, sdec)
            covered = np.zeros(len(sra), bool)
            for c in cones:
                covered |= separation(sra, sdec, c[0], c[1]) <= c[2]
            self.assertTrue(np.all(covered[inside]), region.kind)
            sky = sum(2 * math.pi * (1 - math.cos(math.radians(c[2]))) for c in cones) * (180 / math.pi) ** 2
            self.assertLess(sky, 3.5 * region.area_deg2)  # not one huge bounding cone

    def test_plan_files_from_the_job_manager_are_read(self):
        # The pipeline reads what JobManager.save_plan writes (this mismatch once broke plan_region).
        import json
        import tempfile
        from pathlib import Path
        from scoop.science.regions import read_spec
        from scoop.server.jobs import JobManager
        spec = {"kind": "box", "ra": 150.0, "dec": 2.0, "width_deg": 0.7, "height_deg": 0.7, "cuts": {"gmax": 20}}
        with tempfile.TemporaryDirectory() as tmp:
            jobs = JobManager(Path(tmp), Path(tmp) / "pipeline.py")
            plan_id = jobs.save_plan(spec, "COSMOS box")
            loaded, name = read_spec(json.loads((Path(tmp) / "region_plans" / f"{plan_id}.json").read_text()))
        self.assertEqual((loaded, name), (spec, "COSMOS box"))
        self.assertEqual(read_spec(spec), (spec, None))
        Region.from_spec({k: v for k, v in loaded.items() if k != "cuts"})
        with self.assertRaises(ValueError):
            read_spec({"name": "x"})

    def test_bad_specs_are_refused(self):
        for spec in ({"kind": "box", "ra": 1, "dec": 2, "width_deg": 40, "height_deg": 1},
                     {"kind": "polygon", "vertices": [[0, 0], [1, 1]]},
                     {"kind": "band", "track": [[0, 0]], "width_deg": 1},
                     {"kind": "hexagon"}):
            with self.assertRaises((ValueError, KeyError)):
                Region.from_spec(spec)


if __name__ == "__main__":
    unittest.main()
