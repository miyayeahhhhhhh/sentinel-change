"""PDF case file (ReportLab). Reads only the stored case record + exported preview; no analytics."""
from __future__ import annotations
import json
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .db import CaseStore
from .models import DISCLAIMER

SS = getSampleStyleSheet()
SMALL = ParagraphStyle("small", parent=SS["Normal"], fontSize=8, leading=10)
BODY = ParagraphStyle("body", parent=SS["Normal"], fontSize=9, leading=12)
H = ParagraphStyle("h", parent=SS["Heading3"], textColor=colors.HexColor("#1f3a5f"), spaceBefore=8, spaceAfter=3)


def _p(t, st=SMALL):
    return Paragraph(escape(str(t)), st)


def _table(data, widths):
    t = Table([[_p(c) for c in r] for r in data], colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#b8c0c8")),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8edf2")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


def build_pdf(store: CaseStore, case_id: str, out_path: Path) -> Path:
    c = store.get_case(case_id)
    if c is None:
        raise KeyError(case_id)
    ev, lim, par, exp = json.loads(c["evidence"]), json.loads(c["limitations"]), json.loads(c["params"]), json.loads(c["exports"])
    s = [Paragraph(f"Review case {case_id}", SS["Title"]), _p(DISCLAIMER, BODY)]
    if c["synthetic"]:
        s += [Spacer(1, 4), Paragraph("<b>SAMPLE DATA: inputs are synthetic fixtures, not satellite observations.</b>", BODY)]
    s += [Paragraph("Case summary", H), _table([
        ["Pipeline", c["pipeline"]], ["Analysis state", c["state"]], ["Review status", c["status"]],
        ["Observation period", f"{c['period_start']} to {c['period_end']}"],
        ["Heuristic score", f"{c['score']}  ({c['score_label']})" if c["score"] is not None else "none (no flagged change)"],
        ["Created (UTC)", c["created_at"]], ["Class", ev.get("class") or "-"]], [45 * mm, 125 * mm]),
        Spacer(1, 4), _p(c["summary"], BODY)]
    if Path(exp["preview_png"]).exists():
        s += [Paragraph("Map (analysis grid; red = flagged, grey = not assessed)", H), Image(exp["preview_png"], 85 * mm, 85 * mm)]
    
    # Decision-ready coordinates and jurisdiction
    coords = ev.get("coordinates")
    jur = ev.get("jurisdiction")
    if coords or jur:
        rows_geo = []
        if coords:
            c_lat, c_lon = coords.get("centroid_wgs84", ["-", "-"])
            rows_geo.append(["Centroid (WGS84)", f"{c_lat}°N, {c_lon}°E (CRS: {coords.get('crs', '-')})"])
            b_w, b_s, b_e, b_n = coords.get("bbox_wgs84", ["-", "-", "-", "-"])
            rows_geo.append(["Bounding Box (WGS84)", f"West: {b_w}, South: {b_s}, East: {b_e}, North: {b_n}"])
        if jur:
            rows_geo.append(["Administrative Jurisdiction", jur])
        s += [Paragraph("Geographic Location & Administrative Jurisdiction", H),
              _table(rows_geo, [55 * mm, 115 * mm])]

    s += [Paragraph("Evidence indicators", H),
          _table([["Indicator", "Value", "Note"]] + [[m["indicator"], m["value"], m["note"]] for m in ev.get("metrics", [])], [65 * mm, 40 * mm, 65 * mm])]
    
    # Method and confidence rationale
    rat = ev.get("confidence_rationale")
    meth = ev.get("method")
    if rat or meth:
        s += [Paragraph("Method & Decision-Readiness Rationale", H)]
        if meth:
            s += [_p(f"<b>Method:</b> {meth}", BODY)]
        if rat:
            s += [_p(f"<b>Evidence / Priority Rationale:</b> {rat}", BODY)]

    # Recommended verification protocol
    rec_v = ev.get("recommended_verification")
    if rec_v:
        s += [Paragraph("Recommended Officer Verification Protocol", H)] + [_p(v, BODY) for v in rec_v]
    if ev.get("score_components"):
        s += [Paragraph("Score components (renormalised over available weights)", H),
              _table([["Component", "Normalised", "Weight", "Available"]] + [[r["name"], r["normalised"], r["weight"], r["available"]] for r in ev["score_components"]],
                     [60 * mm, 35 * mm, 35 * mm, 40 * mm])]
    s += [Paragraph("Observations", H),
          _table([["Scene", "Acquired (UTC)", "Valid", "Usable", "Role"]] + [[r["scene_id"], r["acquired"][:16], r["valid_frac"], r["usable"], r.get("role", "")] for r in ev.get("scenes", [])][:40],
                 [50 * mm, 40 * mm, 20 * mm, 20 * mm, 40 * mm]),
          Paragraph("Limitations", H)] + [_p("- " + x, BODY) for x in lim]
    sc = ev.get("sensor_corroboration")
    if isinstance(sc, dict):
        sc_status = sc.get("status", "unknown").upper()
        sc_reason = f" - {sc.get('reason')}" if sc.get("reason") else ""
        corrob_str = f"{sc_status}{sc_reason}"
    else:
        corrob_str = str(sc or "not available")
    s += [Paragraph("Sensor corroboration / jurisdiction", H), _p(f"Corroboration: {corrob_str}. Jurisdiction: {ev.get('jurisdiction') or 'not available'}.", BODY),
          Paragraph("Source attribution", H)] + [_p(x, BODY) for x in ev.get("sources", [])] + [_p("OpenStreetMap exposure layers: not used in this build.", BODY)]
    dec, notes = store.decisions(case_id), store.notes(case_id)
    s.append(Paragraph("Reviewer decisions and notes", H))
    s += [_p(f"{d['decided_at']} {d['reviewer']}: {d['decision']} - {d['note']}", BODY) for d in dec] or [_p("None recorded.", BODY)]
    s += [_p(f"{n['ts']} {n['reviewer']}: {n['note']}", BODY) for n in notes]
    s += [Paragraph("Audit trail", H), _table([["Time (UTC)", "Actor", "Action", "Detail"]] + [[a["ts"], a["actor"], a["action"], a["detail"]] for a in store.audit(case_id)], [40 * mm, 30 * mm, 40 * mm, 60 * mm])]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(out_path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                      title=f"Change Sentinel case {case_id}").build(s)
    store.log(case_id, "system", "pdf_generated", out_path.name)
    return out_path
