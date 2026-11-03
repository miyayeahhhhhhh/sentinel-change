import os
from cs.integrations.emailer import send_case_alert
from cs.integrations.neo4j_sync import sync_case
from cs.integrations.sarvam import translate_text
from cs.evidence.dynamic_world import DynamicWorldSource
from cs.evidence.jrc_gsw import JRCSurfaceWaterSource


def test_external_integrations_disabled_without_credentials(monkeypatch):
    for key in ("CS_SMTP_HOST","CS_SMTP_FROM","CS_ALERT_TO","CS_NEO4J_URI","CS_NEO4J_USER","CS_NEO4J_PASSWORD","SARVAM_API_KEY","CS_GEE_PROJECT"):
        monkeypatch.delenv(key, raising=False)
    ok, msg = send_case_alert({"case_id":"ENC-0001"})
    assert not ok and "not configured" in msg.lower()
    ok, msg = sync_case({"case_id":"ENC-0001"})
    assert not ok and "not configured" in msg.lower()
    try: translate_text("test")
    except RuntimeError as exc: assert "SARVAM_API_KEY" in str(exc)
    else: assert False
    assert DynamicWorldSource().status == "blocked_credentials"
    assert JRCSurfaceWaterSource().status == "implemented_untested_remote"     # never "operational" without a recorded real run


def test_dynamic_world_requirements_name_the_credentials():
    from cs.evidence.dynamic_world import DynamicWorldSource
    assert "CS_GEE_PROJECT" in DynamicWorldSource().requirements
