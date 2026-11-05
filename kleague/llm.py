"""LLM 호출 계층.

- OpenAI 호환 API 하나로 Groq / OpenAI / Gemini 교체 가능 (LLM_BASE_URL, LLM_MODEL)
- 타임아웃·재시도는 SDK 설정으로 처리
- 테스트에서는 FakeLLM 으로 교체 (네트워크 없이 파이프라인 검증)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMReply:
    text: str
    tokens: int = 0              # 합계 (입력 + 출력)
    prompt_tokens: int = 0       # 입력
    completion_tokens: int = 0   # 출력 (추론 모델은 숨은 추론 토큰 포함)


class LLM(Protocol):
    def complete(self, system: str, user: str, max_tokens: int = 1200,
                 temperature: float = 0.0, reasoning_effort: str | None = None) -> LLMReply: ...


class OpenAICompatLLM:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 30.0,
                 reasoning_effort: str = "", max_retries: int = 2):
        from openai import OpenAI  # 지연 import

        self.model = model
        # gpt-oss 같은 추론 모델의 숨은 추론 토큰 양 (low 면 응답이 빨라지고 토큰이 준다). 비우면 서비스 기본값
        self.extra = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout,
                             max_retries=max_retries)  # 평가는 0: 429 를 SDK 가 몰래 재시도하지 않게

    def complete(self, system: str, user: str, max_tokens: int = 1200,
                 temperature: float = 0.0, reasoning_effort: str | None = None) -> LLMReply:
        # reasoning_effort: 이 호출만 추론 강도를 바꿀 때 (예: 요약은 'low'). None 이면 기본 설정
        extra = {"reasoning_effort": reasoning_effort} if reasoning_effort else self.extra
        res = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature,
            max_tokens=max_tokens,
            **({"extra_body": extra} if extra else {}),
        )
        usage = getattr(res, "usage", None)
        return LLMReply(text=res.choices[0].message.content or "",
                        tokens=int(getattr(usage, "total_tokens", 0) or 0),
                        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0))


_LIMIT_KIND = re.compile(r"\((TPM|TPD|RPM|RPD)\)")
_LIMIT_NUMS = re.compile(r"Limit\s+(\d+),\s*Used\s+(\d+),\s*Requested\s+(\d+)")
_LIMIT_NAMES = {"TPM": "분당 토큰", "TPD": "일일 토큰", "RPM": "분당 요청", "RPD": "일일 요청"}


def rate_limit_info(e: Exception) -> dict:
    """429 예외에서 retry-after 와 한도 종류(분당/일일)만 뽑는다.
    오류 원문에는 조직 ID 등이 섞여 있어 저장하지 않고, 정해진 항목만 꺼낸다."""
    resp = getattr(e, "response", None)
    headers = getattr(resp, "headers", None) or {}
    try:
        get = headers.get
    except AttributeError:
        get = lambda k, d=None: d  # noqa: E731
    body = getattr(e, "body", None)
    msg = ""
    if isinstance(body, dict):
        err = body.get("error", body)
        msg = str(err.get("message", "")) if isinstance(err, dict) else ""
    if not msg:
        msg = str(getattr(e, "message", "") or "")
    kind = (_LIMIT_KIND.search(msg) or [None, None])[1]
    nums = _LIMIT_NUMS.search(msg)
    info = {
        "retry_after": get("retry-after"),
        "limit_kind": kind or "unknown",
        "limit_name": _LIMIT_NAMES.get(kind or "", "알 수 없음"),
        "limit": int(nums.group(1)) if nums else None,
        "used": int(nums.group(2)) if nums else None,
        "requested": int(nums.group(3)) if nums else None,
        "remaining_tokens": get("x-ratelimit-remaining-tokens"),
        "remaining_requests": get("x-ratelimit-remaining-requests"),
        "reset_tokens": get("x-ratelimit-reset-tokens"),
        "reset_requests": get("x-ratelimit-reset-requests"),
    }
    return info


class FakeLLM:
    """테스트용: 미리 정한 답을 순서대로 돌려준다."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, max_tokens: int = 1200,
                 temperature: float = 0.0, reasoning_effort: str | None = None) -> LLMReply:
        self.calls.append((system, user))
        text = self.replies.pop(0) if self.replies else '{"sql": null, "reason": "no reply"}'
        return LLMReply(text=text, tokens=100)


# ---------------------------------------------------------------------------
# 응답 파싱: JSON 으로 받되, 모델이 형식을 어겨도 최대한 SQL을 건진다
# ---------------------------------------------------------------------------
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_FENCE = re.compile(r"```(?:sql|json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def parse_sql_reply(text: str) -> tuple[str | None, str]:
    """→ (sql 또는 None, 설명/거절 사유)"""
    if not text:
        return None, "빈 응답"
    m = _JSON_BLOCK.search(text)
    if m:
        try:
            obj = json.loads(m.group(0))
            sql = obj.get("sql")
            note = obj.get("reason") or obj.get("explanation") or ""
            if isinstance(sql, str) and sql.strip():
                return sql.strip().rstrip(";"), note
            return None, note or "SQL로 답할 수 없는 질문"
        except json.JSONDecodeError:
            pass
    fence = _FENCE.search(text)
    body = fence.group(1) if fence else text
    hit = re.search(r"(?is)\b(WITH|SELECT)\b.*", body)
    if hit:
        return hit.group(0).strip().rstrip(";").strip(), ""
    return None, "SQL을 찾지 못함"
