"""The field's target: an LVDB object (center, half-light ellipses) or a cone."""
from ..archives.lvdb import LVDB_RELEASE
from . import Source, eq, layer, register
from ._palette import COLORS


def rows(data: dict) -> list[dict]:
    t = data["target"]
    if t.get("kind") == "cone":
        return [{"id": "center", "ra": t["ra"], "dec": t["dec"], "name": t["name"]},
                {"id": "region", "ra": t["ra"], "dec": t["dec"], "polygon": data["ellipses"]["n"],
                 "name": f"{t['rhalf_arcmin']:g}′ cone (query region)"}]
    return [
        {"id": "center", "ra": t["ra"], "dec": t["dec"], "name": t["name"]},
        {"id": "rhalf", "ra": t["ra"], "dec": t["dec"], "polygon": data["ellipses"]["1"], "name": "1 r_half"},
        {"id": "region", "ra": t["ra"], "dec": t["dec"], "polygon": data["ellipses"]["n"],
         "name": f"{data['n_rhalf']} r_half (query region)"},
    ]


def layers(rows: list[dict], data: dict) -> list[dict]:
    name = data["target"]["name"]
    if data["target"].get("kind") == "cone":
        return [layer("target:center", "target", [eq("id", "center")], name, "Cone center", COLORS["target"],
                      order=90, shape="plus", size=22, hollow=True),
                layer("target:region", "target", [eq("id", "region")], name,
                      f"{data['target']['rhalf_arcmin']:g}′ cone (query region)", COLORS["target"], order=91,
                      dash=True, hollow=True)]
    return [
        layer("target:center", "target", [{"column": "id", "op": "in", "value": ["center", "rhalf"]}],
              name, "Center and r_half ellipse", COLORS["target"], order=90, shape="plus", size=22,
              hollow=True),
        layer("target:region", "target", [eq("id", "region")], name,
              f"{data['n_rhalf']} r_half (query region)", COLORS["target"], order=91, dash=True, hollow=True),
    ]


register(Source(name="target", label="Target", provenance=f"Local Volume Database {LVDB_RELEASE}",
                kind="annotation", order=0, rows=rows, layers=layers))
