"""정답률 평가 (Execution Accuracy).

정답 SQL과 AI가 만든 SQL을 각각 실행해 '결과 값'이 같은지 비교한다.
SQL 문자열이 달라도 결과가 같으면 정답 (별칭·컬럼 순서 무관).

비교 규칙 (관대한 실행 정확도)
  - 행 수가 같아야 함
  - 정답의 숫자 컬럼마다, AI 결과에 같은 값 집합을 가진 컬럼이 있어야 함
    (숫자 컬럼이 없으면 모든 컬럼을 비교)
  - order_matters: true 면 순서까지 같아야 함
  - 소수는 둘째 자리 반올림 후 비교
점수 두 가지
  - 엄격 점수: 위 규칙 그대로
  - 관대 점수: 숫자 컬럼에서 아래 두 가지 '표기 차이'만 봐준다. 그 외 숫자 차이는 오답.
      1) 반올림 차이: 두 값의 차이가 0.01 이하 (예: 0.5556 vs 0.556)
      2) 비율/퍼센트 표기: 한쪽을 100 으로 나눠 비율로 맞추면 1) 이 성립 (예: 0.556 vs 55.56)
         (컬럼의 모든 값에 같은 방식을 써야 함)
      3) 스코어 글자: AI 가 '2-5' 처럼 두 숫자를 한 글자로 합쳐 보여 주면 (앞 = 홈, 뒤 = 원정) 으로 읽어,
         정답의 홈 득점·원정 득점 컬럼과 행마다 짝째로 같을 때만 인정한다. 뒤바뀐 '5-2' 는 오답.
         컬럼의 모든 값이 '숫자-숫자'(또는 '숫자:숫자')이고, 정답에 홈·원정 컬럼이 하나씩 있을 때만.

사용:
    python eval/run_eval.py                 # .env 의 DB·LLM 으로 평가
    python eval/run_eval.py --mock          # LLM 없이 채점기 자체 점검 (정답 SQL을 그대로 답으로 사용)
    python eval/run_eval.py --no-fewshot    # 예시 검색 끄고 비교 (개선 효과 측정)
    python eval/run_eval.py --mock-fail-after 5   # 가짜 LLM 이 5번 답한 뒤 한도 초과 → 무효 실행 저장 점검
    python eval/run_eval.py --ids q01,q15,q26     # 일부 문항만
    python eval/run_eval.py --token-budget 100000 # 누적 토큰이 이 값을 넘기 전에 멈춤
결과: eval/runs/report_{fewshot|nofewshot}[_mock]_YYYYMMDD_HHMMSS[_invalid].md (+ .json)
  - 매번 새 이름으로 저장하고 기존 보고서는 덮어쓰지 않는다.
  - 한도 초과(429)가 한 번이라도 나오면 그 즉시 멈춘다 (다음 문항을 부르지 않음, SDK 자동 재시도도 끔).
    retry-after 와 한도 종류(분당/일일)를 보고서에 남기고, 이름 끝에 _invalid 를 붙여 따로 저장한다.
  - 그 밖의 LLM 오류가 3번 연속 나오거나 토큰 예산을 다 쓰면 역시 멈추고 _invalid 로 저장한다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict, deque
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kleague.config import load_settings  # noqa: E402
from kleague.db import Database  # noqa: E402
from kleague.entities import EntityResolver  # noqa: E402
from kleague.llm import FakeLLM, OpenAICompatLLM  # noqa: E402
from kleague.text2sql import RateLimitStop, Text2SQL  # noqa: E402


def _norm(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        f = float(v)
        return round(f, 2)
    except (TypeError, ValueError):
        return str(v).strip()


def _is_numeric(series: pd.Series) -> bool:
    vals = [v for v in series.tolist() if v is not None]
    if not vals:
        return False
    try:
        [float(v) for v in vals]
        return True
    except (TypeError, ValueError):
        return False


LENIENT_TOL = 0.01


def close_lists(gv: list, pv: list, order_matters: bool) -> bool:
    """관대 비교. 그대로 / 정답을 %→비율 / AI 답을 %→비율, 셋 중 하나로 모든 값 차이가 0.01 이하면 같다."""
    try:
        g = [float(v) for v in gv]
        p = [float(v) for v in pv]
    except (TypeError, ValueError):
        return False
    if len(g) != len(p):
        return False
    if not order_matters:
        g, p = sorted(g), sorted(p)
    for a_list, b_list in ((g, p), ([x / 100 for x in g], p), (g, [x / 100 for x in p])):
        if all(abs(a - b) <= LENIENT_TOL + 1e-9 for a, b in zip(a_list, b_list)):
            return True
    return False


_SCORE = re.compile(r"^\s*(\d+)\s*[-:]\s*(\d+)\s*$")


_HOME = re.compile(r"(?i)home|홈")
_AWAY = re.compile(r"(?i)away|원정")


def _side(name) -> str | None:
    """컬럼 이름으로 홈/원정 구분. 둘 다 들어 있거나 둘 다 없으면 None."""
    h, a = bool(_HOME.search(str(name))), bool(_AWAY.search(str(name)))
    return "home" if h and not a else "away" if a and not h else None


def split_score_columns(pred: pd.DataFrame) -> dict[str, list[tuple]]:
    """'2-5' 같은 스코어 글자 컬럼 → 행마다 (앞 숫자, 뒤 숫자). 모든 값이 '숫자-숫자' 인 컬럼만. 없으면 빈 dict."""
    out: dict[str, list[tuple]] = {}
    for c in pred.columns:
        vals = pred[c].tolist()
        if not vals:
            continue
        hits = [_SCORE.match(str(v)) if isinstance(v, str) else None for v in vals]
        if all(hits):
            out[c] = [(_norm(int(h.group(1))), _norm(int(h.group(2)))) for h in hits]
    return out


def score_text_columns(gold: pd.DataFrame, pred: pd.DataFrame, order_matters: bool) -> dict[str, list]:
    """관대 채점용. 스코어 글자를 정답의 홈 득점·원정 득점 컬럼으로 인정할 수 있으면 그 두 컬럼을 돌려준다.

    가장 보수적으로: 정답에 홈 컬럼 1개·원정 컬럼 1개(이름에 home/홈, away/원정)가 있고,
    스코어 글자의 (앞, 뒤) 가 행마다 (홈, 원정) 과 짝째로 같을 때만 인정한다 (앞 = 홈, 뒤 = 원정).
    홈·원정이 뒤바뀐 '5-2' 나, 행끼리 짝이 섞인 답은 인정하지 않는다. 인정 못 하면 빈 dict (아무것도 안 늘림)."""
    gold_num = [c for c in gold.columns if _is_numeric(gold[c])]
    home = [c for c in gold_num if _side(c) == "home"]
    away = [c for c in gold_num if _side(c) == "away"]
    if len(home) != 1 or len(away) != 1:
        return {}
    gold_pairs = list(zip([_norm(v) for v in gold[home[0]]], [_norm(v) for v in gold[away[0]]]))
    for c, pairs in split_score_columns(pred).items():
        same = pairs == gold_pairs if order_matters else Counter(map(str, pairs)) == Counter(map(str, gold_pairs))
        if same:
            return {f"{c}#홈": [p[0] for p in pairs], f"{c}#원정": [p[1] for p in pairs]}
    return {}


def _home_away_columns(gold: pd.DataFrame, gold_num: list) -> tuple | None:
    """정답 숫자 컬럼 중 홈 컬럼 1개·원정 컬럼 1개가 있으면 (홈, 원정). 아니면 None (기존 비교 그대로)."""
    home = [c for c in gold_num if _side(c) == "home"]
    away = [c for c in gold_num if _side(c) == "away"]
    return (home[0], away[0]) if len(home) == 1 and len(away) == 1 else None


def _pair_found(gold: pd.DataFrame, pair: tuple, pred_cols: dict, order_matters: bool) -> bool:
    """AI 컬럼 두 개(P1, P2)를 골라 줄마다 (P1, P2) == (홈, 원정) 인지 본다.
    이름으로 홈·원정을 알 수 있으면 지킨다: P1 이 '원정' 컬럼이거나 P2 가 '홈' 컬럼이면 안 된다.
    (이름이 없는 컬럼은 어느 쪽인지 알 수 없어 값으로만 본다)"""
    gp = list(zip([_norm(v) for v in gold[pair[0]]], [_norm(v) for v in gold[pair[1]]]))
    for n1, v1 in pred_cols.items():
        if _side(n1) == "away":
            continue
        for n2, v2 in pred_cols.items():
            if n2 == n1 or _side(n2) == "home":
                continue
            pp = list(zip(v1, v2))
            if (pp == gp) if order_matters else Counter(map(str, pp)) == Counter(map(str, gp)):
                return True
    return False


def results_match(gold: pd.DataFrame, pred: pd.DataFrame, order_matters: bool,
                  lenient: bool = False) -> bool:
    if pred is None or len(gold) != len(pred):
        return False
    gold_num = [c for c in gold.columns if _is_numeric(gold[c])]
    gold_cols = gold_num or list(gold.columns)
    pred_cols = {c: [_norm(v) for v in pred[c].tolist()] for c in pred.columns}
    if lenient:
        pred_cols.update(score_text_columns(gold, pred, order_matters))
    # 홈·원정 짝: 정답에 홈 컬럼·원정 컬럼이 하나씩 있으면 두 컬럼은 따로가 아니라 '같은 줄의 (홈, 원정) 짝'으로 비교한다
    pair = _home_away_columns(gold, gold_num)
    if pair:
        if not _pair_found(gold, pair, pred_cols, order_matters):
            return False
        gold_cols = [c for c in gold_cols if c not in pair]
    for gc in gold_cols:
        gv = [_norm(v) for v in gold[gc].tolist()]
        found = False
        for pv in pred_cols.values():
            if order_matters and pv == gv:
                found = True
            elif not order_matters and Counter(map(str, pv)) == Counter(map(str, gv)):
                found = True
            elif lenient and gold_num and close_lists(gv, pv, order_matters):
                found = True
            if found:
                break
        if not found:
            return False
    return True


class TokenPacer:
    """분당 토큰 한도(TPM) 안에서 질문을 보낸다. 한도를 넘기면 LLM 서비스가 429 로 거절하고
    SDK 가 15~20초 기다렸다 재시도해서, 응답 시간이 모델 속도가 아니라 '줄 서기' 시간이 된다."""

    INITIAL_ESTIMATE = 3600   # 첫 문항 어림값이자 바닥값. Groq 는 요청 때 '입력 + 최대 출력' 을 미리 잡아 실제 사용량보다 크게 센다

    def __init__(self, tpm: int, window: float = 60.0, recent: int = 10):
        self.tpm, self.window = tpm, window
        self.used: deque[tuple[float, int]] = deque()
        self.recent: deque[int] = deque(maxlen=recent)   # 최근 문항 토큰 (어림값 계산용)

    @property
    def last(self) -> int:
        """다음 요청 예상 토큰 = 최근 문항 중 가장 큰 값 (바닥값 INITIAL_ESTIMATE).
        직전 문항만 보면 짧은 문항 뒤에 어림값이 작아져 분당 한도를 넘는다 (2026-11-05 q36 429)."""
        return max([self.INITIAL_ESTIMATE, *self.recent])

    def wait(self) -> float:
        if self.tpm <= 0:
            return 0.0
        waited = 0.0
        while True:
            now = time.monotonic()
            while self.used and now - self.used[0][0] > self.window:
                self.used.popleft()
            if sum(t for _, t in self.used) + self.last <= self.tpm or not self.used:
                return waited
            pause = self.window - (now - self.used[0][0]) + 0.5
            time.sleep(pause)
            waited += pause

    def add(self, tokens: int) -> None:
        if tokens:
            self.used.append((time.monotonic(), tokens))
            self.recent.append(tokens)


def describe_model(model: str, base_url: str) -> str:
    """보고서용 모델 표기. 모델 개발사와 추론 서비스를 구분해 적는다."""
    from urllib.parse import urlparse
    host = urlparse(base_url or "").hostname or ""
    service = {"api.groq.com": "Groq 추론 서비스", "api.openai.com": "OpenAI API",
               "generativelanguage.googleapis.com": "Google Gemini API"}.get(host, host or "알 수 없음")
    if model.startswith("openai/gpt-oss"):
        name = f"OpenAI {model.split('/', 1)[1]}(오픈 모델)"
    else:
        name = model
    return f"{name}, {service} (`{model}`)"


class _MockResponse:
    status_code = 429
    headers = {"retry-after": "120", "x-ratelimit-remaining-tokens": "0"}


class MockRateLimitError(Exception):
    """--mock-fail-after 용 가짜 '한도 초과' 오류 (실제 호출 없음). Groq 응답 모양을 흉내 낸다."""
    status_code = 429
    response = _MockResponse()
    body = {"error": {"message": "Rate limit reached for model `m` in organization `org_secret` "
                                 "service tier `on_demand` on tokens per day (TPD): "
                                 "Limit 200000, Used 199000, Requested 3000.",
                      "code": "rate_limit_exceeded"}}


class FailingFakeLLM(FakeLLM):
    """앞의 fail_after 번은 정답을 돌려주고, 그 뒤로는 한도 초과처럼 실패하는 가짜 LLM."""

    def __init__(self, replies: list[str], fail_after: int):
        super().__init__(replies)
        self.fail_after = fail_after

    def complete(self, system, user, max_tokens=1200, temperature=0.0, reasoning_effort=None):
        if len(self.calls) >= self.fail_after:
            self.calls.append((system, user))
            raise MockRateLimitError("mock rate limit")
        return super().complete(system, user, max_tokens, temperature, reasoning_effort)


DEFAULT_OUT_DIR = ROOT / "eval" / "runs"


def report_path(out_dir: Path, fewshot: bool, mock: bool, when: datetime, invalid: bool = False) -> Path:
    """날짜·시각을 붙인 새 이름. 같은 이름이 있으면 _2, _3 … 을 붙여 절대 덮어쓰지 않는다."""
    stem = (f"report_{'fewshot' if fewshot else 'nofewshot'}{'_mock' if mock else ''}"
            f"_{when:%Y%m%d_%H%M%S}{'_invalid' if invalid else ''}")
    path, n = out_dir / f"{stem}.md", 2
    while path.exists() or path.with_suffix(".json").exists():
        path, n = out_dir / f"{stem}_{n}.md", n + 1
    return path


def _write_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as f:  # 'x' = 이미 있으면 실패 (덮어쓰기 방지)
        f.write(text)


def build_summary(rows: list[dict], mode: str, fewshot: bool, tpm: int, effort: str) -> dict:
    total = len(rows)
    correct = sum(r["correct"] for r in rows)
    correct_l = sum(r["correct_lenient"] for r in rows)
    by_cat = defaultdict(lambda: [0, 0])
    for r in rows:
        by_cat[r["category"]][0] += r["correct"]
        by_cat[r["category"]][1] += 1
    return {
        "mode": mode, "fewshot": fewshot,
        "accuracy": round(correct / total, 3) if total else 0, "correct": correct, "total": total,
        "accuracy_lenient": round(correct_l / total, 3) if total else 0, "correct_lenient": correct_l,
        "self_corrected": sum(1 for r in rows if r["correct"] and r["attempts"] > 1),
        "avg_latency_ms": int(sum(r["latency_ms"] for r in rows) / total) if total else 0,
        "avg_tokens": int(sum(r["tokens"] for r in rows) / total) if total else 0,
        "tpm": tpm, "reasoning_effort": effort or "기본값",
        "by_category": {k: f"{v[0]}/{v[1]}" for k, v in by_cat.items()},
    }


def render_md(summary: dict, rows: list[dict], when: datetime, invalid_note: str = "") -> str:
    lines = ["# Text-to-SQL 평가 결과", ""]
    if invalid_note:
        lines += [f"> ⚠️ **무효 결과**: {invalid_note} 정답률로 쓰지 말 것.", ""]
    lines += [
        f"- 측정 시각: {when:%Y-%m-%d %H:%M:%S}",
        f"- 모델: {summary['mode']} / 예시 검색: {'켜짐' if summary['fewshot'] else '꺼짐'}",
        f"- **정답률(엄격): {summary['accuracy'] * 100:.1f}% ({summary['correct']}/{summary['total']})**",
        f"- 정답률(관대: 반올림 0.01 이내·비율/퍼센트 표기 차이만 인정): "
        f"{summary['accuracy_lenient'] * 100:.1f}% ({summary['correct_lenient']}/{summary['total']})",
        f"- 자가 수정으로 정답이 된 문항: {summary['self_corrected']}개",
        f"- 평균 응답 시간: {summary['avg_latency_ms']:,}ms / 평균 토큰: {summary['avg_tokens']:,}",
        f"- 속도 조절: 분당 {summary['tpm']:,} 토큰 이내로 전송 (기다린 시간은 응답 시간에서 뺌) / "
        f"추론 강도: {summary['reasoning_effort']}",
        f"- 문항: {len(summary.get('question_ids') or [])}개 ({', '.join(summary.get('question_ids') or [])}) / "
        f"쓴 토큰 합계: {summary.get('used_tokens', 0):,}", "",
        "| 카테고리 | 정답 |", "|---|---|",
        *[f"| {k} | {v} |" for k, v in summary["by_category"].items()], "",
        "| ID | 질문 | 엄격 | 관대 | 상태 | 시도 | 응답(ms) |", "|---|---|---|---|---|---|---|",
        *[f"| {r['id']} | {r['question']} | {'✅' if r['correct'] else '❌'} | "
          f"{'✅' if r['correct_lenient'] else '❌'} | {r['status']} | {r['attempts']} | {r['latency_ms']:,} |"
          for r in rows],
    ]
    return "\n".join(lines) + "\n"


def run(mock: bool, no_fewshot: bool, out_dir: Path = DEFAULT_OUT_DIR, tpm: int | None = None, *,
        mock_fail_after: int | None = None, now: datetime | None = None,
        ids: list[str] | None = None, token_budget: int = 0) -> dict:
    """평가 실행 → 새 이름의 보고서(.md + .json) 저장. 반환 summary 에 report_path·invalid 포함."""
    s = load_settings()
    db = Database(s.query_db_url, read_only=True)
    cases = yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text(encoding="utf-8"))
    when = now or datetime.now()
    if ids:
        unknown = set(ids) - {c["id"] for c in cases}
        if unknown:
            raise SystemExit(f"없는 문항 ID: {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c["id"] in set(ids)]

    if mock_fail_after is not None:
        mock = True
    if mock:
        replies = [json.dumps({"sql": c["sql"]}, ensure_ascii=False) for c in cases]
        llm = FakeLLM(replies) if mock_fail_after is None else FailingFakeLLM(replies, mock_fail_after)
    else:
        if not s.llm_api_key:
            raise SystemExit("LLM_API_KEY 가 없습니다. --mock 으로 채점기만 점검할 수 있습니다.")
        llm = OpenAICompatLLM(s.llm_api_key, s.llm_base_url, s.llm_model, s.llm_timeout_sec,
                              s.llm_reasoning_effort, max_retries=0)  # 429 를 몰래 재시도하지 않음
    pacer = TokenPacer(0 if mock else (s.llm_tpm if tpm is None else tpm))

    engine = Text2SQL(db, llm, max_rows=s.max_rows, timeout_ms=s.query_timeout_ms,
                      entities=EntityResolver(db), summarize=False,
                      stop_on_rate_limit=True)
    if not no_fewshot:
        seeds = yaml.safe_load((ROOT / "eval" / "seed_examples.yaml").read_text(encoding="utf-8"))
        engine.load_examples([(e["question"], e["sql"]) for e in seeds])

    rows = []
    llm_fail_streak = 0
    stopped_at = None
    stop_reason = ""
    rate_limit: dict | None = None
    used_tokens = 0
    last_tokens = 0
    for c in cases:
        # 토큰 예산: 직전 문항만큼 더 쓰면 넘을 것 같으면 부르기 전에 멈춘다
        if token_budget and used_tokens + last_tokens > token_budget:
            stopped_at, stop_reason = c["id"], "budget"
            break
        gold = db.run_select(c["sql"], 10_000, 10_000).df
        waited = pacer.wait()
        t0 = time.perf_counter()
        try:
            ans = engine.ask(c["question"])
        except RateLimitStop as e:
            # 이 문항은 채점하지 않는다 (오답이 아니라 측정 불가). 다음 문항도 부르지 않는다.
            stopped_at, stop_reason, rate_limit = c["id"], "rate_limit", e.info
            break
        pacer.add(ans.tokens)
        used_tokens += ans.tokens
        last_tokens = ans.tokens
        om = c.get("order_matters", False)
        ok = ans.status == "ok" and results_match(gold, ans.df, om)
        ok_l = ans.status == "ok" and results_match(gold, ans.df, om, lenient=True)
        rows.append({"id": c["id"], "category": c["category"], "question": c["question"],
                     "correct": ok, "correct_lenient": ok_l, "status": ans.status, "attempts": ans.attempts,
                     "latency_ms": int((time.perf_counter() - t0) * 1000), "tokens": ans.tokens,
                     "paced_wait_ms": int(waited * 1000), "llm_error": ans.llm_error,
                     "sql": ans.sql})
        print(f"{'✅' if ok else ('🟡' if ok_l else '❌')} {c['id']} {c['question']}  "
              f"({ans.status}, {ans.attempts}회)", flush=True)
        # LLM 호출 자체가 계속 실패하면(예: 하루 토큰 한도 초과) 오답이 아니라 '측정 불가' → 중단
        llm_fail_streak = llm_fail_streak + 1 if any(n.startswith("LLM 오류") for n in ans.notes) else 0
        if llm_fail_streak >= 3:
            stopped_at, stop_reason = c["id"], "llm_error"
            break

    invalid = stopped_at is not None
    mode = "mock" if mock else describe_model(s.llm_model, s.llm_base_url)
    summary = build_summary(rows, mode, not no_fewshot, pacer.tpm, s.llm_reasoning_effort)
    summary["used_tokens"] = used_tokens
    summary["question_ids"] = [c["id"] for c in cases]
    note = ""
    if invalid:
        done = f"전체 {len(cases)}문항 중 {len(rows)}문항까지만 채점."
        if stop_reason == "rate_limit":
            ri = rate_limit or {}
            note = (f"{stopped_at} 에서 한도 초과(429)로 즉시 멈춤 — 한도 종류: {ri.get('limit_name')}"
                    f"({ri.get('limit_kind')}), retry-after: {ri.get('retry_after') or '없음'}초. "
                    f"{done} {stopped_at} 와 그 뒤 문항은 측정 불가.")
        elif stop_reason == "budget":
            note = (f"토큰 예산 {token_budget:,} 에 닿기 전에 {stopped_at} 에서 멈춤 (쓴 토큰 {used_tokens:,}). "
                    f"{done} 나머지는 측정 불가.")
        else:
            kinds = sorted({r["llm_error"] for r in rows if r["llm_error"]})
            note = (f"LLM 호출이 3번 연속 실패해 {stopped_at} 에서 멈춤 (오류 종류: {', '.join(kinds)}). "
                    f"{done} 실패 문항은 오답이 아니라 측정 불가.")
        summary.update({"invalid": True, "stopped_at": stopped_at, "stop_reason": stop_reason,
                        "rate_limit": rate_limit, "planned_total": len(cases)})
    else:
        summary["invalid"] = False
    summary["measured_at"] = when.strftime("%Y-%m-%d %H:%M:%S")

    out = report_path(Path(out_dir), not no_fewshot, mock, when, invalid)
    _write_new(out, render_md(summary, rows, when, note))
    _write_new(out.with_suffix(".json"),
               json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2))
    summary["report_path"] = str(out)
    if invalid:
        print(f"\n⚠️ 무효 실행: {note}\n→ {out} (기존 보고서는 건드리지 않음)")
    else:
        print(f"\n정답률 엄격 {summary['accuracy'] * 100:.1f}% / 관대 {summary['accuracy_lenient'] * 100:.1f}% → {out}")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="LLM 없이 정답 SQL 을 답으로 써서 채점기 점검")
    ap.add_argument("--mock-fail-after", type=int, default=None,
                    help="가짜 LLM 이 N번 답한 뒤 한도 초과처럼 실패 (무효 실행 저장 점검용, --mock 포함)")
    ap.add_argument("--no-fewshot", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="보고서 폴더 (파일 이름은 날짜·시각으로 자동)")
    ap.add_argument("--tpm", type=int, default=None, help="분당 토큰 한도 (기본: LLM_TPM 또는 8000, 0=조절 안 함)")
    ap.add_argument("--ids", default="", help="이 문항만 (쉼표로 구분, 예: q01,q15)")
    ap.add_argument("--token-budget", type=int, default=0, help="누적 토큰 상한 (0=없음)")
    a = ap.parse_args(argv)
    ids = [x.strip() for x in a.ids.split(",") if x.strip()] or None
    summary = run(a.mock, a.no_fewshot, Path(a.out_dir), a.tpm, mock_fail_after=a.mock_fail_after,
                  ids=ids, token_budget=a.token_budget)
    return 2 if summary["invalid"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
