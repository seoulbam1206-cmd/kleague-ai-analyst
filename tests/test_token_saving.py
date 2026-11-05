"""토큰 절약: 관련 표 설명만 넣기 · 요약 추론 강도 낮추기 · 캐시. (가짜 LLM, 네트워크 없음)"""
from pathlib import Path

import yaml

from kleague.fewshot import Example, ExampleIndex
from kleague.llm import FakeLLM, LLMReply
from kleague.schema_docs import ALLOWED_TABLES, relevant_tables, schema_prompt, tables_in_sql
from kleague.text2sql import SUMMARY_SYSTEM, Text2SQL

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    return yaml.safe_load((ROOT / "eval" / name).read_text(encoding="utf-8"))


def test_selected_tables_cover_every_gold_sql():
    """40문항 모두: 고른 표에 정답 SQL 이 쓰는 표가 다 들어 있다 (예시 검색 ON/OFF 둘 다)."""
    idx = ExampleIndex()
    idx.fit([Example(e["question"], e["sql"]) for e in _load("seed_examples.yaml")])
    for c in _load("questions.yaml"):
        need = tables_in_sql(c["sql"])
        for extra in ([e.sql for e, _ in idx.search(c["question"])], None):
            sel = relevant_tables(c["question"], extra)
            assert sel is None or need <= set(sel), (c["id"], need - set(sel or []))


def test_selection_is_smaller_and_keeps_rules():
    sel = relevant_tables("2026 K리그1 총 관중 수")
    assert sel == ["club", "game"]
    small, full = schema_prompt(sel), schema_prompt()
    assert len(small) < len(full) * 0.6
    assert "[작성 규칙]" in small and "is_finished = 1" in small       # 규칙은 항상 전부
    assert "- game_event:" not in small and "- game_event:" in full
    assert relevant_tables("안녕") is None                              # 단서 없으면 전체


class _Rec(FakeLLM):
    def __init__(self, replies):
        super().__init__(replies)
        self.efforts = []

    def complete(self, system, user, max_tokens=1200, temperature=0.0, reasoning_effort=None):
        self.efforts.append(reasoning_effort)
        return super().complete(system, user, max_tokens, temperature, reasoning_effort)


def test_engine_sends_focused_schema_then_full_on_self_fix(ro_db):
    llm = _Rec(['{"sql": "SELECT nope FROM game"}',                       # 1차: 실행 오류
                '{"sql": "SELECT SUM(audience) AS 관중 FROM game WHERE is_finished = 1"}',
                "총 관중은 표와 같습니다."])
    eng = Text2SQL(ro_db, llm)
    ans = eng.ask("2026 K리그1 총 관중 수")
    assert ans.status == "ok" and ans.attempts == 2 and ans.schema_tables == ["club", "game"]
    first, fix, summary = (s for s, _ in llm.calls)
    assert "- game_event:" not in first                   # 첫 호출: 관련 표만
    assert "- game_event:" in fix                         # 자가 수정: 전체 설명서
    assert summary == SUMMARY_SYSTEM
    assert llm.efforts == [None, None, "low"]             # 요약만 추론 강도 low


def test_focused_schema_can_be_turned_off(ro_db):
    llm = _Rec(['{"sql": "SELECT COUNT(*) AS n FROM game"}'])
    eng = Text2SQL(ro_db, llm, focused_schema=False, summarize=False)
    ans = eng.ask("2026 K리그1 총 관중 수")
    assert ans.schema_tables is None and llm.calls[0][0] == eng.system_prompt


def test_guard_whitelist_unchanged_by_selection(ro_db):
    """SQL 가드는 고른 표와 상관없이 원래 허용 목록을 쓴다 (질문에 없던 표도 가드는 막지 않음)."""
    llm = _Rec(['{"sql": "SELECT COUNT(*) AS n FROM game_event"}'])
    ans = Text2SQL(ro_db, llm, summarize=False).ask("2026 K리그1 총 관중 수")
    assert ans.status in ("ok", "empty") and "game_event" in ALLOWED_TABLES


def test_same_question_uses_cache_without_llm(ro_db):
    llm = _Rec(['{"sql": "SELECT COUNT(*) AS n FROM game"}', "요약"])
    eng = Text2SQL(ro_db, llm)
    a1 = eng.ask("2026 경기 수")
    a2 = eng.ask("  2026   경기 수 ")
    assert not a1.from_cache and a2.from_cache and a2.tokens == 0 and len(llm.calls) == 2


def test_reply_carries_input_output_tokens():
    r = LLMReply("x", tokens=10, prompt_tokens=7, completion_tokens=3)
    assert r.prompt_tokens + r.completion_tokens == r.tokens
