"""퇴단 선수(is_active)·경고2회 퇴장(second_yellow_cards)·관대 채점 (네트워크·LLM 없음).

공개 저장소용: 수집기(collector) 테스트는 빼고, 앱·프롬프트·채점 쪽만 남겼다."""
import sys
from pathlib import Path

import pandas as pd

from kleague.db import Database
from kleague.entities import EntityResolver
from kleague.schema_docs import schema_prompt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
from run_eval import results_match  # noqa: E402


def _init_schema(d: Database) -> None:
    """db/ 의 스키마와 새 컬럼 추가 스크립트로 빈 DB 를 만든다."""
    d.run_script((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
    d.run_script((ROOT / "db" / "migrate.sql").read_text(encoding="utf-8"))



# ------------------------------------------------- 퇴단 선수
def test_departed_player_hint_and_nationality(tmp_path):
    d = Database(f"sqlite:///{tmp_path / 'e.sqlite'}", read_only=False)
    _init_schema(d)
    d.upsert("club", ["team_id"], [{"team_id": "K09", "team_name": "FC서울", "team_s_name": "서울",
                                    "league_name": "K리그1"}])
    d.upsert("player", ["player_id"], [
        {"player_id": "A", "name": "현역선수", "role_name": "선수", "nationality": "한국", "position": "FW", "is_active": 1},
        {"player_id": "B", "name": "떠난선수", "role_name": "선수", "nationality": "브라질", "position": "DF", "is_active": 0},
        {"player_id": "C", "name": "옛날선수", "role_name": "선수", "nationality": "한국", "position": "MF", "is_active": 0},
    ])
    d.upsert("game_player_stat", ["meet_year", "meet_seq", "game_id", "player_id"], [
        {"meet_year": 2026, "meet_seq": 1, "game_id": 1, "team_id": "K09", "player_id": "A", "player_name": "현역선수", "goals": 2, "played": 1},
        {"meet_year": 2026, "meet_seq": 1, "game_id": 1, "team_id": "K09", "player_id": "B", "player_name": "떠난선수", "goals": 1, "played": 1},
    ])
    ro = Database(f"sqlite:///{tmp_path / 'e.sqlite'}", read_only=True)
    r = EntityResolver(ro)
    assert "player_id = 'B' (퇴단 선수)" in r.hints("떠난선수 득점")
    assert "player_id = 'A'" in r.hints("현역선수 득점") and "퇴단" not in r.hints("현역선수 득점")
    assert r.hints("옛날선수 기록") == ""  # 2025~2026 경기에 없는 옛 퇴단 선수는 힌트 대상 아님
    # 국적·포지션 JOIN 이 현역/퇴단 모두 된다
    rows = ro.query("""SELECT p.nationality, p.position, SUM(s.goals) FROM game_player_stat s
                       JOIN player p ON p.player_id = s.player_id GROUP BY p.nationality, p.position ORDER BY 1""")
    assert rows == [("브라질", "DF", 1), ("한국", "FW", 2)]


def test_migrate_adds_columns_to_old_db(tmp_path):
    d = Database(f"sqlite:///{tmp_path / 'old.sqlite'}", read_only=False)
    d.run_script("CREATE TABLE player (player_id VARCHAR(8) PRIMARY KEY, name VARCHAR(20));"
                 "CREATE TABLE game_team_stat (meet_year INT, meet_seq INT, game_id INT, team_id VARCHAR(3), red_cards INT);"
                 "CREATE TABLE game_player_stat (meet_year INT, meet_seq INT, game_id INT, player_id VARCHAR(8), red_cards INT);")
    d.execute("INSERT INTO player VALUES ('X', '기존')")
    for _ in range(2):  # 두 번 실행해도 안전
        d.run_script((ROOT / "db" / "migrate.sql").read_text(encoding="utf-8"))
    assert d.query("SELECT name, is_active FROM player") == [("기존", None)]  # 데이터 그대로
    d.query("SELECT second_yellow_cards FROM game_team_stat")


def test_schema_prompt_red_card_rule():
    p = schema_prompt()
    assert "SUM(red_cards) + SUM(second_yellow_cards)" in p
    assert "'레드카드', '직접 퇴장', '다이렉트 퇴장'" in p and "red_cards 만" in p
    assert "is_active" in p and "K리그를 떠난 선수는 없으므로" not in p


# ------------------------------------------------- 관대 채점
def _df(v):
    return pd.DataFrame({"x": v})


def test_lenient_score_only_forgives_rounding_and_percent():
    g = _df([0.556])
    assert not results_match(g, _df([55.56]), False)                 # 엄격: 오답
    assert results_match(g, _df([55.56]), False, lenient=True)       # 비율 vs 퍼센트
    assert results_match(g, _df([0.5556]), False, lenient=True)      # 반올림 0.01 이내
    assert not results_match(_df([12.44]), _df([12.0]), False, lenient=True)   # 0.44 차이는 오답
    assert not results_match(_df([12048.0]), _df([12048.35]), False, lenient=True)
    assert not results_match(_df([0.556]), _df([0.6]), False, lenient=True)
    assert results_match(_df([3.0, 1.5]), _df([1.505, 3.001]), False, lenient=True)  # 순서 무관


# ------------------------------------------------- 퇴장 내역 표시
def test_red_card_breakdown_rule_in_prompts():
    from kleague.text2sql import SUMMARY_SYSTEM
    p = schema_prompt()
    assert "'퇴장', '직접퇴장', '경고2회퇴장'" in p
    assert "퇴장 36회 (직접 퇴장 25 + 경고 2회 퇴장 11)" in SUMMARY_SYSTEM
    assert "내역을 지어내지 않는다" in SUMMARY_SYSTEM


def test_red_card_examples_show_breakdown():
    import yaml
    seeds = yaml.safe_load((ROOT / "eval" / "seed_examples.yaml").read_text(encoding="utf-8"))
    combined = [e for e in seeds if "second_yellow_cards" in e["sql"] and "red_cards" in e["sql"]]
    assert len(combined) >= 2
    for e in combined:
        assert "AS 퇴장" in e["sql"] and "AS 직접퇴장" in e["sql"] and "AS 경고2회퇴장" in e["sql"], e["question"]


def test_summary_gets_breakdown_table(tmp_path):
    from kleague.llm import FakeLLM
    from kleague.text2sql import SUMMARY_SYSTEM, Text2SQL
    d = Database(f"sqlite:///{tmp_path / 's.sqlite'}", read_only=False)
    _init_schema(d)
    key = {"meet_year": 2026, "meet_seq": 1, "game_id": 1}
    d.upsert("game_team_stat", ["meet_year", "meet_seq", "game_id", "team_id"],
             [{**key, "team_id": "K09", "red_cards": 2, "second_yellow_cards": 1}])
    ro = Database(f"sqlite:///{tmp_path / 's.sqlite'}", read_only=True)
    sql = ("SELECT SUM(red_cards) + SUM(second_yellow_cards) AS 퇴장, SUM(red_cards) AS 직접퇴장, "
           "SUM(second_yellow_cards) AS 경고2회퇴장 FROM game_team_stat")
    llm = FakeLLM(['{"sql": "%s"}' % sql, "퇴장 3회 (직접 퇴장 2 + 경고 2회 퇴장 1)"])
    a = Text2SQL(ro, llm, summarize=True).ask("2026 퇴장 몇 번?")
    assert a.status == "ok" and a.df.iloc[0].tolist() == [3, 2, 1]
    system, user = llm.calls[1]
    assert system == SUMMARY_SYSTEM and "직접퇴장" in user and "경고2회퇴장" in user
    assert a.summary == "퇴장 3회 (직접 퇴장 2 + 경고 2회 퇴장 1)"
