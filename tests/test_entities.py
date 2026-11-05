"""이름 → 코드 힌트, 스키마 설명 규칙, 평가·예시 파일 점검 (네트워크·LLM 없음)."""
from pathlib import Path

import pytest
import yaml

from kleague.db import Database
from kleague.entities import EntityResolver
from kleague.schema_docs import schema_prompt

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def tiny_db(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 't.sqlite'}", read_only=False)
    db.run_script((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
    db.upsert("club", ["team_id"], [
        {"team_id": "K09", "team_name": "FC서울", "team_s_name": "서울", "league_name": "K리그1"},
        {"team_id": "K31", "team_name": "서울 이랜드", "team_s_name": "서울E", "league_name": "K리그2"},
        {"team_id": "K10", "team_name": "대전 하나 시티즌", "team_s_name": "대전", "league_name": "K리그1"},
        {"team_id": "K02", "team_name": "수원 삼성", "team_s_name": "수원", "league_name": "K리그2"},
        {"team_id": "K29", "team_name": "수원FC", "team_s_name": "수원FC", "league_name": "K리그2"},
        {"team_id": "K34", "team_name": "충남 아산 FC", "team_s_name": "충남아산", "league_name": "K리그2"},
        {"team_id": "K33", "team_name": "아산 무궁화", "team_s_name": "아산", "league_name": None},  # 과거 구단
    ])
    db.upsert("player", ["player_id"], [
        {"player_id": "P1", "name": "가상선수무", "team_id": "K09", "role_name": "선수"},
    ])
    # 리그를 떠난 선수: player 에는 없고 경기 기록에만 있음 (마지막 소속은 K10)
    db.upsert("game_player_stat", ["meet_year", "meet_seq", "game_id", "player_id"], [
        {"meet_year": 2025, "meet_seq": 1, "game_id": 1, "team_id": "K09", "player_id": "P9", "player_name": "임꺽정"},
        {"meet_year": 2026, "meet_seq": 1, "game_id": 5, "team_id": "K10", "player_id": "P9", "player_name": "임꺽정"},
    ])
    return Database(f"sqlite:///{tmp_path / 't.sqlite'}", read_only=True)


def test_same_prefix_names_both_found(tiny_db):
    r = EntityResolver(tiny_db)
    h = r.hints("서울E와 서울 상대전적")
    assert "team_id = 'K31'" in h and "team_id = 'K09'" in h
    h = r.hints("수원FC 대 수원")
    assert "team_id = 'K29'" in h and "team_id = 'K02'" in h


def test_common_word_not_team(tiny_db):
    r = EntityResolver(tiny_db)
    assert "K10" not in r.hints("서울 상대전적 알려줘")   # '상대전적' 안의 '대전'
    assert "K10" in r.hints("대전 상대전적 알려줘")


def test_historical_club_alias_skipped(tiny_db):
    h = EntityResolver(tiny_db).hints("아산 2025 성적")
    assert "K34" in h and "K33" not in h


def test_player_only_in_game_records(tiny_db):
    r = EntityResolver(tiny_db)
    assert "player_id = 'P1'" in r.hints("가상선수무 득점")
    assert r.players["임꺽정"] == [("P9", "K10")]  # 마지막 경기 소속
    assert "player_id = 'P9'" in r.hints("임꺽정 2025 득점")


def test_schema_prompt_has_new_rules():
    p = schema_prompt()
    assert "is_finished = 1" in p and "[종료 경기만]" in p
    assert "[자책골]" in p and "is_own_goal" in p
    assert "파이널A" in p and "Play Off" in p and "SY=경고2회 퇴장" in p and "second_yellow_cards" in p
    assert "GROUP BY 해 SUM" in p  # 이적 선수: player_id 로 묶기


def test_eval_files_consistent():
    cases = yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text(encoding="utf-8"))
    seeds = yaml.safe_load((ROOT / "eval" / "seed_examples.yaml").read_text(encoding="utf-8"))
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)) >= 38
    key = lambda q: "".join(ch for ch in q if ch.isalnum())  # noqa: E731
    assert not {key(c["question"]) for c in cases} & {key(s["question"]) for s in seeds}
    for c in cases:
        assert c["sql"].strip().upper().startswith("SELECT") and c["category"]
