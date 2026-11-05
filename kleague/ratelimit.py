"""요청 제한 (비용·남용 방지).

- 세션당: 1분에 N회 (슬라이딩 윈도우)
- 전체: 하루 토큰 예산 (DB에 저장되므로 재시작해도 유지)
"""
from __future__ import annotations

import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, max_calls: int, window_sec: float = 60.0):
        self.max_calls = max_calls
        self.window = window_sec
        self._hits: deque[float] = deque()

    def allow(self, now: float | None = None) -> tuple[bool, int]:
        """(허용 여부, 다시 가능해질 때까지 남은 초)"""
        now = time.time() if now is None else now
        while self._hits and now - self._hits[0] >= self.window:
            self._hits.popleft()
        if len(self._hits) >= self.max_calls:
            return False, int(self.window - (now - self._hits[0])) + 1
        self._hits.append(now)
        return True, 0


def budget_left(used_today: int, budget: int) -> int:
    return max(0, budget - used_today)
