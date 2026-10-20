"""Sky geometry: angular separations, ObsCore ``s_region`` parsing, cone overlap.

Overlap tests use a gnomonic (tangent-plane) projection about the cone center.
Great circles project to straight lines and the cone to a circle of radius
tan(r), so the tests are exact for any polygon lying within 90 degrees of the
center.
"""
from __future__ import annotations

import numpy as np

EQUATORIAL_FRAMES = {"ICRS", "FK5", "J2000"}
OTHER_FRAMES = {"GALACTIC", "ECLIPTIC", "FK4", "B1950"}


def separation_deg(ra1, dec1, ra2, dec2):
    """Angular separation in degrees (Vincenty formula; inputs in degrees)."""
    ra1, dec1, ra2, dec2 = map(np.radians, (ra1, dec1, ra2, dec2))
    dra = ra2 - ra1
    num = np.hypot(np.cos(dec2) * np.sin(dra),
                   np.cos(dec1) * np.sin(dec2) - np.sin(dec1) * np.cos(dec2) * np.cos(dra))
    den = np.sin(dec1) * np.sin(dec2) + np.cos(dec1) * np.cos(dec2) * np.cos(dra)
    return np.degrees(np.arctan2(num, den))


def parse_s_region(s_region: str) -> list[tuple]:
    """Parse an STC-S ``s_region`` string into shapes.

    Returns a list of ``("polygon", [(ra, dec), ...])`` and
    ``("circle", (ra, dec, radius))`` tuples, all in degrees. Several shapes in
    one string (e.g. one polygon per detector chip) are all returned. Raises
    ``ValueError`` for non-equatorial frames, since the numbers would not be
    RA/Dec.
    """
    shapes = []
    current = None
    for token in str(s_region).replace("(", " ").replace(")", " ").split():
        word = token.upper()
        if word in ("POLYGON", "CIRCLE"):
            current = [word, []]
            shapes.append(current)
        elif word in OTHER_FRAMES:
            raise ValueError(f"s_region uses non-equatorial frame {word}")
        elif current is not None:
            try:
                current[1].append(float(token))
            except ValueError:
                pass  # frame names (ICRS, ...) and other STC-S words
    out = []
    for kind, nums in shapes:
        if kind == "POLYGON" and len(nums) >= 6 and len(nums) % 2 == 0:
            out.append(("polygon", list(zip(nums[0::2], nums[1::2]))))
        elif kind == "CIRCLE" and len(nums) == 3:
            out.append(("circle", tuple(nums)))
    return out


def _gnomonic(ra, dec, ra0, dec0):
    """Project (ra, dec) onto the tangent plane at (ra0, dec0).

    Returns (x, y, cos_c) with x, y in radians of the tangent plane; points with
    cos_c <= 0 are on the far hemisphere and cannot be projected.
    """
    ra, dec = np.radians(np.asarray(ra, float)), np.radians(np.asarray(dec, float))
    ra0, dec0 = np.radians(ra0), np.radians(dec0)
    cos_c = np.sin(dec0) * np.sin(dec) + np.cos(dec0) * np.cos(dec) * np.cos(ra - ra0)
    x = np.cos(dec) * np.sin(ra - ra0) / cos_c
    y = (np.cos(dec0) * np.sin(dec) - np.sin(dec0) * np.cos(dec) * np.cos(ra - ra0)) / cos_c
    return x, y, cos_c


def _origin_in_polygon(x, y) -> bool:
    """Ray-casting test for whether (0, 0) is inside the polygon."""
    inside = False
    n = len(x)
    for i in range(n):
        x1, y1, x2, y2 = x[i], y[i], x[(i + 1) % n], y[(i + 1) % n]
        if (y1 > 0) != (y2 > 0) and 0 < x1 + (0 - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _origin_to_segment(x1, y1, x2, y2) -> float:
    dx, dy = x2 - x1, y2 - y1
    t = np.clip(-(x1 * dx + y1 * dy) / (dx * dx + dy * dy or 1.0), 0.0, 1.0)
    return float(np.hypot(x1 + t * dx, y1 + t * dy))


def polygon_overlap(vertices, ra0, dec0, radius_deg) -> tuple[bool, bool]:
    """Whether a polygon overlaps a cone, and whether it contains the center.

    ``vertices`` is a sequence of (ra, dec) in degrees.
    """
    ra, dec = np.array(vertices, float).T
    x, y, cos_c = _gnomonic(ra, dec, ra0, dec0)
    if np.any(cos_c <= 0):
        # Polygon reaches the far hemisphere; fall back to vertex distances.
        near = bool(np.any(separation_deg(ra0, dec0, ra, dec) <= radius_deg))
        return near, False
    if _origin_in_polygon(x, y):
        return True, True
    r_tan = np.tan(np.radians(radius_deg))
    n = len(x)
    d_min = min(_origin_to_segment(x[i], y[i], x[(i + 1) % n], y[(i + 1) % n]) for i in range(n))
    return d_min <= r_tan, False


def region_overlap(s_region: str, ra0, dec0, radius_deg) -> tuple[bool, bool]:
    """Whether any shape in ``s_region`` overlaps the cone / contains its center."""
    shapes = parse_s_region(s_region)
    if not shapes:
        raise ValueError(f"could not parse s_region {str(s_region)[:60]!r}")
    overlaps = contains = False
    for kind, geom in shapes:
        if kind == "polygon":
            o, c = polygon_overlap(geom, ra0, dec0, radius_deg)
        else:
            ra, dec, r = geom
            sep = separation_deg(ra0, dec0, ra, dec)
            o, c = sep <= radius_deg + r, sep <= r
        overlaps, contains = overlaps or bool(o), contains or bool(c)
    return overlaps, contains


# ---------------------------------------------------------------- polygon regions

def _points_in_polygon(px, py, x, y):
    """Ray-casting test of many points against one polygon (tangent-plane coordinates)."""
    px, py = np.atleast_1d(px)[:, None], np.atleast_1d(py)[:, None]
    x1, y1, x2, y2 = x, y, np.roll(x, -1), np.roll(y, -1)
    with np.errstate(divide="ignore", invalid="ignore"):
        crosses = ((y1 > py) != (y2 > py)) & (px < x1 + (py - y1) * (x2 - x1) / (y2 - y1))
    return np.count_nonzero(crosses, axis=1) % 2 == 1


def _edges_intersect(ax, ay, bx, by) -> bool:
    """Whether any edge of polygon A crosses any edge of polygon B."""
    a1x, a1y, a2x, a2y = ax[:, None], ay[:, None], np.roll(ax, -1)[:, None], np.roll(ay, -1)[:, None]
    b1x, b1y, b2x, b2y = bx[None, :], by[None, :], np.roll(bx, -1)[None, :], np.roll(by, -1)[None, :]

    def orient(px, py, qx, qy, rx, ry):
        return np.sign((qx - px) * (ry - py) - (qy - py) * (rx - px))

    d1 = orient(a1x, a1y, a2x, a2y, b1x, b1y)
    d2 = orient(a1x, a1y, a2x, a2y, b2x, b2y)
    d3 = orient(b1x, b1y, b2x, b2y, a1x, a1y)
    d4 = orient(b1x, b1y, b2x, b2y, a2x, a2y)
    return bool(np.any((d1 * d2 < 0) & (d3 * d4 < 0)))


def _distance_to_edges(px, py, x, y) -> float:
    """Smallest distance from a point to the polygon's edges (tangent-plane units)."""
    x1, y1, x2, y2 = x, y, np.roll(x, -1), np.roll(y, -1)
    dx, dy = x2 - x1, y2 - y1
    t = np.clip(((px - x1) * dx + (py - y1) * dy) / np.where(dx * dx + dy * dy == 0, 1, dx * dx + dy * dy), 0, 1)
    return float(np.min(np.hypot(x1 + t * dx - px, y1 + t * dy - py)))


class Region:
    """The area a query covers: a cone, or a polygon (e.g. an ellipse traced by its vertices).

    Polygon tests use the tangent plane at the region's center, which is exact
    for straight (great-circle) edges and fine for regions a few degrees across.
    """

    def __init__(self, ra, dec, radius_deg=None, polygon=None):
        self.ra, self.dec = float(ra), float(dec)
        self.polygon = [(float(a), float(b)) for a, b in polygon] if polygon else None
        if self.polygon:
            ra_v, dec_v = np.array(self.polygon).T
            self._x, self._y, cos_c = _gnomonic(ra_v, dec_v, self.ra, self.dec)
            if np.any(cos_c <= 0):
                raise ValueError("polygon region reaches more than 90 degrees from its center")
            self.radius_deg = float(np.max(separation_deg(self.ra, self.dec, ra_v, dec_v)))
        else:
            self.radius_deg = float(radius_deg)

    @classmethod
    def from_query(cls, q):
        r = q.get("radius_arcmin")
        return cls(q["ra"], q["dec"], None if r is None else r / 60, q.get("polygon"))

    def describe(self) -> str:
        if self.polygon:
            return f"{len(self.polygon)}-vertex polygon around ({self.ra:.4f}, {self.dec:.4f})"
        return f"cone of radius {self.radius_deg * 60:.3f} arcmin around ({self.ra:.4f}, {self.dec:.4f})"

    def adql(self) -> str:
        if self.polygon:
            return "POLYGON('ICRS', " + ", ".join(f"{a!r}, {b!r}" for a, b in self.polygon) + ")"
        return f"CIRCLE('ICRS', {self.ra!r}, {self.dec!r}, {self.radius_deg!r})"

    def contains(self, ra, dec, tol_deg: float = 0.0) -> bool:
        if not self.polygon:
            return bool(separation_deg(self.ra, self.dec, ra, dec) <= self.radius_deg + tol_deg)
        x, y, cos_c = _gnomonic(ra, dec, self.ra, self.dec)
        if cos_c <= 0:
            return False
        if _points_in_polygon(x, y, self._x, self._y)[0]:
            return True
        return tol_deg > 0 and _distance_to_edges(float(x), float(y), self._x, self._y) <= np.tan(np.radians(tol_deg))

    def contains_many(self, ra, dec, tol_deg: float = 0.0):
        """Vectorized ``contains``: a boolean array, one entry per (ra, dec)."""
        ra, dec = np.asarray(ra, float), np.asarray(dec, float)
        if not self.polygon:
            return separation_deg(self.ra, self.dec, ra, dec) <= self.radius_deg + tol_deg
        x, y, cos_c = _gnomonic(ra, dec, self.ra, self.dec)
        inside = _points_in_polygon(x, y, self._x, self._y) & (cos_c > 0)
        if tol_deg > 0:
            near = np.array([_distance_to_edges(a, b, self._x, self._y) for a, b in zip(x[~inside], y[~inside])])
            idx = np.flatnonzero(~inside)
            inside[idx[near <= np.tan(np.radians(tol_deg))]] = True
        return inside

    def polygon_overlap(self, vertices) -> tuple[bool, bool]:
        """Whether a polygon (list of (ra, dec)) overlaps the region / contains its center."""
        if not self.polygon:
            return polygon_overlap(vertices, self.ra, self.dec, self.radius_deg)
        ra_v, dec_v = np.array(vertices, float).T
        x, y, cos_c = _gnomonic(ra_v, dec_v, self.ra, self.dec)
        if np.any(cos_c <= 0):
            return False, False
        contains = bool(_points_in_polygon(0.0, 0.0, x, y)[0])
        overlaps = (contains
                    or bool(np.any(_points_in_polygon(x, y, self._x, self._y)))
                    or bool(np.any(_points_in_polygon(self._x, self._y, x, y)))
                    or _edges_intersect(x, y, self._x, self._y))
        return overlaps, contains

    def s_region_overlap(self, s_region: str) -> tuple[bool, bool]:
        """Whether any shape in an ``s_region`` overlaps the region / contains its center."""
        if not self.polygon:
            return region_overlap(s_region, self.ra, self.dec, self.radius_deg)
        shapes = parse_s_region(s_region)
        if not shapes:
            raise ValueError(f"could not parse s_region {str(s_region)[:60]!r}")
        overlaps = contains = False
        for kind, geom in shapes:
            if kind == "polygon":
                o, c = self.polygon_overlap(geom)
            else:
                ra, dec, r = geom
                x, y, _ = _gnomonic(ra, dec, self.ra, self.dec)
                inside = bool(_points_in_polygon(x, y, self._x, self._y)[0])
                o = inside or bool(_distance_to_edges(float(x), float(y), self._x, self._y) <= np.tan(np.radians(r)))
                c = bool(separation_deg(self.ra, self.dec, ra, dec) <= r)
            overlaps, contains = overlaps or o, contains or c
        return overlaps, contains
