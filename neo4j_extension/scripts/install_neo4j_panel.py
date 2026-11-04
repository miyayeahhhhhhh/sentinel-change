from pathlib import Path
import shutil
import sys

root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
app = root / 'app.py'
source = Path(__file__).resolve().parents[1] / 'cs' / 'neo4j_dashboard_extension.py'
target_dir = root / 'cs'
target = target_dir / 'neo4j_dashboard_extension.py'
if not app.exists() or not target_dir.exists():
    raise SystemExit('Run this script from the project root (the folder containing app.py and cs/).')
if target.exists():
    raise SystemExit(f'Refusing to overwrite existing file: {target}')
text = app.read_text(encoding='utf-8')
marker = 'from cs.neo4j_dashboard_extension import render_neo4j_panel'
if marker in text:
    raise SystemExit('Neo4j panel import already exists; no changes made.')
shutil.copy2(source, target)
# Add the panel as a new Streamlit expander at the end of app.py. Existing UI remains intact.
with app.open('a', encoding='utf-8', newline='') as f:
    f.write('\n\n# Optional Neo4j graph access panel (added by neo4j-extension).\n')
    f.write(marker + '\n')
    f.write('with st.expander("Neo4j Knowledge Graph", expanded=False):\n')
    f.write('    render_neo4j_panel()\n')
print('Installed Neo4j panel. Existing app.py was appended, not replaced.')
print('Next: pip install neo4j, configure CS_NEO4J_URI / CS_NEO4J_USER / CS_NEO4J_PASSWORD, restart Streamlit.')
