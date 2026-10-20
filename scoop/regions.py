"""Query regions beyond cones: boxes, polygons, and bands along a track (stellar streams).

A region is built from a JSON-able spec (``Region.from_spec``) and answers what the
pipeline and the tools need: whether points are inside (exactly), an outline to draw,
pieces small enough to query archives with (polygons or circles, each with a bounding
cone), and its area. Bands along a track carry a great-circle stream frame (phi1 along
the track, phi2 across it), so containment, cuts and plots work in stream coordinates.

Pure numpy, so the server can use it without astropy or gala. GreatCircleFrame follows
gala's GreatCircleICRSFrame.from_pole_ra0 (as galstreams builds its frames); the tests
check it against gala when gala is installed.
"""
from __future__ import annotations

import math

import numpy as np

MAX_AREA_DEG2 = 2000.0
MAX_BOX_SIDE_DEG = 30.0
MAX_POLYGON_EXTENT_DEG = 30.0
SEGMENT_DEG = 10.0  # long bands are queried in pieces this long (in phi1)
KINDS = ("cone", "box", "polygon", "band")


def unit(ra, dec) -> np.ndarray:
    """Unit vectors (..., 3) for ICRS degrees."""
    a, d = np.deg2rad(np.asarray(ra, float)), np.deg2rad(np.asarray(dec, float))
    return np.stack([np.cos(d) * np.cos(a), np.cos(d) * np.sin(a), np.sin(d)], axis=-1)


def radec(v) -> tuple[np.ndarray, np.ndarray]:
    v = np.asarray(v, float)
    v = v / np.linalg.norm(v, axis=-1, keepdims=True)
    return np.rad2deg(np.arctan2(v[..., 1], v[..., 0])) % 360, np.rad2deg(np.arcsin(np.clip(v[..., 2], -1, 1)))


def separation(ra1, dec1, ra2, dec2) -> np.ndarray:
    """Great-circle separation in degrees (vectorised)."""
    u1, u2 = np.broadcast_arrays(unit(ra1, dec1), unit(ra2, dec2))
    return np.rad2deg(np.arctan2(np.linalg.norm(np.cross(u1, u2), axis=-1), np.sum(u1 * u2, axis=-1)))


def gnomonic(ra, dec, ra0, dec0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Tangent-plane (x east, y north, degrees) about (ra0, dec0); ok is False on the far side."""
    v = unit(ra, dec)
    z = unit(ra0, dec0)
    east = np.array([-math.sin(math.radians(ra0)), math.cos(math.radians(ra0)), 0.0])
    north = np.cross(z, east)
    c = v @ z
    ok = c > 1e-6
    with np.errstate(divide="ignore", invalid="ignore"):
        x, y = np.rad2deg((v @ east) / c), np.rad2deg((v @ north) / c)
    return x, y, ok


def from_gnomonic(x, y, ra0, dec0) -> tuple[np.ndarray, np.ndarray]:
    z = unit(ra0, dec0)
    east = np.array([-math.sin(math.radians(ra0)), math.cos(math.radians(ra0)), 0.0])
    north = np.cross(z, east)
    v = z + np.deg2rad(np.asarray(x, float))[..., None] * east + np.deg2rad(np.asarray(y, float))[..., None] * north
    return radec(v)


def in_polygon(x, y, px, py) -> np.ndarray:
    """Planar point-in-polygon (ray casting), vectorised over points."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    inside = np.zeros(x.shape, bool)
    n = len(px)
    for i in range(n):
        x1, y1, x2, y2 = px[i], py[i], px[(i + 1) % n], py[(i + 1) % n]
        crosses = (y1 > y) != (y2 > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xc = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
        inside ^= crosses & (x < xc)
    return inside


class GreatCircleFrame:
    """A great-circle frame: phi1 along the circle from ``origin``, phi2 toward ``pole``."""

    def __init__(self, pole, origin):
        z = np.asarray(pole, float) / np.linalg.norm(pole)
        x = np.asarray(origin, float)
        x = x - (x @ z) * z  # orthogonalise the origin, keeping the pole
        x /= np.linalg.norm(x)
        self.R = np.array([x, np.cross(z, x), z])  # rows: x, y, z of the new frame in ICRS

    @classmethod
    def from_pole_ra0(cls, pole_ra: float, pole_dec: float, ra0: float, disambiguate=None) -> "GreatCircleFrame":
        """The origin is where the great circle crosses RA = ra0 (of the two crossings, the
        one nearer ``disambiguate``, default (0, 0)), as gala's from_pole_ra0."""
        z = unit(pole_ra, pole_dec)
        a = math.radians(ra0)
        if abs(z[2]) < 1e-10:
            raise ValueError("pole is on the equator, so no origin has that RA")
        # x = (cos a cos d, sin a cos d, sin d) with x . z = 0
        d = math.atan2(-(z[0] * math.cos(a) + z[1] * math.sin(a)), z[2])
        x = np.array([math.cos(a) * math.cos(d), math.sin(a) * math.cos(d), math.sin(d)])
        ref = unit(*(disambiguate if disambiguate is not None else (0.0, 0.0)))
        return cls(z, x if x @ ref >= -x @ ref else -x)

    @classmethod
    def fit(cls, ra, dec) -> "GreatCircleFrame":
        """The great circle closest to a track, oriented so phi1 increases along it, with
        phi1 = 0 at the track's middle point."""
        v = unit(ra, dec)
        pole = np.linalg.svd(v)[2][-1]  # normal of the best-fit plane through the origin
        if np.cross(v[0], v[-1]) @ pole < 0:
            pole = -pole
        mid = v[len(v) // 2]
        return cls(pole, mid)

    @property
    def pole(self) -> tuple[float, float]:
        ra, dec = radec(self.R[2])
        return float(ra), float(dec)

    @property
    def origin(self) -> tuple[float, float]:
        ra, dec = radec(self.R[0])
        return float(ra), float(dec)

    def to_spec(self) -> dict:
        return {"pole": list(self.pole), "origin": list(self.origin)}

    @classmethod
    def from_spec(cls, spec: dict) -> "GreatCircleFrame":
        return cls(unit(*spec["pole"]), unit(*spec["origin"]))

    def to_frame(self, ra, dec) -> tuple[np.ndarray, np.ndarray]:
        """(phi1, phi2) in degrees, phi1 in [-180, 180)."""
        w = unit(ra, dec) @ self.R.T
        phi1 = np.rad2deg(np.arctan2(w[..., 1], w[..., 0]))
        return phi1, np.rad2deg(np.arcsin(np.clip(w[..., 2], -1, 1)))

    def to_icrs(self, phi1, phi2) -> tuple[np.ndarray, np.ndarray]:
        return radec(unit(phi1, phi2) @ self.R)

    def _bases(self, ra, dec):
        a, d = np.deg2rad(np.asarray(ra, float)), np.deg2rad(np.asarray(dec, float))
        e_ra = np.stack([-np.sin(a), np.cos(a), np.zeros_like(a)], axis=-1)
        e_dec = np.stack([-np.sin(d) * np.cos(a), -np.sin(d) * np.sin(a), np.cos(d)], axis=-1)
        p1, p2 = self.to_frame(ra, dec)
        p1, p2 = np.deg2rad(p1), np.deg2rad(p2)
        x, y, z = self.R
        e_p1 = -np.sin(p1)[..., None] * x + np.cos(p1)[..., None] * y
        e_p2 = (-np.sin(p2) * np.cos(p1))[..., None] * x - (np.sin(p2) * np.sin(p1))[..., None] * y \
            + np.cos(p2)[..., None] * z
        return e_ra, e_dec, e_p1, e_p2

    def pm_to_frame(self, ra, dec, pmra_cosdec, pmdec) -> tuple[np.ndarray, np.ndarray]:
        """Proper motions (mu_alpha*, mu_delta) to (mu_phi1 cos phi2, mu_phi2), same units."""
        e_ra, e_dec, e_p1, e_p2 = self._bases(ra, dec)
        v = np.asarray(pmra_cosdec, float)[..., None] * e_ra + np.asarray(pmdec, float)[..., None] * e_dec
        return np.sum(v * e_p1, axis=-1), np.sum(v * e_p2, axis=-1)

    def pm_from_frame(self, ra, dec, pm_phi1_cosphi2, pm_phi2) -> tuple[np.ndarray, np.ndarray]:
        e_ra, e_dec, e_p1, e_p2 = self._bases(ra, dec)
        v = np.asarray(pm_phi1_cosphi2, float)[..., None] * e_p1 + np.asarray(pm_phi2, float)[..., None] * e_p2
        return np.sum(v * e_ra, axis=-1), np.sum(v * e_dec, axis=-1)


def _num(v, name, lo=None, hi=None) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{name} must be a number")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return float(v)


def _radec_pair(p, name) -> tuple[float, float]:
    if not isinstance(p, (list, tuple)) or len(p) != 2:
        raise ValueError(f"{name} must be [ra, dec]")
    return _num(p[0], f"{name} ra", -360, 720) % 360, _num(p[1], f"{name} dec", -90, 90)


def _bounding_cone(ra, dec) -> tuple[float, float, float]:
    v = unit(ra, dec)
    c = v.mean(axis=0)
    cra, cdec = radec(c)
    r = float(np.max(separation(cra, cdec, ra, dec)))
    return float(cra), float(cdec), r


class Region:
    """A query region; build with Region.from_spec(spec)."""

    def __init__(self, spec: dict):
        self.spec = spec
        self.kind = spec["kind"]
        self.name = spec.get("name") or self.kind
        self.frame: GreatCircleFrame | None = None
        if self.kind == "cone":
            self.ra, self.dec = _radec_pair([spec["ra"], spec["dec"]], "center")
            self.radius = _num(spec["radius_deg"], "radius_deg", 0, 30)
        elif self.kind == "box":
            self.ra, self.dec = _radec_pair([spec["ra"], spec["dec"]], "center")
            self.width = _num(spec["width_deg"], "width_deg", 0, MAX_BOX_SIDE_DEG)
            self.height = _num(spec["height_deg"], "height_deg", 0, MAX_BOX_SIDE_DEG)
            self.pa = _num(spec.get("pa_deg", 0.0), "pa_deg", -360, 360)
            w, h, t = self.width / 2, self.height / 2, math.radians(self.pa)
            # Corners in the tangent plane (degrees); pa rotates the box east of north.
            corners = [(-w, -h), (w, -h), (w, h), (-w, h)]
            self._px = [math.degrees(math.tan(math.radians(x * math.cos(t) + y * math.sin(t)))) for x, y in corners]
            self._py = [math.degrees(math.tan(math.radians(-x * math.sin(t) + y * math.cos(t)))) for x, y in corners]
        elif self.kind == "polygon":
            pts = spec.get("vertices")
            if not isinstance(pts, list) or not 3 <= len(pts) <= 200:
                raise ValueError("a polygon needs 3-200 vertices [[ra, dec], ...]")
            pts = [_radec_pair(p, "vertex") for p in pts]
            ra, dec = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
            self.ra, self.dec, extent = _bounding_cone(ra, dec)
            if extent > MAX_POLYGON_EXTENT_DEG:
                raise ValueError(f"polygon extends {extent:.1f} deg from its centre; at most "
                                 f"{MAX_POLYGON_EXTENT_DEG} (use a band for long regions)")
            self._px, self._py, _ = gnomonic(ra, dec, self.ra, self.dec)
        elif self.kind == "band":
            self._init_band(spec)
        else:
            raise ValueError(f"unknown region kind {self.kind!r}; one of {KINDS}")
        if self.area_deg2 > MAX_AREA_DEG2:
            raise ValueError(f"region area {self.area_deg2:.0f} deg2 exceeds {MAX_AREA_DEG2:.0f}")

    @classmethod
    def from_spec(cls, spec: dict) -> "Region":
        if not isinstance(spec, dict) or "kind" not in spec:
            raise ValueError("a region spec needs a kind")
        return cls(spec)

    # ── bands ────────────────────────────────────────────────────────────────
    def _init_band(self, spec):
        track = spec.get("track")
        if not isinstance(track, list) or not 2 <= len(track) <= 5000:
            raise ValueError("a band needs a track of 2-5000 [ra, dec] points")
        pts = [_radec_pair(p, "track point") for p in track]
        ra, dec = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
        self.frame = GreatCircleFrame.from_spec(spec["frame"]) if spec.get("frame") else GreatCircleFrame.fit(ra, dec)
        phi1, phi2 = self.frame.to_frame(ra, dec)
        order = np.argsort(phi1)
        self._phi1, self._phi2 = phi1[order], phi2[order]
        if np.any(np.diff(self._phi1) <= 0):
            keep = np.concatenate([[True], np.diff(self._phi1) > 0])
            self._phi1, self._phi2, order = self._phi1[keep], self._phi2[keep], order[keep]
        width = spec.get("width_deg")
        if isinstance(width, list):
            if len(width) != len(track):
                raise ValueError("width_deg as a list needs one width per track point")
            self._width = np.array([_num(w, "width_deg", 0, 20) for w in width])[order]
        else:
            self._width = np.full(len(self._phi1), _num(width, "width_deg", 0, 20))
        lo, hi = spec.get("phi1_range") or [float(self._phi1[0]), float(self._phi1[-1])]
        self.phi1_range = (max(_num(lo, "phi1_range"), float(self._phi1[0])),
                           min(_num(hi, "phi1_range"), float(self._phi1[-1])))
        if self.phi1_range[1] <= self.phi1_range[0]:
            raise ValueError("phi1_range doesn't overlap the track")
        self._order = order
        mid = 0.5 * sum(self.phi1_range)
        self.ra, self.dec = (float(v) for v in self.frame.to_icrs(mid, self.phi2_track(mid)))

    def phi2_track(self, phi1):
        return np.interp(phi1, self._phi1, self._phi2)

    def width_at(self, phi1):
        return np.interp(phi1, self._phi1, self._width)

    def track_values(self, key: str):
        """A per-track-point list from the spec (e.g. 'pm_track'), in phi1 order."""
        vals = self.spec.get(key)
        return None if vals is None else np.asarray(vals, float)[self._order]

    def pm_track_frame(self, phi1):
        """Track proper motion in the stream frame at phi1, or None without a pm_track."""
        pm = self.track_values("pm_track")
        if pm is None:
            return None
        ra, dec = self.frame.to_icrs(self._phi1, self._phi2)
        p1, p2 = self.frame.pm_to_frame(ra, dec, pm[:, 0], pm[:, 1])
        return np.interp(phi1, self._phi1, p1), np.interp(phi1, self._phi1, p2)

    # ── shared answers ───────────────────────────────────────────────────────
    @property
    def area_deg2(self) -> float:
        if self.kind == "cone":
            return 2 * math.pi * (1 - math.cos(math.radians(self.radius))) * (180 / math.pi) ** 2
        if self.kind in ("box", "polygon"):
            x, y = np.asarray(self._px), np.asarray(self._py)
            return abs(0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)))  # tangent plane; small regions
        grid = np.linspace(*self.phi1_range, 400)
        trapezoid = getattr(np, "trapezoid", None) or np.trapz
        return float(trapezoid(self.width_at(grid) * np.cos(np.deg2rad(self.phi2_track(grid))), grid))

    def contains(self, ra, dec) -> np.ndarray:
        ra, dec = np.asarray(ra, float), np.asarray(dec, float)
        if self.kind == "cone":
            return separation(ra, dec, self.ra, self.dec) <= self.radius
        if self.kind in ("box", "polygon"):
            x, y, ok = gnomonic(ra, dec, self.ra, self.dec)
            return ok & in_polygon(x, y, self._px, self._py)
        phi1, phi2 = self.frame.to_frame(ra, dec)
        lo, hi = self.phi1_range
        within = (phi1 >= lo) & (phi1 <= hi)
        return within & (np.abs(phi2 - self.phi2_track(phi1)) <= self.width_at(phi1) / 2)

    def _band_polygon(self, lo: float, hi: float, pad: float = 0.0, step: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
        n = max(2, int(math.ceil((hi - lo) / step)) + 1)
        p1 = np.linspace(lo, hi, n)
        half = self.width_at(p1) / 2 + pad
        up = self.frame.to_icrs(p1, self.phi2_track(p1) + half)
        down = self.frame.to_icrs(p1[::-1], (self.phi2_track(p1) - half)[::-1])
        return np.concatenate([up[0], down[0]]), np.concatenate([up[1], down[1]])

    def outline(self) -> list[list[float]]:
        """One closed outline, [[ra, dec], ...], for drawing."""
        if self.kind == "cone":
            t = np.linspace(0, 360, 145)[:-1]
            ra, dec = from_gnomonic(np.rad2deg(np.tan(np.deg2rad(self.radius))) * np.sin(np.deg2rad(t)),
                                    np.rad2deg(np.tan(np.deg2rad(self.radius))) * np.cos(np.deg2rad(t)), self.ra, self.dec)
        elif self.kind in ("box", "polygon"):
            # Densify edges so they draw as the tangent-plane straight lines they are.
            xs, ys = [], []
            n = len(self._px)
            for i in range(n):
                t = np.linspace(0, 1, 9)[:-1]
                xs += list(self._px[i] + t * (self._px[(i + 1) % n] - self._px[i]))
                ys += list(self._py[i] + t * (self._py[(i + 1) % n] - self._py[i]))
            ra, dec = from_gnomonic(np.array(xs), np.array(ys), self.ra, self.dec)
        else:
            ra, dec = self._band_polygon(*self.phi1_range)
        return [[round(float(a), 6), round(float(d), 6)] for a, d in zip(ra, dec)]

    def parts(self, pad_deg: float = 0.02) -> list[dict]:
        """Pieces to query archives with: {"polygon": [[ra, dec], ...]} or {"circle": [ra, dec, r]},
        each with a bounding "cone" [ra, dec, r]; their union covers the region (filter
        results with contains)."""
        if self.kind == "cone":
            return [{"circle": [self.ra, self.dec, self.radius], "cone": [self.ra, self.dec, self.radius]}]
        if self.kind in ("box", "polygon"):
            ring = self.outline()
            ra, dec = np.array([p[0] for p in ring]), np.array([p[1] for p in ring])
            cra, cdec, r = _bounding_cone(ra, dec)
            return [{"polygon": ring, "cone": [cra, cdec, r + pad_deg]}]
        lo, hi = self.phi1_range
        n = max(1, int(math.ceil((hi - lo) / SEGMENT_DEG)))
        edges = np.linspace(lo, hi, n + 1)
        out = []
        for a, b in zip(edges[:-1], edges[1:]):
            ra, dec = self._band_polygon(a - pad_deg, b + pad_deg, pad=pad_deg, step=1.0)
            cra, cdec, r = _bounding_cone(ra, dec)
            out.append({"polygon": [[round(float(x), 6), round(float(y), 6)] for x, y in zip(ra, dec)],
                        "cone": [cra, cdec, r + pad_deg], "phi1": [float(a), float(b)]})
        return out

    def cover_cones(self, pad_deg: float = 0.02) -> list[tuple[float, float, float]]:
        """Small circles (ra, dec, radius) covering the region, for archives that only take
        cones: a chain along a band's track, or a grid over a box/polygon wider than ~1.5 deg.
        Much less sky than one bounding cone per piece (MAST takes minutes per 5 deg cone)."""
        if self.kind == "cone":
            return [(self.ra, self.dec, self.radius)]
        if self.kind == "band":
            lo, hi = self.phi1_range
            step = max(1.0, float(self._width.max()))
            n = max(1, int(math.ceil((hi - lo) / step)))
            edges = np.linspace(lo, hi, n + 1)
            out = []
            for a, b in zip(edges[:-1], edges[1:]):
                grid = np.linspace(a, b, 5)
                w = float(np.max(self.width_at(grid)))
                c1 = 0.5 * (a + b)
                ra, dec = self.frame.to_icrs(c1, self.phi2_track(c1))
                # Reach the corners of this stretch of the band (the track may tilt across it).
                cra, cdec = self.frame.to_icrs(np.r_[grid, grid], np.r_[self.phi2_track(grid) + w / 2,
                                                                       self.phi2_track(grid) - w / 2])
                r = float(np.max(separation(float(ra), float(dec), cra, cdec)))
                out.append((float(ra), float(dec), r + pad_deg))
            return out
        cra, cdec, r = self.bounding_cone()
        if r <= 1.5:
            return [(cra, cdec, r + pad_deg)]
        ring = np.array(self.outline())
        x, y, _ = gnomonic(ring[:, 0], ring[:, 1], cra, cdec)
        R = 1.0
        s = R * math.sqrt(2)  # a square grid of circles of radius R leaves no gaps at this spacing
        out = []
        for gx in np.arange(x.min(), x.max() + s, s):
            for gy in np.arange(y.min(), y.max() + s, s):
                ra, dec = from_gnomonic(np.array([gx]), np.array([gy]), cra, cdec)
                near = separation(float(ra[0]), float(dec[0]), ring[:, 0], ring[:, 1]).min() <= R
                if near or self.contains(ra, dec)[0]:
                    out.append((float(ra[0]), float(dec[0]), R + pad_deg))
        return out

    def bounding_cone(self) -> tuple[float, float, float]:
        ring = self.outline()
        return _bounding_cone(np.array([p[0] for p in ring]), np.array([p[1] for p in ring]))

    def to_spec(self) -> dict:
        """The spec, with the band's frame filled in (so it is reproducible)."""
        out = dict(self.spec)
        if self.frame is not None:
            out["frame"] = self.frame.to_spec()
            out["phi1_range"] = list(self.phi1_range)
        return out

    def describe(self) -> dict:
        out = {"kind": self.kind, "name": self.name, "center": [round(self.ra, 5), round(self.dec, 5)],
               "area_deg2": round(self.area_deg2, 3), "pieces": len(self.parts())}
        if self.kind == "band":
            out.update(phi1_range=[round(v, 3) for v in self.phi1_range],
                       length_deg=round(self.phi1_range[1] - self.phi1_range[0], 2),
                       width_deg=[round(float(self._width.min()), 3), round(float(self._width.max()), 3)],
                       frame=self.frame.to_spec())
        return out


def adql_condition(part: dict, ra: str = "ra", dec: str = "dec") -> str:
    """ADQL for one part: CONTAINS(POINT, POLYGON or CIRCLE) = 1."""
    if "circle" in part:
        a, d, r = part["circle"]
        shape = f"CIRCLE('ICRS', {a:.7f}, {d:.7f}, {r:.7f})"
    else:
        shape = "POLYGON('ICRS', " + ", ".join(f"{a:.6f}, {d:.6f}" for a, d in part["polygon"]) + ")"
    return f"1=CONTAINS(POINT('ICRS', {ra}, {dec}), {shape})"


__all__ = ["GreatCircleFrame", "Region", "adql_condition", "gnomonic", "in_polygon", "separation", "unit"]
