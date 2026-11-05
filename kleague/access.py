"""접속 코드 확인 (선택 기능).

- 환경변수/Secrets 의 ACCESS_CODE 가 있을 때만 켜진다. 없으면 누구나 바로 쓴다(지금과 같음).
- 비교는 hmac.compare_digest (입력 길이·내용에 따라 걸리는 시간이 달라지지 않게).
- 틀린 시도는 세션당 MAX_FAILS 번까지. 넘으면 그 세션에서는 더 비교하지 않는다.
- 코드 값은 돌려주지도, 기록하지도 않는다. 상태(state)에는 통과 여부와 틀린 횟수만 둔다.
"""
from __future__ import annotations

import hmac
from collections.abc import MutableMapping

MAX_FAILS = 5
OK_KEY = "access_ok"
FAIL_KEY = "access_fails"


class AccessGate:
    def __init__(self, code: str | None):
        self._code = (code or "").strip()

    def __repr__(self) -> str:  # 코드 값이 디버그 출력에 섞이지 않게
        return f"AccessGate(enabled={self.enabled})"

    @property
    def enabled(self) -> bool:
        return bool(self._code)

    def is_open(self, state: MutableMapping) -> bool:
        return not self.enabled or bool(state.get(OK_KEY))

    def fails(self, state: MutableMapping) -> int:
        return int(state.get(FAIL_KEY, 0))

    def remaining(self, state: MutableMapping) -> int:
        return max(0, MAX_FAILS - self.fails(state))

    def locked(self, state: MutableMapping) -> bool:
        return self.enabled and not self.is_open(state) and self.fails(state) >= MAX_FAILS

    def try_code(self, state: MutableMapping, attempt: str | None) -> str:
        """→ "ok" / "wrong" / "locked" / "off"(기능 꺼짐)."""
        if not self.enabled:
            return "off"
        if state.get(OK_KEY):
            return "ok"
        if self.fails(state) >= MAX_FAILS:
            return "locked"
        given = (attempt or "").strip().encode("utf-8")
        if hmac.compare_digest(given, self._code.encode("utf-8")):
            state[OK_KEY] = True
            state[FAIL_KEY] = 0
            return "ok"
        state[FAIL_KEY] = self.fails(state) + 1
        return "locked" if self.fails(state) >= MAX_FAILS else "wrong"
