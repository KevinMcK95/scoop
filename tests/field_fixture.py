"""A tiny field in sky_viewer.viewer_data format, for offline tests."""


def viewer_data():
    gaia = lambda gid, ra, g, clean=True: {"ra": ra, "dec": 57.9, "data": {  # noqa: E731
        "Gaia DR3": gid, "G": g, "BP-RP": 0.9, "parallax (mas)": 0.01, "pmRA (mas/yr)": 0.04,
        "pmDec (mas/yr)": -0.19, "parallax error (mas)": 0.2, "pmRA error (mas/yr)": 0.3,
        "pmDec error (mas/yr)": 0.3, "r_ell (r_half)": round(abs(ra - 260.05) * 10, 3),
        "RUWE": 1.0, "clean": clean}}
    return {
        "key": "mini_1",
        "target": {"name": "Mini", "ra": 260.05, "dec": 57.92, "rhalf_arcmin": 9.6,
                   "ellipticity": 0.3, "pa_deg": 89, "ref_structure": "test", "distance_kpc": 80.0,
                   "pmra": 0.04, "pmdec": -0.19, "ref_proper_motion": "test"},
        "n_rhalf": 2, "fov": 0.8,
        "ellipses": {"1": [[260.0, 57.9], [260.1, 57.9], [260.1, 58.0]],
                     "n": [[259.9, 57.8], [260.2, 57.8], [260.2, 58.1]]},
        "observations": [
            {"tel": "HST", "inst": "ACS/WFC", "filters": "F606W", "obs_id": "j8x101", "date": "2004-08-01",
             "year": 2004.6, "exptime": 500.0, "program": "10229", "pi": "Doe, J.", "rights": "PUBLIC",
             "frac": 1.0, "s_region": "POLYGON 260.0 57.9 260.05 57.9 260.05 57.95", "ra": 260.02, "dec": 57.92},
            {"tel": "JWST", "inst": "NIRCAM/IMAGE", "filters": "F150W", "obs_id": "jw01234", "date": "2023-05-01",
             "year": 2023.3, "exptime": 1200.0, "program": "1234", "pi": "Roe, R.", "rights": "PUBLIC",
             "frac": 0.5, "s_region": "POLYGON 260.0 57.9 260.03 57.9 260.03 57.93", "ra": 260.01, "dec": 57.91},
        ],
        "planned": [],
        "coverage": [{"tel": "HST", "inst": "ACS/WFC", "frac": 0.2}],
        "baselines": [{"pair": "HST–JWST", "years": 18.7, "a": "j8x101", "b": "jw01234"}],
        "gaia": {"stars": [gaia("1001", 260.01, 18.0), gaia("1002", 260.02, 20.4, False)],
                 "galaxies": [gaia("1003", 260.03, 20.9)], "crf": [gaia("1004", 260.04, 19.0)],
                 "quaia": [gaia("1004", 260.04, 19.0)], "candidates": [], "rrlyrae": [gaia("1005", 260.05, 19.5)]},
        "milliquas": [{"ra": 260.06, "dec": 57.95, "data": {"MILLIQUAS": "J1720+5757", "type": "Q", "z": 1.4,
                                                          "Rmag": 19.8}, "confirmed": True}],
        "simbad_galaxies": [{"ra": 260.07, "dec": 57.96, "data": {"SIMBAD": "LEDA 1", "type": "G",
                             "major axis (′)": 0.3, "Gaia": "Gaia galaxy candidate", "Gaia DR3": "1003",
                             "Gaia 2-parameter solution": True}, "flagged": True, "unflagged": False}],
        "spectra": {"1001": {"ra": 260.01, "dec": 57.9, "spectra": [{"kind": "DESI", "survey": "main",
                             "spectype": "STAR", "z": -0.0009, "zwarn": 0, "rv": -291.5, "rv_err": 1.2,
                             "feh": -1.9, "teff": 4500, "logg": 1.2}]},
                    "sdss:42": {"ra": 260.08, "dec": 57.97, "spectra": [{"kind": "APOGEE", "survey": "apogee2"}]}},
        "decisions": ["Hid the Gaia QSO candidate table."],
        "surveys": [["CDS/P/DSS2/color", "DSS2 color"]], "colors": {},
    }
