from __future__ import annotations
import sys
from pathlib import Path

IMPORT_LINE = "from cs import mapview, registry as registry_mod, workflow"
NEW_IMPORT = IMPORT_LINE + "\nfrom cs_case_map_overlay_v2 import render_case_map"
OLD_BLOCK = '''        preview_path = Path(exp.get("preview_png", ""))
        if preview_path.exists():
            st.image(str(preview_path), caption="Raster analysis grid preview (red = flagged indicator, grey = unobserved; north-up grid, not a map-aligned overlay)", width=420)
'''
NEW_BLOCK = '''        # Georeferenced case map extension v2: render spatial data, not the ungeoreferenced PNG grid.
        st.markdown("#### Georeferenced Case Map")
        render_case_map(c, exp, SETTINGS, registry_mod)
'''

def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python apply_georeferenced_map_patch_v2.py path\\to\\app.py")
    target = Path(sys.argv[1]).resolve()
    if not target.is_file():
        raise SystemExit(f"Target app.py not found: {target}")
    text = target.read_text(encoding="utf-8")
    if "from cs_case_map_overlay_v2 import render_case_map" not in text:
        if IMPORT_LINE not in text:
            raise SystemExit("Expected app.py import line not found. No changes made.")
        text = text.replace(IMPORT_LINE, NEW_IMPORT, 1)
    if NEW_BLOCK not in text:
        if OLD_BLOCK not in text:
            raise SystemExit("Expected raster preview block not found. No changes made; your app.py was not modified.")
        text = text.replace(OLD_BLOCK, NEW_BLOCK, 1)
    target.write_text(text, encoding="utf-8")
    print("Applied georeferenced case map patch v2.")
    print("Added module: cs_case_map_overlay_v2.py")

if __name__ == "__main__":
    main()
