"""생성된 SQL 을 누구에게 보여 줄지 정한다 (관리자에게만).

- 관리자 목록: Secrets/환경변수 ADMIN_EMAILS (쉼표로 구분). 실제 이메일은 코드·문서에 쓰지 않는다.
- 접속자 이메일: Streamlit 이 주는 접속자 정보(st.user)에서 읽는다. 읽을 수 없으면 None.
- 비교는 대소문자·앞뒤 공백을 무시한다.
- 가상 데이터(demo) 모드에서는 모두에게 보여 준다 (실제 DB 구조가 아니므로).
- 관리자가 아니거나 이메일을 알 수 없으면 숨긴다 (기본값 = 숨김).
"""
from __future__ import annotations

from typing import Any


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
        email = user.get("email") if hasattr(user, "get") else getattr(user, "email", None)
        return normalize_email(email)
    except Exception:
        return None


def can_view_sql(email: str | None, admins: frozenset[str], demo_mode: bool) -> bool:
    if demo_mode:
        return True
    e = normalize_email(email)
    return bool(e) and e in admins
