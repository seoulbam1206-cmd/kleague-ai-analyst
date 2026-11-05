"""생성된 SQL 을 누구에게 보여 줄지 정한다 (관리자에게만).

- 관리자 목록: Secrets/환경변수 ADMIN_EMAILS (쉼표로 구분). 실제 이메일은 코드·문서에 쓰지 않는다.
- 접속자 이메일: Streamlit 이 주는 접속자 정보(st.user)에서 읽는다. 읽을 수 없으면 None.
- 비교는 대소문자·앞뒤 공백을 무시한다.
- 가상 데이터(demo) 모드에서는 모두에게 보여 준다 (실제 DB 구조가 아니므로).
- 관리자가 아니거나 이메일을 알 수 없으면 숨긴다 (기본값 = 숨김).
- 보조 방법: Secrets 의 ADMIN_CODE 를 사이드바에 입력한 세션은 SQL 을 본다 (AdminCodeGate).
  Streamlit 1.42.0 부터 Community Cloud 는 로그인(OIDC, [auth]) 설정 없이는 st.user 에 이메일을 주지 않는다.

SQL 표시 기준: 관리자 이메일이거나, 관리자 코드를 입력한 세션이거나, 데모 모드.
접속자 이메일·관리자 코드·입력값은 화면·로그·기록 어디에도 내지 않는다.
"""
from __future__ import annotations

import hmac
import time
from collections.abc import MutableMapping
from typing import Any

EMAIL_OK_TEXT = "접속자 확인: 이메일 확인됨"
EMAIL_UNKNOWN_TEXT = "접속자 확인: 확인 불가"

ADMIN_MAX_FAILS = 5        # 연속으로 이만큼 틀리면
ADMIN_LOCK_SEC = 60        # 이 시간 동안 입력을 막는다
_OK_KEY = "admin_code_ok"
_FAIL_KEY = "admin_code_fails"
_UNTIL_KEY = "admin_code_locked_until"
_now = time.monotonic      # 테스트에서 바꿔 끼울 수 있게
# Streamlit 이 시험 실행(AppTest·시작 점검)에서만 넣는 가짜 접속자 값 → 실제 접속자가 아니므로 '확인 불가'로 본다
PLACEHOLDER_EMAILS = frozenset({"test@example.com"})


def normalize_email(value: Any) -> str | None:
    """이메일을 비교용으로 다듬는다. 이메일 모양이 아니면 None."""
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    return v if "@" in v and not v.startswith("@") and not v.endswith("@") else None


def parse_admin_emails(raw: str | None) -> frozenset[str]:
    """'a@example.invalid, B@Example.invalid ' → {'a@example.invalid', 'b@example.invalid'}. 빈 값·이메일 모양이 아닌 값은 버린다."""
    if not raw:
        return frozenset()
    return frozenset(e for e in (normalize_email(p) for p in str(raw).split(",")) if e)


def read_viewer_email(user: Any = None) -> str | None:
    """접속자 이메일. user 를 주지 않으면 Streamlit 의 st.user(옛 버전은 st.experimental_user)에서 읽는다.
    어떤 이유로든 읽지 못하면 None (→ SQL 숨김)."""
    try:
        if user is None:
            import streamlit as st

            user = getattr(st, "user", None)
            if user is None:
                user = getattr(st, "experimental_user", None)
        if user is None:
            return None
        email = normalize_email(user.get("email") if hasattr(user, "get") else getattr(user, "email", None))
        return None if email in PLACEHOLDER_EMAILS else email
    except Exception:
        return None


def email_status_text(email: str | None) -> str:
    """사이드바 진단 문구. 이메일 주소 자체는 넣지 않는다 (확인됨 / 확인 불가만)."""
    return EMAIL_OK_TEXT if normalize_email(email) else EMAIL_UNKNOWN_TEXT


def can_view_sql(email: str | None, admins: frozenset[str], demo_mode: bool, code_ok: bool = False) -> bool:
    if demo_mode or code_ok:
        return True
    e = normalize_email(email)
    return bool(e) and e in admins


class AdminCodeGate:
    """관리자 코드 확인 (선택). 세션 상태에는 통과 여부·틀린 횟수·잠금 끝 시각만 둔다 (코드·입력값은 저장 안 함).
    새로고침하면 세션이 새로 시작되어 다시 입력해야 한다."""

    def __init__(self, code: str | None):
        self._code = (code or "").strip()

    def __repr__(self) -> str:  # 코드 값이 디버그 출력에 섞이지 않게
        return f"AdminCodeGate(enabled={self.enabled})"

    @property
    def enabled(self) -> bool:
        return bool(self._code)

    def is_ok(self, state: MutableMapping) -> bool:
        return self.enabled and bool(state.get(_OK_KEY))

    def wait_sec(self, state: MutableMapping) -> int:
        """잠겨 있으면 남은 초, 아니면 0."""
        left = float(state.get(_UNTIL_KEY, 0.0)) - _now()
        return max(0, int(left + 0.999)) if left > 0 else 0

    def try_code(self, state: MutableMapping, attempt: str | None) -> str:
        """'ok' / 'wrong' / 'locked' / 'disabled'. 잠긴 동안에는 비교하지 않는다."""
        if not self.enabled:
            return "disabled"
        if self.wait_sec(state) > 0:
            return "locked"
        if hmac.compare_digest((attempt or "").strip().encode("utf-8"), self._code.encode("utf-8")):
            state[_OK_KEY] = True
            state[_FAIL_KEY] = 0
            return "ok"
        fails = int(state.get(_FAIL_KEY, 0)) + 1
        if fails >= ADMIN_MAX_FAILS:
            state[_UNTIL_KEY] = _now() + ADMIN_LOCK_SEC
            state[_FAIL_KEY] = 0          # 잠금이 풀리면 다시 5번
            return "locked"
        state[_FAIL_KEY] = fails
        return "wrong"
