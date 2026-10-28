"""Result export: GeoTIFF (mask + coverage), GeoJSON, JSON evidence record, PNG preview. Validates outputs."""
from __future__ import annotations
import json
from pathlib import Path

import numpy as np
from PIL import Image

from .geo import check_geotiff, write_geojson, write_geotiff
from .models import DISCLAIMER, Result


def render_png(base: np.ndarray, mask: np.ndarray, observed: np.ndarray, path: Path, scale: int = 3) -> None:
    b = np.nan_to_num(base, nan=np.nanmedian(base) if np.isfinite(base).any() else 0.0)
    lo, hi = np.percentile(b, [2, 98]) if b.size else (0, 1)
    g = np.clip((b - lo) / (hi - lo + 1e-9), 0, 1) * 200 + 30
    rgb = np.stack([g, g, g], -1)
    rgb[~observed] = (150, 155, 165)                       # not assessed = flat grey
    rgb[mask] = 0.4 * rgb[mask] + 0.6 * np.array([200, 60, 40])
    img = Image.fromarray(rgb.astype("uint8")).resize((rgb.shape[1] * scale, rgb.shape[0] * scale), Image.NEAREST)
    img.save(path)


def export_result(result: Result, case_id: str, outdir: Path) -> dict:
    d = Path(outdir) / case_id
    d.mkdir(parents=True, exist_ok=True)
    a, grid = result.arrays, result.grid
    mask = a[result.primary_layer].astype(bool)
    out = mask.astype("uint8")
    out[~a["observed"].astype(bool)] = 255                  # unobserved = nodata, never "0"
    paths = {"change_mask_tif": d / "change_mask.tif", "coverage_tif": d / "coverage.tif",
             "polygons_geojson": d / "polygons.geojson", "evidence_json": d / "evidence.json",
             "preview_png": d / "preview.png"}
    write_geotiff(paths["change_mask_tif"], out, grid, nodata=255)
    write_geotiff(paths["coverage_tif"], a["coverage"].astype("float32"), grid, nodata=-1.0)
    for k in ("change_mask_tif", "coverage_tif"):
        if not check_geotiff(paths[k], grid):
            raise IOError(f"Exported raster {paths[k]} failed CRS/transform/shape validation")
    if result.polygons is not None:
        write_geojson(result.polygons, paths["polygons_geojson"])
    else:
        paths["polygons_geojson"].write_text('{"type": "FeatureCollection", "features": []}')
    render_png(a["context"], mask, a["observed"].astype(bool), paths["preview_png"])
    record = {"case_id": case_id, "pipeline": result.pipeline.value, "state": result.state.value, "summary": result.summary,
              "score": result.score, "score_label": result.score_label, "synthetic_inputs": result.synthetic,
              "evidence": result.evidence, "limitations": result.limitations, "params": result.params,
              "crs": {"exchange": "EPSG:4326", "area_calc": str(grid.crs)}, "disclaimer": DISCLAIMER}
    paths["evidence_json"].write_text(json.dumps(record, indent=2, default=str))
    return {k: str(v) for k, v in paths.items()}
