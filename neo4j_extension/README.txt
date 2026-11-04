CHANGE SENTINEL — NEO4J PANEL EXTENSION

WHAT IT ADDS
A read-only Neo4j panel in the Streamlit app. It checks connectivity and displays node count, relationship count, labels and relationship types. It does not change case assessments or write graph data.

INSTALL (Windows PowerShell)
1. Extract this ZIP.
2. Open PowerShell in your sentinel-change-main project folder (the folder containing app.py).
3. Run:
   python .\neo4j-extension\scripts\install_neo4j_panel.py .
   (If you extracted the folder under another name, adjust the path.)
4. Install the driver in your active virtual environment:
   pip install neo4j
5. Configure secrets. For local use, add these to your untracked .env file or set them in PowerShell:
   $env:CS_NEO4J_URI="neo4j+s://<your-instance>.databases.neo4j.io"
   $env:CS_NEO4J_USER="neo4j"
   $env:CS_NEO4J_PASSWORD="<your-password>"
6. Restart Streamlit. Expand “Neo4j Knowledge Graph” at the bottom of the app and click “Connect / refresh graph”.

STREAMLIT CLOUD
Add the same three values in App → Settings → Secrets, using TOML:
CS_NEO4J_URI = "neo4j+s://<your-instance>.databases.neo4j.io"
CS_NEO4J_USER = "neo4j"
CS_NEO4J_PASSWORD = "your-password"

IMPORTANT
- Never put real credentials in GitHub or .env.example.
- This extension is intentionally read-only. Your project already has a Neo4j sync module; the exact sync call is not changed here because the current source archive was not available to verify its function signature.
- Back up app.py before installation. The installer refuses to overwrite an existing extension module and adds a small panel at the end of app.py.
- To undo: remove the appended block at the bottom of app.py and delete cs/neo4j_dashboard_extension.py.
