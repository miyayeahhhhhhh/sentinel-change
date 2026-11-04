import pytest

pytest.importorskip('streamlit')
from streamlit.testing.v1 import AppTest


def _run(page, monkeypatch, tmp_path):
    monkeypatch.setenv("CS_DB_PATH", str(tmp_path / "a.db")); monkeypatch.setenv("CS_OUTPUT_DIR", str(tmp_path / "o"))
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    for key in ("CS_NEO4J_URI", "CS_NEO4J_USER", "CS_NEO4J_PASSWORD", "CS_SMTP_HOST", "CS_SMTP_FROM", "CS_ALERT_TO"):
        monkeypatch.delenv(key, raising=False)
    at = AppTest.from_file("../app.py", default_timeout=120).run()
    at.sidebar.radio[0].set_value(page).run()
    if page == "Analysis workspace":
        at.radio(key="target").set_value("Fixed grid (legacy / synthetic)").run()
    return at


def test_workspaces_run_without_exception(monkeypatch, tmp_path):
    at = _run("Analysis workspace", monkeypatch, tmp_path)
    assert not at.exception
    run = [b for b in at.button if b.label == "Run live analysis"][0]
    assert run.disabled                    # Sample mode cannot create a synthetic case from the live-run button.
    at = _run("Pilot registry", monkeypatch, tmp_path); assert not at.exception
    # registry content is the user's; the page only has to render either way
    at = _run("Dashboard", monkeypatch, tmp_path); assert not at.exception
    at = _run("Case management", monkeypatch, tmp_path); assert not at.exception
    at = _run("Methods and readiness", monkeypatch, tmp_path); assert not at.exception


def test_reviewer_queue_ui_flow(monkeypatch, tmp_path):
    monkeypatch.setenv("CS_DB_PATH", str(tmp_path / "a.db"))
    monkeypatch.setenv("CS_OUTPUT_DIR", str(tmp_path / "o"))
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    for key in ("CS_NEO4J_URI", "CS_NEO4J_USER", "CS_NEO4J_PASSWORD", "CS_SMTP_HOST", "CS_SMTP_FROM", "CS_ALERT_TO"):
        monkeypatch.delenv(key, raising=False)
    from cs.config import Settings
    from cs.workflow import execute_encroachment
    execute_encroachment(Settings.from_env(), "persistent_loss")
    at = AppTest.from_file("../app.py", default_timeout=120).run()
    
    # 2. Sample cases for the queue are created in this isolated test database.
    at.sidebar.radio[0].set_value("Analysis workspace").run()
    at.radio(key="target").set_value("Fixed grid (legacy / synthetic)").run()
    assert [b for b in at.button if b.label == "Run live analysis"][0].disabled

    # 3. Open Review queues
    at.sidebar.radio[0].set_value("Case management").run()
    assert not at.exception
    at.radio(key="queue_scope").set_value("synthetic").run()     # default scope is the real pilot; the fixture case is synthetic
    assert not at.exception and len(at.dataframe) > 0
    at.radio(key="queue_scope").set_value("pilot").run()
    assert not at.exception

    # 4. Check Methods and status page
    at.sidebar.radio[0].set_value("Methods and readiness").run()
    assert not at.exception
