from streamlit.testing.v1 import AppTest


def _run(page, monkeypatch, tmp_path):
    monkeypatch.setenv("CS_DB_PATH", str(tmp_path / "a.db")); monkeypatch.setenv("CS_OUTPUT_DIR", str(tmp_path / "o"))
    at = AppTest.from_file("../app.py", default_timeout=120).run()
    at.sidebar.radio[0].set_value(page).run()
    return at


def test_workspaces_run_without_exception(monkeypatch, tmp_path):
    for page, label in (("Flood workspace", "Run analysis"), ("Encroachment workspace", "Run analysis")):
        at = _run(page, monkeypatch, tmp_path)
        assert not at.exception
        [b for b in at.button if b.label == label][0].click().run()
        assert not at.exception, at.exception
    at = _run("Review queues", monkeypatch, tmp_path); assert not at.exception
    at = _run("Methods and status", monkeypatch, tmp_path); assert not at.exception


def test_reviewer_queue_ui_flow(monkeypatch, tmp_path):
    monkeypatch.setenv("CS_DB_PATH", str(tmp_path / "a.db"))
    monkeypatch.setenv("CS_OUTPUT_DIR", str(tmp_path / "o"))
    at = AppTest.from_file("../app.py", default_timeout=120).run()
    
    # 1. Run Flood Analysis
    at.sidebar.radio[0].set_value("Flood workspace").run()
    [b for b in at.button if b.label == "Run analysis"][0].click().run()
    assert not at.exception

    # 2. Run Encroachment Analysis
    at.sidebar.radio[0].set_value("Encroachment workspace").run()
    [b for b in at.button if b.label == "Run analysis"][0].click().run()
    assert not at.exception

    # 3. Open Review queues
    at.sidebar.radio[0].set_value("Review queues").run()
    assert not at.exception
    assert len(at.dataframe) > 0

    # 4. Check Methods and status page
    at.sidebar.radio[0].set_value("Methods and status").run()
    assert not at.exception
