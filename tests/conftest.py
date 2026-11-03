import pytest
from cs.config import Settings


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    monkeypatch.setenv("CS_MODE", "sample")


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=tmp_path / "t.db", output_dir=tmp_path / "out")


def write_empty_manifest(d):
    """Hermetic registry for tests: the shipped manifest's pilot block, with NO district/waterbody records."""
    import json
    from cs import registry as R
    m = json.loads((R.DEFAULT_DIR / "manifest.json").read_text())
    m["districts"], m["waterbodies"] = [], []
    (d / "manifest.json").write_text(json.dumps(m))
