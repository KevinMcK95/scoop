"""Sky cutouts for the agent to look at, rendered by CDS hips2fits.

The same HiPS surveys the map shows, at any position and field of view. The
agent sees the image (it is attached to the conversation after the tool result);
catalogs remain the source for identifications and numbers.
"""
from __future__ import annotations

import base64
import math

HIPS2FITS = "https://alasky.cds.unistra.fr/hips-image-services/hips2fits"
# Surveys the agent may request (the map's backgrounds; checked against hips2fits).
SURVEYS = {
    "CDS/P/DSS2/color": "DSS2 optical color",
    "CDS/P/PanSTARRS/DR1/color-z-zg-g": "Pan-STARRS DR1 optical color",
    "CDS/P/DESI-Legacy-Surveys/DR10/color": "DESI Legacy Surveys DR10 color (deep optical)",
    "CDS/P/SDSS9/color": "SDSS9 optical color",
    "CDS/P/2MASS/color": "2MASS near-infrared color (JHK)",
    "CDS/P/unWISE/color-W2-W1W2-W1": "unWISE mid-infrared color (W1/W2)",
}
SIZE = 512  # pixels; enough detail for morphology, small enough to keep cost down


def cutout_params(ra: float, dec: float, fov_deg: float, survey: str) -> dict:
    """Validated query parameters for one JPEG cutout."""
    numbers = (ra, dec, fov_deg)
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in numbers):
        raise ValueError("ra, dec and fov must be finite numbers")
    if not (0 <= ra < 360 and -90 <= dec <= 90):
        raise ValueError("ra must be in [0, 360) and dec in [-90, 90] degrees")
    if not (1 / 3600 <= fov_deg <= 10):
        raise ValueError("fov must be between 1 arcsec and 10 degrees")
    if survey not in SURVEYS:
        raise ValueError(f"unknown survey {survey!r}; choose one of {sorted(SURVEYS)}")
    return {"hips": survey, "width": SIZE, "height": SIZE, "projection": "TAN", "fov": fov_deg,
            "ra": ra, "dec": dec, "format": "jpg"}


def cutout_fetcher(client):
    """An async ``(ra, dec, fov_deg, survey) -> JPEG bytes`` using an active httpx client."""

    async def fetch(ra: float, dec: float, fov_deg: float, survey: str) -> bytes:
        response = await client.get(HIPS2FITS, params=cutout_params(ra, dec, fov_deg, survey), timeout=60.0)
        response.raise_for_status()
        if not response.headers.get("content-type", "").startswith("image/"):
            raise ValueError("hips2fits did not return an image")
        return response.content

    return fetch


def data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


__all__ = ["HIPS2FITS", "SURVEYS", "cutout_fetcher", "cutout_params", "data_url"]
