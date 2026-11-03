"""Create a native-language companion report using Sarvam; source evidence stays immutable."""
from __future__ import annotations
import json
from pathlib import Path
from docx import Document
from docx.shared import Pt
from .sarvam import translate_text


def build_native_report(store, case_id: str, out_path: Path, language_name: str, language_code: str) -> Path:
    case = store.get_case(case_id)
    if case is None:
        raise KeyError(case_id)
    evidence = json.loads(case["evidence"])
    limitations = json.loads(case["limitations"])
    metrics = evidence.get("metrics", [])
    # Translate narrative fields individually; keep measured values, IDs, dates and scores exact.
    doc = Document()
    styles = doc.styles
    styles["Normal"].font.name = "Noto Sans"
    styles["Normal"].font.size = Pt(10)
    title = translate_text("Change Sentinel - Official Analysis Report", language_code)
    doc.add_heading(title, 0)
    doc.add_paragraph(f"English companion report: {case_id}")
    doc.add_paragraph("MACHINE-TRANSLATED text (Sarvam AI). The English PDF case file is authoritative; verify before any action.")
    doc.add_paragraph(translate_text("This report is an automated screening assessment for officer review. It is not a finding of illegality, ownership or intent.", language_code))
    sections = [
        ("Case summary", [f"Case ID: {case_id}", f"Pipeline: {case['pipeline']}", f"Analysis state: {case['state']}",
         f"Review status: {case['status']}", f"Observation period: {case['period_start']} to {case['period_end']}",
         f"Heuristic review score: {case['score']} ({case['score_label']})", f"Summary: {case['summary']}"]),
        ("Geographic jurisdiction", [f"Jurisdiction: {evidence.get('jurisdiction') or 'Not available'}",
         f"AOI: {(evidence.get('aoi') or {}).get('name', 'Not available')}"]),
        ("Evidence indicators", [f"{m.get('indicator')}: {m.get('value')} — {m.get('note','')}" for m in metrics]),
        ("Method and rationale", [str(evidence.get('method') or 'Not recorded'), str(evidence.get('confidence_rationale') or 'Not recorded')]),
        ("Limitations", [str(x) for x in limitations]),
        ("Data sources", [str(x) for x in evidence.get('sources', [])]),
    ]
    for heading, lines in sections:
        doc.add_heading(translate_text(heading, language_code), level=1)
        for line in lines:
            # Translate prose while retaining numeric values and source identifiers as text.
            doc.add_paragraph(translate_text(line, language_code))
    doc.add_paragraph(translate_text("Prepared with Sarvam AI language services. Verify all findings against source imagery and field evidence before administrative action.", language_code))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    store.log(case_id, "system", "native_report_generated", f"Sarvam AI | {language_name} ({language_code})")
    return out_path
