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
