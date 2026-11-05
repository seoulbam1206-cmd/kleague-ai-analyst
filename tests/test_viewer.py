"""관리자에게만 SQL 표시 (ADMIN_EMAILS). LLM·네트워크 호출 없음.

이메일은 모두 가짜 값(example.invalid)만 쓴다.
"""
import shutil
from pathlib import Path

import pandas as pd
import pytest

from kleague import viewer
from kleague.config import load_settings
from kleague.text2sql import Answer

ROOT = Path(__file__).resolve().parents[1]
ADMIN = "admin@example.invalid"
SQL_OK = "SELECT team_s_name AS 구단 FROM club ORDER BY team_s_name LIMIT 3"
SQL_BAD = "SELECT nosuchcol FROM game LIMIT 1"
ERR_NOTE = "1차 오류 → 자가 수정: OperationalError: no such column: nosuchcol"


# ------------------------------------------------------------ 판정 함수
def test_parse_admin_emails_ignores_case_space_and_junk():
    got = viewer.parse_admin_emails(f"  {ADMIN.upper()} , second@example.invalid,, not-an-email ,")
    assert got == {ADMIN, "second@example.invalid"}
    assert viewer.parse_admin_emails("") == frozenset() and viewer.parse_admin_emails(None) == frozenset()


def test_can_view_sql_rules():
    admins = viewer.parse_admin_emails(ADMIN)
    assert viewer.can_view_sql(ADMIN, admins, demo_mode=False)                      # 관리자 → 보임
    assert viewer.can_view_sql("  Admin@EXAMPLE.invalid ", admins, demo_mode=False)  # 대소문자·공백 무시
    assert not viewer.can_view_sql("user@example.invalid", admins, demo_mode=False)  # 일반 계정 → 숨김
    assert not viewer.can_view_sql(None, admins, demo_mode=False)                    # 이메일 모름 → 숨김
    assert not viewer.can_view_sql("", admins, demo_mode=False)
    assert not viewer.can_view_sql(ADMIN, frozenset(), demo_mode=False)              # 관리자 목록 없음 → 숨김
    assert viewer.can_view_sql(None, frozenset(), demo_mode=True)                    # 데모 → 모두 보임


def test_read_viewer_email_from_user_info():
    assert viewer.read_viewer_email({"email": f" {ADMIN.upper()} "}) == ADMIN        # st.user 처럼 get 지원

    class Obj:
        email = ADMIN
    assert viewer.read_viewer_email(Obj()) == ADMIN

    class Broken:
        def get(self, *_):
            raise RuntimeError("no user info")
    assert viewer.read_viewer_email(Broken()) is None                                # 읽다 실패 → None
    assert viewer.read_viewer_email({}) is None and viewer.read_viewer_email({"email": None}) is None
    assert viewer.read_viewer_email({"email": "not-an-email"}) is None
    assert viewer.read_viewer_email({"email": "Test@Example.com"}) is None          # Streamlit 시험용 가짜 값


def test_settings_reads_admin_emails(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", f"{ADMIN.upper()}, second@example.invalid")
    s = load_settings()
    assert s.admin_emails == {ADMIN, "second@example.invalid"}
    assert ADMIN not in repr(s)                                                      # repr 에 이메일이 안 나옴
    monkeypatch.setenv("ADMIN_EMAILS", "")
    assert load_settings().admin_emails == frozenset()


# ------------------------------------------------------------ 앱 화면 (AppTest)
def _answers():
    ok = Answer("ok", "구단 목록", sql=SQL_OK, df=pd.DataFrame({"구단": ["가상팀"]}),
                summary="구단 목록입니다.", attempts=2, latency_ms=12, tokens=0,
                notes=[ERR_NOTE])
    err = Answer("error", "없는 컬럼 질문", sql=SQL_BAD,
                 summary="SQL을 실행하지 못했습니다. 질문을 조금 더 구체적으로 바꿔 보세요.",
                 attempts=3, latency_ms=20, notes=["OperationalError: no such column: nosuchcol"])
    return [{"role": "user", "text": ok.question}, {"role": "assistant", "answer": ok, "log_id": None},
            {"role": "user", "text": err.question}, {"role": "assistant", "answer": err, "log_id": None}]


def _visible(at) -> str:
    parts = []
    for kind in ("markdown", "title", "error", "warning", "info", "success", "caption", "text", "code"):
        parts += [str(getattr(e, "value", "")) for e in at.get(kind)]
    parts += [str(getattr(e, "label", "")) for e in at.get("expandable")]
    return "\n".join(parts)


@pytest.fixture()
def run_app(monkeypatch, tmp_path, demo_path):
    """email: 접속자 이메일(None = 알 수 없음), demo: 가상 데이터 모드 여부."""
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    def run(email, demo=False, admins=ADMIN, admin_code=""):
        if demo:
            (tmp_path / "demo").mkdir(exist_ok=True)
            shutil.copy(demo_path, tmp_path / "demo" / "demo.sqlite")   # demo/demo.sqlite → 데모 판정
            db = tmp_path / "demo" / "demo.sqlite"
        else:
            db = Path(demo_path)                                          # 다른 이름 → 실데이터 모드로 동작
        monkeypatch.setenv("QUERY_DB_URL", f"sqlite:///{db.as_posix()}")
        monkeypatch.setenv("APP_DB_URL", f"sqlite:///{(tmp_path / 'app.sqlite').as_posix()}")
        monkeypatch.setenv("LLM_API_KEY", "")
        monkeypatch.setenv("GROQ_API_KEY", "")
        monkeypatch.setenv("ACCESS_CODE", "")
        monkeypatch.setenv("ADMIN_EMAILS", admins)
        monkeypatch.setenv("ADMIN_CODE", admin_code)
        monkeypatch.setattr(viewer, "read_viewer_email", lambda user=None: viewer.normalize_email(email))
        st.cache_resource.clear()
        st.cache_data.clear()
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
        at.session_state["chat"] = _answers()
        at.run()
        assert not at.exception
        return at
    return run


def _assert_sql_hidden(at):
    text = _visible(at)
    assert SQL_OK not in text and SQL_BAD not in text and "nosuchcol" not in text   # 오류 화면 속 SQL·오류 원문도 없음
    assert not at.get("code") and not any("생성된 SQL" in e.label for e in at.get("expandable"))
    assert "정답률 평가" not in text                                                  # 정답 SQL 조각이 든 평가 기록도 숨김
    # 결과 표·요약은 그대로 보인다
    assert "구단 목록입니다." in text and "SQL을 실행하지 못했습니다" in text and len(at.dataframe) >= 1


def _assert_sql_shown(at):
    codes = [c.value for c in at.get("code")]
    assert SQL_OK in codes and SQL_BAD in codes
    assert "nosuchcol" in _visible(at)                                                # 오류 상세(메모)도 보임
    assert "정답률 평가" in _visible(at)


def test_admin_sees_sql(run_app):
    _assert_sql_shown(run_app(ADMIN))


def test_admin_match_ignores_case_and_spaces(run_app):
    _assert_sql_shown(run_app("  ADMIN@Example.Invalid "))


def test_normal_user_does_not_see_sql(run_app):
    _assert_sql_hidden(run_app("user@example.invalid"))


def test_unknown_email_does_not_see_sql(run_app):
    _assert_sql_hidden(run_app(None))


def test_no_admin_list_hides_sql_even_for_known_email(run_app):
    _assert_sql_hidden(run_app(ADMIN, admins=""))


def test_demo_mode_shows_sql_to_everyone(run_app):
    at = run_app(None, demo=True)
    assert "가상 데이터입니다" in _visible(at)
    _assert_sql_shown(at)


def test_read_viewer_email_works_inside_app_without_login(monkeypatch, tmp_path, demo_path):
    """패치 없이 실제 st.user 를 읽어도 앱이 오류 없이 뜨고, 로그인 정보가 없으면 SQL 을 숨긴다."""
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("QUERY_DB_URL", f"sqlite:///{Path(demo_path).as_posix()}")
    monkeypatch.setenv("APP_DB_URL", f"sqlite:///{(tmp_path / 'app.sqlite').as_posix()}")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("ACCESS_CODE", "")
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)
    monkeypatch.setenv("ADMIN_CODE", "")
    st.cache_resource.clear()
    st.cache_data.clear()
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.session_state["chat"] = _answers()
    at.run()
    assert not at.exception
    _assert_sql_hidden(at)
    assert viewer.EMAIL_UNKNOWN_TEXT in _visible(at)   # AppTest 의 가짜 접속자(test@...)는 '확인 불가'


# ------------------------------------------------------------ 관리자 코드 (보조 방법)
CODE = "fake-admin-code-" + "x" * 12        # 가짜 값 (실제 코드 아님)


def test_admin_code_gate_rules(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(viewer, "_now", lambda: clock[0])
    off = viewer.AdminCodeGate("")
    assert not off.enabled and off.try_code({}, CODE) == "disabled" and not off.is_ok({})
    g = viewer.AdminCodeGate(f"  {CODE} ")
    assert CODE not in repr(g)
    state = {}
    assert g.try_code(state, "wrong") == "wrong" and not g.is_ok(state)
    assert g.try_code(state, CODE.upper()) == "wrong"                     # 대소문자 구분 (코드는 정확히)
    assert g.try_code(state, f" {CODE} ") == "ok" and g.is_ok(state)       # 앞뒤 공백은 무시
    assert CODE not in str(state)                                          # 세션에 코드·입력값을 저장하지 않음


def test_admin_code_rate_limit(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(viewer, "_now", lambda: clock[0])
    g, state = viewer.AdminCodeGate(CODE), {}
    for _ in range(viewer.ADMIN_MAX_FAILS - 1):
        assert g.try_code(state, "bad") == "wrong"
    assert g.try_code(state, "bad") == "locked" and g.wait_sec(state) == viewer.ADMIN_LOCK_SEC
    assert g.try_code(state, CODE) == "locked" and not g.is_ok(state)     # 잠긴 동안은 맞는 코드도 안 받음
    clock[0] += viewer.ADMIN_LOCK_SEC - 1
    assert g.try_code(state, CODE) == "locked"
    clock[0] += 2
    assert g.wait_sec(state) == 0 and g.try_code(state, CODE) == "ok"     # 잠금이 풀리면 다시 가능


def test_can_view_sql_with_code():
    assert viewer.can_view_sql(None, frozenset(), demo_mode=False, code_ok=True)
    assert not viewer.can_view_sql(None, frozenset(), demo_mode=False, code_ok=False)


def test_email_status_text_never_contains_email():
    assert viewer.email_status_text(ADMIN) == viewer.EMAIL_OK_TEXT and "@" not in viewer.EMAIL_OK_TEXT
    assert viewer.email_status_text(None) == viewer.EMAIL_UNKNOWN_TEXT == "접속자 확인: 확인 불가"
    assert viewer.email_status_text("not-an-email") == viewer.EMAIL_UNKNOWN_TEXT


def test_settings_reads_admin_code_without_repr(monkeypatch):
    monkeypatch.setenv("ADMIN_CODE", f" {CODE} ")
    s = load_settings()
    assert s.admin_code == CODE and CODE not in repr(s)


def _submit_code(at, code):
    at.text_input(key="admin_code_input").input(code)
    at.button(key="admin_code_submit").click().run()
    assert not at.exception
    return at


def test_diagnostic_shows_status_but_not_email(run_app):
    at = run_app(ADMIN)
    text = _visible(at)
    assert viewer.EMAIL_OK_TEXT in text and ADMIN not in text and "admin@" not in text.lower()
    at = run_app(None)
    assert viewer.EMAIL_UNKNOWN_TEXT in _visible(at)


def test_no_admin_code_setting_means_no_input(run_app):
    at = run_app(None, admin_code="")
    assert not [w for w in at.text_input if w.key == "admin_code_input"]
    _assert_sql_hidden(at)


def test_admin_code_turns_on_sql_when_email_unknown(run_app):
    at = run_app(None, admin_code=CODE)
    _assert_sql_hidden(at)                                                 # 입력 전: 숨김
    _submit_code(at, CODE)
    _assert_sql_shown(at)                                                  # 맞는 코드: 이 세션에서 보임
    text = _visible(at)
    assert "관리자 코드로 켜짐" in text and CODE not in text
    assert not [w for w in at.text_input if w.key == "admin_code_input"]  # 통과 뒤 입력칸 없음


def test_wrong_admin_code_keeps_sql_hidden(run_app):
    at = _submit_code(run_app("user@example.invalid", admin_code=CODE), "wrong-code")
    _assert_sql_hidden(at)
    text = _visible(at)
    assert "맞지 않습니다" in text and "wrong-code" not in text and CODE not in text


def test_admin_code_lock_in_app(run_app, monkeypatch):
    clock = [5000.0]
    monkeypatch.setattr(viewer, "_now", lambda: clock[0])
    at = run_app(None, admin_code=CODE)
    for i in range(viewer.ADMIN_MAX_FAILS):
        _submit_code(at, f"bad{i}")
    assert "잠시 막았습니다" in _visible(at)
    _submit_code(at, CODE)                                                 # 잠긴 동안 맞는 코드도 거부
    _assert_sql_hidden(at)
    clock[0] += viewer.ADMIN_LOCK_SEC + 1
    _submit_code(at, CODE)
    _assert_sql_shown(at)


def test_new_session_needs_code_again(run_app):
    at = _submit_code(run_app(None, admin_code=CODE), CODE)
    _assert_sql_shown(at)
    _assert_sql_hidden(run_app(None, admin_code=CODE))                     # 새 세션(새로고침) → 다시 숨김
