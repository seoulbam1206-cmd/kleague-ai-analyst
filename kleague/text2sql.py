"""질문 → SQL → 실행 → 요약 파이프라인 (UI와 분리, 단위 테스트 가능).

흐름
  1) 입력 검사 (길이·제어문자)                      → 비용 0
  2) 캐시 확인 (같은 질문 10분 내 재사용)            → LLM·DB 호출 0
  3) 이름 → 코드 힌트 + 유사 예시 검색               → 정답률↑
  4) LLM: SQL 생성 (JSON 응답)
  5) SQL 가드 (AST 검사 + LIMIT 강제)
  6) 읽기 전용 실행 (시간 제한)
  7) 실패 시 에러를 근거로 자가 수정 (최대 N회)
  8) 결과 요약 (표에 있는 숫자만 쓰도록 지시)
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from .db import Database, QueryTimeout
from .entities import EntityResolver
from .fewshot import Example, ExampleIndex, format_examples
from .llm import LLM, parse_sql_reply, rate_limit_info
from .schema_docs import ALLOWED_TABLES, relevant_tables, schema_prompt
from .sql_guard import GuardResult, check_sql

_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


@dataclass
class Turn:
    question: str
    sql: str | None


@dataclass
class Answer:
    status: str                    # ok / empty / no_sql / blocked / error / rejected
    question: str
    sql: str | None = None
    df: pd.DataFrame | None = None
    summary: str = ""
    attempts: int = 0
    tokens: int = 0
    latency_ms: int = 0
    truncated: bool = False
    from_cache: bool = False
    notes: list[str] = field(default_factory=list)
    llm_error: str = ""            # LLM 호출 실패 종류: rate_limit / auth / network / other (원문은 저장 안 함)
    schema_tables: list[str] | None = None   # SQL 생성 프롬프트에 넣은 표 (None = 전체)


class TTLCache:
    def __init__(self, ttl_sec: int = 600, max_items: int = 256):
        self.ttl, self.max = ttl_sec, max_items
        self._d: OrderedDict[str, tuple[float, Answer]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Answer | None:
        with self._lock:
            hit = self._d.get(key)
            if not hit:
                return None
            ts, ans = hit
            if time.time() - ts > self.ttl:
                self._d.pop(key, None)
                return None
            self._d.move_to_end(key)
            return ans

    def put(self, key: str, ans: Answer) -> None:
        with self._lock:
            self._d[key] = (time.time(), ans)
            self._d.move_to_end(key)
            while len(self._d) > self.max:
                self._d.popitem(last=False)


SYSTEM_TEMPLATE = """너는 K리그 공식기록 데이터베이스 전문 SQL 분석가다.
사용자 질문을 {dialect_name} 문법의 SELECT 문 하나로 바꾼다.

{schema}

[출력 형식] 반드시 JSON 한 개만 출력한다. 설명·마크다운 금지.
- 데이터로 답할 수 있으면: {{"sql": "SELECT ...", "reason": "한 줄 설명"}}
- K리그 기록과 무관하거나 데이터로 답할 수 없으면: {{"sql": null, "reason": "이유"}}
"""

NO_SQL_MSG = "K리그 기록 데이터로 답할 수 있는 질문을 해주세요. 예: '2026 K리그1 득점 순위 TOP 5'"
LLM_FAIL_MSG ="AI 응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요."
LLM_RATE_LIMIT_MSG = "오늘 무료 AI 사용량을 다 썼습니다. 내일 다시 시도해 주세요."


def llm_error_kind(e: Exception) -> str:
    """LLM 호출 예외를 종류로만 구분한다 (예외 문장은 키·주소가 섞일 수 있어 보지 않음)."""
    status = getattr(e, "status_code", None)
    if status is None:
        status = getattr(getattr(e, "response", None), "status_code", None)
    name = type(e).__name__
    if status == 429 or name == "RateLimitError":
        return "rate_limit"
    if status in (401, 403) or name in ("AuthenticationError", "PermissionDeniedError"):
        return "auth"
    if name in ("APIConnectionError", "APITimeoutError", "ConnectionError", "Timeout", "TimeoutError",
                "ConnectError", "ReadTimeout"):
        return "network"
    return "other"


class RateLimitStop(Exception):
    """stop_on_rate_limit=True 일 때 429 를 오류 답변으로 바꾸지 않고 호출한 쪽으로 올린다 (평가용)."""

    def __init__(self, info: dict):
        super().__init__(f"rate limit: {info.get('limit_kind')}")
        self.info = info


def llm_fail_message(kind: str) -> str:
    return LLM_RATE_LIMIT_MSG if kind == "rate_limit" else LLM_FAIL_MSG


SUMMARY_SYSTEM = """너는 K리그 기록 해설가다. 주어진 조회 결과 표만 근거로 2~3문장 한국어로 답한다.
규칙: 표에 없는 숫자·사실을 만들지 않는다. 표가 잘렸으면 '상위 일부 기준'이라고 말한다.
퇴장: 표에 '직접퇴장'·'경고2회퇴장' 컬럼이 있으면 퇴장 횟수를 말할 때 항상 내역을 붙인다.
  예: "퇴장 36회 (직접 퇴장 25 + 경고 2회 퇴장 11)". 내역 컬럼이 없으면 내역을 지어내지 않는다."""


class Text2SQL:
    def __init__(self, query_db: Database, llm: LLM, *, max_rows: int = 500,
                 timeout_ms: int = 5000, max_retries: int = 2, max_question_len: int = 300,
                 examples: ExampleIndex | None = None,
                 entities: EntityResolver | None = None,
                 guard: Callable[..., GuardResult] = check_sql,
                 summarize: bool = True, focused_schema: bool = True,
                 summary_reasoning: str | None = "low", stop_on_rate_limit: bool = False):
        self.db = query_db
        self.llm = llm
        self.max_rows = max_rows
        self.timeout_ms = timeout_ms
        self.max_retries = max_retries
        self.max_question_len = max_question_len
        self.examples = examples or ExampleIndex()
        self.entities = entities
        self.guard = guard
        self.summarize = summarize
        # 토큰 절약: SQL 생성 첫 호출에는 질문과 관련된 표 설명만 넣는다 (자가 수정은 전체 설명서)
        self.focused_schema = focused_schema
        # 요약은 표를 문장으로 옮기는 일이라 추론 강도를 낮춘다 (정답 SQL 과 무관)
        self.summary_reasoning = summary_reasoning
        # 평가: 한도(429)에 걸리면 즉시 RateLimitStop 을 올려 다음 문항을 부르지 않게 한다
        self.stop_on_rate_limit = stop_on_rate_limit
        self.cache = TTLCache()
        dialect_name = "SQLite" if query_db.dialect == "sqlite" else "MySQL(TiDB)"
        self.dialect_name = dialect_name
        self.system_prompt = SYSTEM_TEMPLATE.format(dialect_name=dialect_name, schema=schema_prompt())
        self._prompt_cache: dict[tuple, str] = {}

    def load_examples(self, pairs: list[tuple[str, str]]) -> None:
        self.examples.fit([Example(q, s) for q, s in pairs])

    # ------------------------------------------------------------------
    def ask(self, question: str, history: list[Turn] | None = None) -> Answer:
        t0 = time.perf_counter()
        q = _CTRL.sub("", (question or "")).strip()
        if len(q) < 2:
            return Answer("rejected", q, summary="질문이 너무 짧습니다. 시즌·구단·선수를 넣어 물어봐 주세요.")
        if len(q) > self.max_question_len:
            return Answer("rejected", q, summary=f"질문은 {self.max_question_len}자 이내로 입력해 주세요.")

        prev = (history or [])[-1:] if history else []
        cache_key = hashlib.sha1((" ".join(q.split()) + "|" +
                                  "|".join(f"{t.question}>{t.sql}" for t in prev)).encode()).hexdigest()
        cached = self.cache.get(cache_key)
        if cached:
            ans = Answer(**{**cached.__dict__, "from_cache": True, "tokens": 0,
                            "latency_ms": int((time.perf_counter() - t0) * 1000)})
            return ans

        ans = self._run(q, prev)
        ans.latency_ms = int((time.perf_counter() - t0) * 1000)
        if ans.status in ("ok", "empty", "no_sql"):
            self.cache.put(cache_key, ans)
        return ans

    # ------------------------------------------------------------------
    def _system_for(self, tables: list[str] | None) -> str:
        if tables is None:
            return self.system_prompt
        key = tuple(tables)
        if key not in self._prompt_cache:
            self._prompt_cache[key] = SYSTEM_TEMPLATE.format(dialect_name=self.dialect_name,
                                                             schema=schema_prompt(list(tables)))
        return self._prompt_cache[key]

    def _pick_tables(self, q: str, prev: list[Turn]) -> list[str] | None:
        if not self.focused_schema:
            return None
        sqls = [e.sql for e, _ in self.examples.search(q)]
        if prev and prev[0].sql:
            sqls.append(prev[0].sql)   # '그 선수 도움은?' 처럼 직전 질문을 잇는 경우
        return relevant_tables(q, sqls)

    def _build_user_prompt(self, q: str, prev: list[Turn]) -> str:
        parts = []
        if self.entities:
            h = self.entities.hints(q)
            if h:
                parts.append(h)
        ex = format_examples(self.examples.search(q))
        if ex:
            parts.append(ex)
        if prev and prev[0].sql:
            parts.append(f"[직전 대화] 질문: {prev[0].question}\nSQL: {prev[0].sql}\n"
                         "현재 질문이 '그 선수', '그 팀'처럼 직전 대화를 가리키면 이어서 해석한다.")
        parts.append(f"[질문] {q}")
        return "\n\n".join(parts)

    def _run(self, q: str, prev: list[Turn]) -> Answer:
        ans = Answer("error", q)
        user_prompt = self._build_user_prompt(q, prev)
        ans.schema_tables = self._pick_tables(q, prev)

        try:
            reply = self.llm.complete(self._system_for(ans.schema_tables), user_prompt)
        except Exception as e:
            ans.llm_error = llm_error_kind(e)
            self._maybe_stop(ans.llm_error, e)
            ans.summary = llm_fail_message(ans.llm_error)
            ans.notes.append(f"LLM 오류: {type(e).__name__}")
            return ans
        ans.tokens += reply.tokens
        sql, _reason = parse_sql_reply(reply.text)
        if not sql:
            ans.status = "no_sql"
            # LLM 이 쓴 거절 이유는 보여주지 않는다 (DB 종류·내부 구조가 섞여 나올 수 있음). 고정 문구만.
            ans.summary = NO_SQL_MSG
            return ans

        last_error = ""
        for attempt in range(1, self.max_retries + 2):
            ans.attempts = attempt
            g = self.guard(sql, ALLOWED_TABLES, self.db.dialect, self.max_rows)
            if g.ok:
                ans.sql = g.sql
                try:
                    res = self.db.run_select(g.sql, self.max_rows, self.timeout_ms)
                    ans.df, ans.truncated = res.df, res.truncated
                    break
                except QueryTimeout as e:
                    last_error = str(e) + " 집계 범위를 줄이거나 JOIN 을 단순화하라."
                except Exception as e:
                    last_error = _clean_db_error(e)
            else:
                ans.sql = sql
                last_error = f"보안 검사 불통과: {g.reason}"

            if attempt > self.max_retries:
                ans.status = "blocked" if last_error.startswith("보안") else "error"
                ans.summary = "SQL을 실행하지 못했습니다. 질문을 조금 더 구체적으로 바꿔 보세요."
                ans.notes.append(last_error)
                return ans

            fix_prompt = (f"{user_prompt}\n\n[이전 SQL]\n{sql}\n\n[오류]\n{last_error}\n\n"
                          "오류 원인을 고쳐 같은 JSON 형식으로 다시 답하라.")
            try:
                reply = self.llm.complete(self.system_prompt, fix_prompt)
            except Exception as e:
                ans.llm_error = llm_error_kind(e)
                self._maybe_stop(ans.llm_error, e)
                ans.notes.append(f"LLM 오류: {type(e).__name__}")
                ans.summary = llm_fail_message(ans.llm_error)
                return ans
            ans.tokens += reply.tokens
            new_sql, _ = parse_sql_reply(reply.text)
            if not new_sql:
                ans.summary = "SQL을 고치지 못했습니다."
                ans.notes.append(last_error)
                return ans
            ans.notes.append(f"{attempt}차 오류 → 자가 수정: {last_error[:120]}")
            sql = new_sql

        if ans.df is None or ans.df.empty:
            ans.status = "empty"
            ans.summary = "조건에 맞는 기록이 없습니다. 시즌·리그·이름 표기를 확인해 보세요."
            return ans

        ans.status = "ok"
        ans.summary = self._summarize(q, ans)
        return ans

    def _summarize(self, q: str, ans: Answer) -> str:
        df = ans.df
        fallback = f"{len(df):,}건을 조회했습니다." + (" (상위 일부만 표시)" if ans.truncated else "")
        if not self.summarize:
            return fallback
        sample = df.head(20).to_csv(index=False)
        note = f"(전체 {len(df)}행 중 최대 20행, 잘림 여부: {ans.truncated})"
        try:
            reply = self.llm.complete(SUMMARY_SYSTEM, f"질문: {q}\n{note}\n표:\n{sample}",
                                      max_tokens=400, temperature=0.1,
                                      reasoning_effort=self.summary_reasoning)
            ans.tokens += reply.tokens
            return reply.text.strip() or fallback
        except Exception as e:
            self._maybe_stop(llm_error_kind(e), e)
            return fallback

    def _maybe_stop(self, kind: str, e: Exception) -> None:
        if self.stop_on_rate_limit and kind == "rate_limit":
            raise RateLimitStop(rate_limit_info(e)) from e


def _clean_db_error(e: Exception) -> str:
    """DB 에러에서 접속 정보 같은 민감한 부분 없이 원인만 남긴다."""
    msg = str(e)
    msg = re.sub(r"'[^']*'@'[^']*'", "'***'", msg)          # user@host
    msg = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "***", msg)  # IP
    msg = re.sub(r"\b[\w-]+(?:\.[\w-]+){2,}\b", "***", msg)   # 호스트 이름 (점 2개 이상)
    return f"{type(e).__name__}: {msg[:300]}"
