"""Small-field sky geometry in ICRS degrees: separations, STC-S footprints, regions.

Polygons are tested in a gnomonic (tangent-plane) projection about the point
or circle being tested, which is accurate for the arcminute-to-degree scales
of archive footprints. No astropy dependency, so tools and tests stay light.
"""
from __future__ import annotations

import math
import re

Polygon = list[tuple[float, float]]
_NUMBER = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")


def separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle distance (haversine; stable at small separations)."""
    p1, p2 = math.radians(dec1), math.radians(dec2)
    dra, ddec = math.radians(ra2 - ra1), p2 - p1
    h = math.sin(ddec / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dra / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(h))))


def project(ra: float, dec: float, ra0: float, dec0: float) -> tuple[float, float] | None:
    """Gnomonic projection about (ra0, dec0), in degrees; None on the far hemisphere."""
    a, d, a0, d0 = map(math.radians, (ra, dec, ra0, dec0))
    cos_c = math.sin(d0) * math.sin(d) + math.cos(d0) * math.cos(d) * math.cos(a - a0)
    if cos_c <= 0:
        return None
    x = math.cos(d) * math.sin(a - a0) / cos_c
    y = (math.cos(d0) * math.sin(d) - math.sin(d0) * math.cos(d) * math.cos(a - a0)) / cos_c
    return math.degrees(x), math.degrees(y)


def parse_stcs(text: str) -> list[tuple[str, list[float]]]:
    """Shapes in an STC-S string: [('polygon', [ra, dec, ...]) | ('circle', [ra, dec, r])].

    Frame and reference-position words are skipped; UNION is flattened.
    """
    shapes: list[tuple[str, list[float]]] = []
    for token in (text or "").replace("(", " ").replace(")", " ").split():
        word = token.upper()
        if word in ("POLYGON", "CIRCLE"):
            shapes.append((word.lower(), []))
        elif _NUMBER.match(token) and shapes:
            shapes[-1][1].append(float(token))
    valid = []
    for kind, numbers in shapes:
        if kind == "circle" and len(numbers) == 3:
            valid.append((kind, numbers))
        elif kind == "polygon" and len(numbers) >= 6 and len(numbers) % 2 == 0:
            valid.append((kind, numbers))
    return valid


def _polygon_xy(numbers: list[float], ra0: float, dec0: float) -> Polygon | None:
    points = [project(numbers[i], numbers[i + 1], ra0, dec0) for i in range(0, len(numbers), 2)]
    return None if any(p is None for p in points) else points  # type: ignore[return-value]


def _contains_origin(poly: Polygon) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > 0) != (y2 > 0) and 0 < x1 + (0 - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _distance_to_edges(poly: Polygon) -> float:
    best = math.inf
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        dx, dy = x2 - x1, y2 - y1
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(x1 + t * dx, y1 + t * dy))
    return best


def footprint_contains(stcs: str, ra: float, dec: float) -> bool:
    for kind, numbers in parse_stcs(stcs):
        if kind == "circle":
            if separation_deg(ra, dec, numbers[0], numbers[1]) <= numbers[2]:
                return True
        else:
            poly = _polygon_xy(numbers, ra, dec)
            if poly and _contains_origin(poly):
                return True
    return False


def footprint_overlaps_circle(stcs: str, ra: float, dec: float, radius: float) -> bool:
    for kind, numbers in parse_stcs(stcs):
        if kind == "circle":
            if separation_deg(ra, dec, numbers[0], numbers[1]) <= numbers[2] + radius:
                return True
        else:
            poly = _polygon_xy(numbers, ra, dec)
            if poly and (_contains_origin(poly) or _distance_to_edges(poly) <= radius):
                return True
    return False


class Footprint:
    """A parsed STC-S footprint with a bounding circle for fast rejection."""

    def __init__(self, stcs: str) -> None:
        self.stcs = stcs
        self.shapes = parse_stcs(stcs)
        points = [(n[i], n[i + 1]) for kind, n in self.shapes if kind == "polygon"
                  for i in range(0, len(n), 2)]
        points += [(n[0], n[1]) for kind, n in self.shapes if kind == "circle"]
        if not points:
            self.center, self.reach = None, 0.0
            return
        ref = points[0][0]
        ras = [ref + ((ra - ref + 180) % 360) - 180 for ra, _ in points]
        self.center = ((sum(ras) / len(ras)) % 360, sum(d for _, d in points) / len(points))
        self.reach = max(separation_deg(*self.center, ra, dec) for ra, dec in points)
        self.reach += max((n[2] for kind, n in self.shapes if kind == "circle"), default=0.0)

    def near(self, ra: float, dec: float, margin: float = 0.0) -> bool:
        return self.center is not None and separation_deg(*self.center, ra, dec) <= self.reach + margin

    def contains(self, ra: float, dec: float) -> bool:
        return self.near(ra, dec) and footprint_contains(self.stcs, ra, dec)

    def polygons(self) -> list[list[tuple[float, float]]]:
        """Every shape as [(ra, dec), ...]; circles become 32-gons."""
        out = []
        for kind, n in self.shapes:
            if kind == "polygon":
                out.append([(n[i], n[i + 1]) for i in range(0, len(n), 2)])
            else:
                ra0, dec0, r = n
                cos = max(math.cos(math.radians(dec0)), 1e-6)
                out.append([(ra0 + r * math.sin(t) / cos, dec0 + r * math.cos(t))
                            for t in (2 * math.pi * k / 32 for k in range(32))])
        return out

    def overlaps(self, other: "Footprint") -> bool:
        """Whether any shape of this footprint intersects any shape of the other."""
        if self.center is None or other.center is None:
            return False
        if separation_deg(*self.center, *other.center) > self.reach + other.reach:
            return False
        ra0, dec0 = self.center
        for a in self.polygons():
            pa = [project(ra, dec, ra0, dec0) for ra, dec in a]
            for b in other.polygons():
                pb = [project(ra, dec, ra0, dec0) for ra, dec in b]
                if None in pa or None in pb:
                    continue
                if _polygons_intersect(pa, pb):
                    return True
        return False


def _segments_cross(p1, p2, q1, q2) -> bool:
    def side(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = side(q1, q2, p1), side(q1, q2, p2)
    d3, d4 = side(p1, p2, q1), side(p1, p2, q2)
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)


def _inside(point, poly) -> bool:
    shifted = [(x - point[0], y - point[1]) for x, y in poly]
    return _contains_origin(shifted)


def _polygons_intersect(a: Polygon, b: Polygon) -> bool:
    edges_a, edges_b = list(zip(a, a[1:] + a[:1])), list(zip(b, b[1:] + b[:1]))
    if any(_segments_cross(p1, p2, q1, q2) for p1, p2 in edges_a for q1, q2 in edges_b):
        return True
    return _inside(a[0], b) or _inside(b[0], a)


def polygon_contains(points: list[list[float]], ra: float, dec: float) -> bool:
    """Whether a point lies inside a polygon given as [[ra, dec], ...]."""
    poly = _polygon_xy([v for point in points for v in point], ra, dec)
    return bool(poly) and _contains_origin(poly)


def polygon_contains_circle(points: list[list[float]], ra: float, dec: float, radius: float) -> bool:
    """Whether a circle lies wholly inside a polygon given as [[ra, dec], ...]."""
    poly = _polygon_xy([v for point in points for v in point], ra, dec)
    return bool(poly) and _contains_origin(poly) and _distance_to_edges(poly) >= radius


__all__ = ["Footprint", "polygon_contains", "footprint_contains", "footprint_overlaps_circle", "parse_stcs",
           "polygon_contains_circle", "project", "separation_deg"]
