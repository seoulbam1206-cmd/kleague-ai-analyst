import pytest

from kleague.db import QueryTimeout


def test_select_works_and_caps_rows(ro_db):
    r = ro_db.run_select("SELECT * FROM game_player_stat", max_rows=50, timeout_ms=5000)
    assert len(r.df) == 50 and r.truncated


def test_write_is_blocked_at_db_layer(ro_db):
    # 가드가 뚫려도 DB 계층에서 한 번 더 막힌다 (심층 방어)
    for sql in ["DELETE FROM game", "DROP TABLE game", "INSERT INTO club (team_id) VALUES ('X')",
                "ATTACH DATABASE '/tmp/x.db' AS x", "PRAGMA writable_schema = 1"]:
        with pytest.raises(Exception):
            ro_db.run_select(sql, 10, 1000)


def test_sqlite_master_hidden(ro_db):
    with pytest.raises(Exception):
        ro_db.run_select("SELECT * FROM sqlite_master", 10, 1000)


def test_timeout(ro_db):
    heavy = ("WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) "
             "SELECT COUNT(*) FROM r")
    with pytest.raises(QueryTimeout):
        ro_db.run_select(heavy, 10, 300)


def test_execute_refused_on_readonly(ro_db):
    with pytest.raises(PermissionError):
        ro_db.execute("DELETE FROM game")
