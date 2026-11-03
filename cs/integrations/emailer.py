from __future__ import annotations
import os, smtplib, ssl
from email.message import EmailMessage
from pathlib import Path


def send_case_alert(case: dict, pdf_path: str | None = None, recipient: str | None = None,
                    extra_attachments: list[str] | None = None) -> tuple[bool, str]:
    host, sender = (os.getenv(k, "").strip() for k in ("CS_SMTP_HOST", "CS_SMTP_FROM"))
    recipient = (recipient or os.getenv("CS_ALERT_TO", "")).strip()
    if not all((host, sender, recipient)):
        return False, "Email not configured (set CS_SMTP_HOST, CS_SMTP_FROM and a recipient)."
    msg = EmailMessage()
    msg["Subject"] = f"Change Sentinel analysis report: {case.get('case_id','case')}"
    msg["From"], msg["To"] = sender, recipient
    translated = bool(extra_attachments)
    msg.set_content(("NOTE: the attached native-language report is MACHINE-TRANSLATED (Sarvam AI); the English PDF is authoritative.\n\n" if translated else "") +
        "A Change Sentinel analysis report is attached for administrative review.\n\n"
        f"Case: {case.get('case_id')}\nState: {case.get('state')}\nStatus: {case.get('status')}\n"
        f"Summary: {case.get('summary')}\n\nThis is an automated screening indicator, not a finding of illegality. "
        "Please verify against source imagery and field evidence before action.")
    attachments = [pdf_path, *(extra_attachments or [])]
    for item in attachments:
        if item and Path(item).is_file():
            p = Path(item)
            subtype = "pdf" if p.suffix.lower() == ".pdf" else "vnd.openxmlformats-officedocument.wordprocessingml.document"
            maintype = "application"
            msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)
    port = int(os.getenv("CS_SMTP_PORT", "587")); user = os.getenv("CS_SMTP_USER", ""); password = os.getenv("CS_SMTP_PASSWORD", "")
    try:
        with smtplib.SMTP(host, port, timeout=25) as server:
            server.ehlo(); server.starttls(context=ssl.create_default_context()); server.ehlo()
            if user: server.login(user, password)
            server.send_message(msg)
        return True, f"Report email sent to {recipient}."
    except Exception as exc:
        return False, f"Email delivery failed: {type(exc).__name__}: {exc}"


def guarded_send(store, case: dict, actor: str, recipient: str, confirmed: bool, pdf_path: str | None,
                 extra_attachments: list[str] | None = None, sender=send_case_alert) -> tuple[bool, str]:
    """The only path the UI uses to e-mail a case. The recipient is whatever the user typed (never defaulted); synthetic,
    legacy, unassessed, no-change or never-reviewed cases are blocked; an explicit confirmation is required; every
    attempt (blocked, failed or sent) is written to the audit log."""
    from ..scope import email_block_reasons
    cid = case["case_id"]
    recipient = (recipient or "").strip()
    reasons = email_block_reasons(case, store.decisions(cid))
    if reasons:
        msg = "Sending blocked: " + "; ".join(reasons) + "."
    elif not recipient or "@" not in recipient:
        msg = "Sending blocked: type the officer's e-mail address (no default is ever filled in)."
    elif not confirmed:
        msg = "Sending blocked: tick the confirmation that you have reviewed the report and the recipient."
    else:
        ok, msg = sender(case, pdf_path, recipient, extra_attachments)
        store.log(cid, actor, "report_email_sent" if ok else "report_email_failed", f"{recipient} | {msg}")
        return ok, msg
    store.log(cid, actor, "report_email_blocked", f"{recipient or '(no recipient)'} | {msg}")
    return False, msg
