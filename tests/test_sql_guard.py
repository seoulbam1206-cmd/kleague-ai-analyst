import pytest

from kleague.schema_docs import ALLOWED_TABLES
from kleague.sql_guard import check_sql

MAX = 500


def guard(sql, dialect="mysql"):
    return check_sql(sql, ALLOWED_TABLES, dialect, MAX)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM game",
    "SELECT team_id, SUM(goals_for) FROM game_team_stat GROUP BY team_id ORDER BY 2 DESC LIMIT 5",
    "WITH s AS (SELECT player_id, SUM(goals) g FROM game_player_stat GROUP BY player_id) SELECT * FROM s",
    "SELECT name FROM player UNION SELECT team_name FROM club",
    "SELECT '나는 DROP TABLE 이라는 글자' AS 문자열 FROM club",   # 문자열 안 금지어는 오탐 없음
    "SELECT p.name FROM player p JOIN (SELECT player_id FROM game_player_stat) x ON x.player_id = p.player_id",
])
def test_allows_safe_queries(sql):
    r = guard(sql)
    assert r.ok, r.reason


@pytest.mark.parametrize("sql, why", [
    ("DROP TABLE game", "DDL"),
    ("DELETE FROM game", "DML"),
    ("UPDATE club SET team_name = 'x'", "DML"),
    ("INSERT INTO club (team_id) VALUES ('X')", "DML"),
    ("SELECT 1 FROM game; DROP TABLE game", "다중 문장"),
    ("SELECT * FROM game; SELECT * FROM club", "다중 문장"),
    ("SELECT * INTO OUTFILE '/tmp/x' FROM game", "INTO OUTFILE"),
    ("SELECT * FROM information_schema.tables", "시스템 스키마"),
    ("SELECT * FROM mysql.user", "시스템 스키마"),
    ("SELECT * FROM app_query_log", "앱 테이블 접근"),
    ("SELECT * FROM users", "화이트리스트 밖"),
    ("SELECT SLEEP(10) FROM game", "지연 함수"),
    ("SELECT BENCHMARK(1000000, MD5('a'))", "부하 함수"),
    ("SELECT LOAD_FILE('/etc/passwd')", "파일 읽기"),
    ("SELECT * FROM game FOR UPDATE", "잠금"),
    ("SELECT * FROM (SELECT * FROM kleague.app_usage) x", "서브쿼리 우회"),
    ("WITH x AS (SELECT * FROM app_sql_example) SELECT * FROM x", "CTE 우회"),
    ("", "빈 SQL"),
])
def test_blocks_dangerous(sql, why):
    assert not guard(sql).ok, why


def test_adds_limit_when_missing():
    r = guard("SELECT * FROM game")
    assert r.ok and r.limit_applied and "LIMIT 500" in r.sql


def test_caps_large_limit():
    r = guard("SELECT * FROM game LIMIT 100000")
    assert r.ok and "LIMIT 500" in r.sql and "100000" not in r.sql


def test_keeps_small_limit():
    r = guard("SELECT * FROM game LIMIT 10")
    assert r.ok and "LIMIT 10" in r.sql and not r.limit_applied


def test_union_gets_limit():
    r = guard("SELECT name FROM player UNION SELECT team_name FROM club")
    assert r.ok and "LIMIT 500" in r.sql


def test_sqlite_dialect():
    r = guard("SELECT * FROM game LIMIT 3", dialect="sqlite")
    assert r.ok


def test_comment_tricks():
    assert not guard("SELECT 1 FROM game /* ; */ ; DROP TABLE game").ok
    assert guard("SELECT 1 FROM game -- ; DROP TABLE game").ok  # 주석 안 내용은 실행되지 않음


# ---------------------------------------------------------------------------
# 서버·세션 정보 함수와 변수 (2026-11-05 공격 시험에서 VERSION()·DATABASE()·@@hostname 이 통과했던 구멍)
# ---------------------------------------------------------------------------
INFO_SQL = [
    "SELECT VERSION()", "SELECT version()", "SELECT DATABASE()", "SELECT SCHEMA()",
    "SELECT USER()", "SELECT CURRENT_USER()", "SELECT CURRENT_USER", "SELECT SESSION_USER()",
    "SELECT SYSTEM_USER()", "SELECT CONNECTION_ID()", "SELECT CURRENT_ROLE()", "SELECT LAST_INSERT_ID()",
    "SELECT sqlite_version()", "SELECT sqlite_source_id()", "SELECT last_insert_rowid()",
    "SELECT changes()", "SELECT total_changes()", "SELECT sqlite_compileoption_get(0)",
    "SELECT tidb_version()", "SELECT tidb_current_tso()",
    "SELECT team_id, VERSION() AS v FROM club",                       # 정상 컬럼 사이에 숨김
    "SELECT * FROM club WHERE team_s_name = DATABASE()",              # WHERE 안
    "SELECT (SELECT VERSION()) AS v FROM club",                       # 서브쿼리 안
    "WITH v AS (SELECT CURRENT_USER() AS u) SELECT * FROM v",         # CTE 안
    "SELECT CONCAT('v=', VERSION()) FROM club",                       # 다른 함수 인자로
]
VAR_SQL = [
    "SELECT @@version", "SELECT @@hostname", "SELECT @@global.hostname", "SELECT @@session.time_zone",
    "SELECT @x", "SELECT @x := 1", "SELECT team_id FROM club WHERE team_id = @t",
    "SELECT team_id FROM club WHERE team_id = ?", "SELECT team_id FROM club WHERE team_id = :t",
    "SELECT team_id, @@port FROM club",
]


@pytest.mark.parametrize("dialect", ["mysql", "sqlite"])
@pytest.mark.parametrize("sql", INFO_SQL + VAR_SQL)
def test_blocks_server_info_and_variables(sql, dialect):
    r = guard(sql, dialect)
    assert not r.ok, f"{dialect}: {sql} 가 통과함 → {r.sql}"


def test_renamed_functions_are_caught():
    """sqlglot 이 VERSION → CURRENT_VERSION, DATABASE → CURRENT_SCHEMA 로 바꿔 읽어도 막힌다."""
    assert "서버·세션 정보" in guard("SELECT VERSION()", "mysql").reason
    assert "서버·세션 정보" in guard("SELECT DATABASE()", "mysql").reason
    assert "서버·세션 정보" in guard("SELECT sqlite_version()", "sqlite").reason
    assert "변수" in guard("SELECT @@hostname", "mysql").reason


# 정상 질문에 쓰는 집계·날짜·문자열·조건 함수는 그대로 통과해야 한다 (좁히는 수정이 과하지 않은지)
NORMAL_SQL = {
    "mysql": [
        "SELECT team_id, COUNT(*), SUM(goals_for), AVG(goals_for), MAX(goals_for), MIN(goals_for) FROM game_team_stat GROUP BY team_id",
        "SELECT ROUND(AVG(home_goal + away_goal), 2) FROM game WHERE is_finished = 1",
        "SELECT DATE_FORMAT(game_date, '%Y-%m'), COUNT(*) FROM game GROUP BY 1",
        "SELECT YEAR(game_date), MONTH(game_date), DAYOFWEEK(game_date), WEEKDAY(game_date) FROM game",
        "SELECT CURRENT_DATE, CURDATE(), NOW(), CURRENT_TIMESTAMP FROM game LIMIT 1",
        "SELECT * FROM game WHERE game_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)",
        "SELECT DATEDIFF(MAX(game_date), MIN(game_date)) FROM game",
        "SELECT CONCAT(home_goal, '-', away_goal), CAST(home_goal AS CHAR) FROM game",
        "SELECT GROUP_CONCAT(DISTINCT team_s_name ORDER BY team_s_name SEPARATOR ', ') FROM club",
        "SELECT SUBSTRING(player_name, 1, 2), LENGTH(player_name), UPPER(player_name), TRIM(player_name) FROM player",
        "SELECT COALESCE(audience, 0), IFNULL(audience, 0), NULLIF(audience, 0) FROM game",
        "SELECT CASE WHEN home_goal > away_goal THEN '승' ELSE '무패' END, IF(home_goal > away_goal, 1, 0) FROM game",
        "SELECT player_id, RANK() OVER (ORDER BY SUM(goals) DESC), ROW_NUMBER() OVER (PARTITION BY team_id ORDER BY player_id) FROM game_player_stat GROUP BY player_id, team_id",
        "SELECT ABS(home_goal - away_goal), GREATEST(home_goal, away_goal), LEAST(home_goal, away_goal) FROM game",
        "SELECT SUM(goals) * 1.0 / NULLIF(SUM(minutes), 0) * 90 FROM game_player_stat",
        "SELECT * FROM player WHERE player_name LIKE '%이%'",
    ],
    "sqlite": [
        "SELECT strftime('%Y-%m', game_date), COUNT(*) FROM game GROUP BY 1",
        "SELECT strftime('%w', game_date), date('now'), datetime('now'), julianday(game_date) FROM game",
        "SELECT home_goal || '-' || away_goal, CAST(home_goal AS TEXT) FROM game",
        "SELECT GROUP_CONCAT(DISTINCT team_s_name) FROM club",
        "SELECT substr(player_name, 1, 2), length(player_name), instr(player_name, '이') FROM player",
        "SELECT ROUND(AVG(home_goal + away_goal), 2), TOTAL(audience) FROM game",
        "SELECT IFNULL(audience, 0), COALESCE(audience, 0), IIF(home_goal > away_goal, 1, 0) FROM game",
        "SELECT player_id, DENSE_RANK() OVER (ORDER BY SUM(goals) DESC) FROM game_player_stat GROUP BY player_id",
        "SELECT MAX(home_goal, away_goal), MIN(home_goal, away_goal) FROM game",
        "SELECT CURRENT_DATE, CURRENT_TIMESTAMP FROM game LIMIT 1",
    ],
}


@pytest.mark.parametrize("dialect, sql", [(d, s) for d, qs in NORMAL_SQL.items() for s in qs])
def test_normal_functions_still_allowed(dialect, sql):
    r = guard(sql, dialect)
    assert r.ok, f"{dialect}: {sql} → {r.reason}"


def test_column_named_like_info_function_is_ok():
    """함수가 아니라 컬럼·별칭 이름이 version/user 인 것은 막지 않는다."""
    assert guard("SELECT team_id AS version, team_s_name AS user FROM club").ok


# ---------------------------------------------------------------------------
# 함수 허용 목록 (기록만 하는 방식): 목록 밖 함수는 막지 않고 로그·unknown_funcs 에만 남긴다
# ---------------------------------------------------------------------------
def test_unknown_function_is_logged_not_blocked(caplog):
    caplog.set_level("WARNING", logger="kleague.sql_guard")
    r = guard("SELECT SOUNDEX(player_name), COUNT(*) FROM player GROUP BY 1")
    assert r.ok                                   # 차단하지 않음 (기존 동작 그대로)
    assert r.unknown_funcs == ("SOUNDEX",)
    assert any("SOUNDEX" in m and "기록만" in m for m in caplog.messages)


def test_known_functions_not_logged(caplog):
    caplog.set_level("WARNING", logger="kleague.sql_guard")
    for dialect, qs in NORMAL_SQL.items():
        for q in qs:
            r = guard(q, dialect)
            assert r.ok and r.unknown_funcs == (), f"{dialect}: {q} → {r.unknown_funcs}"
    assert not caplog.messages


def test_allowlist_does_not_change_verdict_or_sql():
    """허용 목록은 판정·SQL 을 바꾸지 않는다: 막히던 것은 그대로 막히고, 통과하던 SQL 문자열도 같다."""
    assert not guard("SELECT SLEEP(10) FROM game").ok
    assert not guard("SELECT VERSION()").ok
    r = guard("SELECT SOUNDEX(player_name) FROM player")
    assert r.ok and r.sql == "SELECT SOUNDEX(player_name) FROM player LIMIT 500"


def test_blocked_sql_has_no_unknown_funcs_record():
    assert guard("SELECT SLEEP(1)").unknown_funcs == ()


def test_saved_sql_all_inside_allowlist():
    """정답·예시 SQL 이 쓰는 함수는 모두 목록 안이어야 한다 (목록이 너무 좁지 않은지)."""
    import yaml
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    sqls = [c["sql"] for c in yaml.safe_load((root / "eval/questions.yaml").read_text(encoding="utf-8"))]
    sqls += [e["sql"] for e in yaml.safe_load((root / "eval/seed_examples.yaml").read_text(encoding="utf-8"))]
    outside = {f for q in sqls for f in guard(q, "sqlite").unknown_funcs}
    assert outside == set(), outside
