import sys
from pathlib import Path

import pytest

# Tests import the package from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Keep every test out of the real ``data/`` directory.

    ``paths.data_dir()`` defaults to ``data/`` beside the repository, and some
    tests build a real ``ConfigStore()`` or ``MainWindow``.  Those save on
    teardown, so ``ConfigStore()`` alone is enough to write to the developer's
    own config - a plain pytest run was observed changing the engine, the
    translation platform and the remembered target window in it.

    Redirecting the directory the same way ``test_shutdown`` does for its child
    process makes a test run read-only with respect to the real install.  This
    is read from the environment on every call, so setting it here is enough.
    """
    monkeypatch.setenv("PRTSBOX_DATA_DIR", str(tmp_path / "data"))
