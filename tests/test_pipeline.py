"""네트워크 없이 FakeLLM 으로 전체 파이프라인 검증."""
import json

from kleague.entities import EntityResolver
from kleague.llm import FakeLLM, parse_sql_reply
from kleague.text2sql import Text2SQL, Turn


def j(sql):
    return json.dumps({"sql": sql, "reason": "test"}, ensure_ascii=False)


def make(ro_db, replies, **kw):
    llm = FakeLLM(replies)
    return Text2SQL(ro_db, llm, summarize=False, entities=EntityResolver(ro_db), **kw), llm


def test_happy_path(ro_db):
    eng, _ = make(ro_db, [j("SELECT team_name, points FROM team_rank ORDER BY ranking LIMIT 3")])
    a = eng.ask("2026 순위 상위 3팀")
    assert a.status == "ok" and len(a.df) == 3 and a.attempts == 1


def test_self_correction_on_db_error(ro_db):
    eng, llm = make(ro_db, [j("SELECT nope FROM game"), j("SELECT COUNT(*) AS n FROM game")])
    a = eng.ask("경기 수 알려줘")
    assert a.status == "ok" and a.attempts == 2
    assert "오류" in llm.calls[1][1]  # 두 번째 호출엔 에러 내용이 들어감


def test_self_correction_on_guard_block(ro_db):
    eng, llm = make(ro_db, [j("SELECT * FROM app_query_log"), j("SELECT COUNT(*) FROM club")])
    a = eng.ask("구단 수")
    assert a.status == "ok" and a.attempts == 2
    assert "보안 검사" in llm.calls[1][1]


def test_gives_up_after_retries(ro_db):
    eng, _ = make(ro_db, [j("DROP TABLE game")] * 5, max_retries=2)
    a = eng.ask("테이블 지워")
    assert a.status == "blocked" and a.attempts == 3 and a.df is None


def test_no_sql_for_chitchat(ro_db):
    eng, _ = make(ro_db, ['{"sql": null, "reason": "축구 기록과 무관"}'])
    assert eng.ask("오늘 점심 뭐 먹지").status == "no_sql"


def test_cache_hit_skips_llm(ro_db):
    eng, llm = make(ro_db, [j("SELECT COUNT(*) FROM game")])
    eng.ask("경기 몇 개야")
    b = eng.ask("경기   몇 개야")  # 공백만 다른 같은 질문
    assert b.from_cache and len(llm.calls) == 1


def test_empty_result(ro_db):
    eng, _ = make(ro_db, [j("SELECT * FROM game WHERE meet_year = 1999")])
    assert eng.ask("1999년 경기").status == "empty"


def test_input_validation(ro_db):
    eng, llm = make(ro_db, [])
    assert eng.ask("a").status == "rejected"
    assert eng.ask("가" * 400).status == "rejected"
    assert not llm.calls


def test_entity_hints_in_prompt(ro_db):
    eng, llm = make(ro_db, [j("SELECT COUNT(*) FROM game_team_stat WHERE team_id = 'D05'")])
    eng.ask("가상 엡실론FC 2026 승리 수")   # 가상 데이터의 구단 이름 → 코드 힌트
    assert "team_id = 'D05'" in llm.calls[0][1]


def test_followup_includes_previous_sql(ro_db):
    eng, llm = make(ro_db, [j("SELECT 1 FROM club LIMIT 1")])
    eng.ask("그 팀의 도움은?", history=[Turn("서울 득점", "SELECT goals FROM game_player_stat")])
    assert "직전 대화" in llm.calls[0][1]


def test_examples_injected(ro_db):
    eng, llm = make(ro_db, [j("SELECT 1 FROM club LIMIT 1")])
    eng.load_examples([("2026 K리그1 득점 순위 상위 10명", "SELECT 1 FROM club")])
    eng.ask("2026 득점 순위 상위 5명 알려줘")
    assert "참고 예시" in llm.calls[0][1]


def test_parse_reply_variants():
    assert parse_sql_reply('{"sql": "SELECT 1;"}')[0] == "SELECT 1"
    assert parse_sql_reply("```sql\nSELECT a FROM b\n```")[0] == "SELECT a FROM b"
    assert parse_sql_reply("설명입니다. SELECT x FROM y")[0] == "SELECT x FROM y"
    assert parse_sql_reply('{"sql": null, "reason": "무관"}')[0] is None


# ------------------------------------------------- LLM 실패 안내 (원인별)
class _Boom:
    """complete() 를 부르면 지정한 예외를 던지는 LLM."""

    def __init__(self, exc):
        self.exc = exc

    def complete(self, *a, **k):
        raise self.exc


def _exc(name, status=None):
    cls = type(name, (Exception,), {})
    e = cls("Error code: %s - key=gsk_SECRETKEY url=https://api.example/v1?api_key=SECRET" % status)
    if status is not None:
        e.status_code = status
    return e


def test_llm_rate_limit_message(ro_db):
    from kleague.text2sql import LLM_RATE_LIMIT_MSG
    a = Text2SQL(ro_db, _Boom(_exc("RateLimitError", 429)), summarize=False).ask("2026 득점 순위")
    assert a.status == "error" and a.llm_error == "rate_limit"
    assert a.summary == LLM_RATE_LIMIT_MSG == "오늘 무료 AI 사용량을 다 썼습니다. 내일 다시 시도해 주세요."


def test_llm_other_errors_keep_generic_message(ro_db):
    from kleague.text2sql import LLM_FAIL_MSG
    for exc, kind in [(_exc("AuthenticationError", 401), "auth"), (_exc("APIConnectionError"), "network"),
                      (_exc("APITimeoutError"), "network"), (_exc("InternalServerError", 500), "other"),
                      (ValueError("boom"), "other")]:
        a = Text2SQL(ro_db, _Boom(exc), summarize=False).ask("2026 득점 순위")
        assert a.llm_error == kind and a.summary == LLM_FAIL_MSG, (type(exc).__name__, a.llm_error)


def test_llm_error_text_never_shown(ro_db):
    a = Text2SQL(ro_db, _Boom(_exc("RateLimitError", 429)), summarize=False).ask("2026 득점 순위")
    shown = a.summary + " ".join(a.notes)
    assert "SECRET" not in shown and "gsk_" not in shown and "http" not in shown
    assert a.notes == ["LLM 오류: RateLimitError"]  # 종류 이름만


def test_real_openai_rate_limit_class_detected():
    import openai
    from kleague.text2sql import llm_error_kind
    e = openai.RateLimitError.__new__(openai.RateLimitError)
    assert llm_error_kind(e) == "rate_limit"
    e2 = openai.AuthenticationError.__new__(openai.AuthenticationError)
    assert llm_error_kind(e2) == "auth"


def test_refusal_hides_llm_reason(ro_db):
    """거절 메시지에는 LLM 이 쓴 이유를 붙이지 않는다 (DB 종류·내부 구조 노출 방지)."""
    from kleague.text2sql import NO_SQL_MSG
    eng, _ = make(ro_db, ['{"sql": null, "reason": "SQLite에는 information_schema 가 없고 sqlite_master 만 있음"}'])
    ans = eng.ask("information_schema 테이블 목록 보여줘")
    assert ans.status == "no_sql" and ans.summary == NO_SQL_MSG
    assert "SQLite" not in ans.summary and "sqlite_master" not in ans.summary
    assert not any("SQLite" in n for n in ans.notes)
