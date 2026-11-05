"""접속 코드 기능 (ACCESS_CODE). LLM·네트워크 호출 없음."""
from pathlib import Path

import pytest

from kleague.access import FAIL_KEY, MAX_FAILS, AccessGate
from kleague.config import load_settings

ROOT = Path(__file__).resolve().parents[1]
CODE = "Blue-" + "Dragon-77"  # 테스트용 가짜 코드


def test_off_when_no_code():
    for code in (None, "", "   "):
        g, state = AccessGate(code), {}
        assert not g.enabled and g.is_open(state)
        assert g.try_code(state, "anything") == "off"


def test_correct_code_opens_and_resets_fails():
    g, state = AccessGate(CODE), {}
    assert not g.is_open(state)
    assert g.try_code(state, "wrong") == "wrong" and g.fails(state) == 1
    assert g.try_code(state, f"  {CODE} ") == "ok"   # 앞뒤 공백은 무시
    assert g.is_open(state) and g.fails(state) == 0


def test_five_wrong_attempts_lock_the_session():
    g, state = AccessGate(CODE), {}
    results = [g.try_code(state, f"bad{i}") for i in range(MAX_FAILS)]
    assert results == ["wrong"] * (MAX_FAILS - 1) + ["locked"]
    assert g.locked(state) and g.remaining(state) == 0
    assert g.try_code(state, CODE) == "locked"        # 잠긴 뒤에는 맞는 코드도 비교하지 않음
    assert not g.is_open(state) and state[FAIL_KEY] == MAX_FAILS


def test_uses_constant_time_compare(monkeypatch):
    import hmac

    import kleague.access as A
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(A.hmac, "compare_digest", lambda a, b: calls.append(1) or real(a, b))
    AccessGate(CODE).try_code({}, CODE)
    assert calls == [1]


def test_code_never_in_repr_or_settings(monkeypatch):
    monkeypatch.setenv("ACCESS_CODE", CODE)
    s = load_settings()
    assert s.access_code == CODE
    assert CODE not in repr(s) and CODE not in repr(AccessGate(CODE))
    assert "llm_api_key" not in repr(s) and "query_db_url" not in repr(s)
    monkeypatch.setenv("ACCESS_CODE", "")
    assert load_settings().access_code == ""


# ------------------------------------------------------------ 실제 화면 (Streamlit AppTest)
def _all_text(at) -> str:
    parts = []
    for kind in ("markdown", "title", "error", "warning", "info", "success", "caption", "text"):
        parts += [str(getattr(e, "value", "")) for e in getattr(at, kind)]
    return "\n".join(parts)


@pytest.fixture()
def app_env(monkeypatch, tmp_path, demo_path):
    monkeypatch.setenv("QUERY_DB_URL", f"sqlite:///{Path(demo_path).as_posix()}")
    monkeypatch.setenv("APP_DB_URL", f"sqlite:///{(tmp_path / 'app.sqlite').as_posix()}")
    monkeypatch.setenv("LLM_API_KEY", "")   # LLM 객체를 만들지 않음 → 호출 불가
    monkeypatch.setenv("GROQ_API_KEY", "")
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    def make():
        st.cache_resource.clear()   # 앞 테스트의 DB 설정이 캐시에 남지 않게
        st.cache_data.clear()
        return AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    return make


def test_app_gate_blocks_until_code(app_env, monkeypatch):
    monkeypatch.setenv("ACCESS_CODE", CODE)
    at = app_env().run()
    assert len(at.text_input) == 1 and not at.tabs          # 코드 입력 화면만, 탭 없음
    at.text_input[0].input("wrong-code")
    at.button[0].click().run()
    text = _all_text(at)
    assert "맞지 않습니다" in text and f"{MAX_FAILS - 1}번" in text
    assert CODE not in text and "wrong-code" not in text   # 코드·입력값이 화면에 안 나옴
    assert not at.tabs and not at.exception

    at.text_input[0].input(CODE)
    at.button[0].click().run()
    assert not at.exception
    assert len(at.tabs) == 3                                 # 통과 → 질문·리포트·성능 탭
    assert CODE not in _all_text(at)


def test_app_locks_after_five_wrong(app_env, monkeypatch):
    monkeypatch.setenv("ACCESS_CODE", CODE)
    at = app_env().run()
    for i in range(MAX_FAILS):
        at.text_input[0].input(f"bad{i}")
        at.button[0].click().run()
    assert "입력할 수 없습니다" in _all_text(at)
    at.run()
    assert not at.text_input and not at.tabs                 # 잠기면 입력칸도 없음


def test_app_open_without_code(app_env, monkeypatch, tmp_path, demo_path):
    import shutil
    (tmp_path / "demo").mkdir()
    shutil.copy(demo_path, tmp_path / "demo" / "demo.sqlite")   # demo/demo.sqlite 를 가리키면 데모 판정
    monkeypatch.setenv("QUERY_DB_URL", f"sqlite:///{(tmp_path / 'demo' / 'demo.sqlite').as_posix()}")
    monkeypatch.setenv("ACCESS_CODE", "")
    at = app_env().run()
    assert not at.exception and len(at.tabs) == 3
    assert "가상 데이터입니다. 실제 K리그 기록이 아닙니다." in _all_text(at)  # 데모 DB 배너
