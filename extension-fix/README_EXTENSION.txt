THE CHANGE SENTINEL — CASE REVIEW UX EXTENSION

Purpose
-------
This extension addresses the case-management confusion shown in the screenshots.

It adds:
1. An “Open Analysis Workspace” button from the selected case.
2. A dedicated “Complete review — insufficient evidence” action for NOT_ASSESSED cases.
   This records status=reviewed and a formal insufficient_evidence decision in the audit trail.
   It DOES NOT change the algorithm's immutable analysis state to a successful assessment.
3. A clear warning that the existing PNG thumbnail is not a map-aligned overlay.

Apply on Windows
----------------
1. Stop Streamlit (Ctrl+C in the terminal running it).
2. Extract this ZIP into a folder inside your project, e.g. extension-fix.
3. Open PowerShell in the project root.
4. Make a backup:
   Copy-Item app.py app.py.before-review-extension.bak
5. Run the script (if extracted into extension-fix):
   python .\extension-fix\apply_extension.py .\app.py
   If extracted directly into the project root:
   python .\apply_extension.py .\app.py
6. Start the app again:
   streamlit run app.py

Safety
------
The script checks that every expected code section occurs exactly once. If the source differs, it stops without writing app.py. It does not modify the database, case records, satellite data, or credentials.

Important distinction
---------------------
“Reviewed — insufficient evidence” is a human review outcome, not a successful satellite assessment. A new analysis run creates a new immutable case. The extension intentionally does not let an operator falsely mark missing imagery as a successful satellite assessment.
