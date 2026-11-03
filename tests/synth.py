"""TEST-ONLY synthetic Sentinel-1 scenes (labelled synthetic; never used by the app)."""
import numpy as np
from datetime import datetime, timezone
from cs.imagery import Scene


def synthetic_s1(grid, level_db=-11.0, n=3, year=2025):
    rng = np.random.default_rng(3)
    out = []
    for i in range(n):
        v = rng.normal(level_db, 1.0, (grid.height, grid.width)).astype("float32")
        out.append(Scene(f"SYNTH-S1-{year}{7 + i:02d}10", "sentinel-1", datetime(year, 7 + i, 10, 5, 30, tzinfo=timezone.utc),
                         {"vv_db": v}, "SYNTHETIC TEST FIXTURE - not Sentinel data", True, {}))
    return out
