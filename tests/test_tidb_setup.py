"""TiDB 준비물 점검: 계정 SQL 규칙 (TiDB·네트워크 없이). 공개 저장소에는 복사 스크립트를 넣지 않아 그 테스트는 뺐다."""
import re
import sqlite3
from pathlib import Path

from kleague.db import Database
from kleague.schema_docs import ALLOWED_TABLES

ROOT = Path(__file__).resolve().parents[1]
SETUP = (ROOT / "db" / "tidb_setup.sql").read_text(encoding="utf-8")
BODY = "\n".join(l for l in SETUP.splitlines() if not l.strip().startswith("--"))


def _grants(user):
    return re.findall(r"GRANT (.+?) ON (\S+)\s+TO 'PREFIX\.%s'@'%%'" % user, BODY)


def test_user_names_use_starter_prefix_and_placeholder_passwords():
    users = re.findall(r"CREATE USER IF NOT EXISTS '([^']+)'", BODY)
    assert users == ["PREFIX.kl_collector", "PREFIX.kl_query_ro", "PREFIX.kl_app_rw"]
    assert re.findall(r"IDENTIFIED BY '([^']*)'", BODY) == ["CHANGE_ME"] * 3
    assert "FLUSH PRIVILEGES" not in BODY


def test_grants_stay_inside_kleague():
    targets = re.findall(r"GRANT .+? ON (\S+)\s+TO", BODY)
    assert targets and all(t.startswith("kleague.") for t in targets)
    assert not re.search(r"\b(DELETE|DROP|ALL|SUPER|GRANT OPTION)\b", " ".join(re.findall(r"GRANT (.+?) ON", BODY)))


def test_query_account_is_select_only_on_data_tables():
    g = _grants("kl_query_ro")
    assert {p for p, _ in g} == {"SELECT"}
    assert {t.split(".", 1)[1] for _, t in g} == set(ALLOWED_TABLES)
    assert not any("app_" in t for _, t in g)


def test_app_account_only_app_tables():
    g = _grants("kl_app_rw")
    assert {t for _, t in g} == {"kleague.app_sql_example", "kleague.app_query_log", "kleague.app_usage"}


def test_collector_can_run_migration():
    (privs, target), = _grants("kl_collector")
    assert target == "kleague.*" and "ALTER" in privs and "INSERT" in privs and "UPDATE" in privs

