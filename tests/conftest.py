import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def demo_path(tmp_path_factory) -> Path:
    from scripts.make_demo_db import build

    path = tmp_path_factory.mktemp("db") / "demo.sqlite"
    build(path)
    return path


@pytest.fixture()
def ro_db(demo_path):
    from kleague.db import Database

    return Database(f"sqlite:///{demo_path}", read_only=True)


@pytest.fixture()
def app_store(tmp_path):
    from kleague.db import Database
    from kleague.store import AppStore

    s = AppStore(Database(f"sqlite:///{tmp_path / 'app.sqlite'}", read_only=False))
    s.init_schema()
    return s
