"""평가 정답 SQL·예시 SQL 점검 (LLM 호출 없음, DB 읽기 전용).

    python eval/check_gold.py          # .env 의 QUERY_DB_URL 로 점검
                                       # → eval/gold_check.md (실제 기록 값 포함, 공개 안 함)
                                       # → eval/gold_check_public.md (질문 번호·행 수·통과 여부만, 공개용)

점검 내용
  1) 평가 문항마다 정답 SQL 을 실행해 결과 행 수와 첫 줄을 기록 (0행이면 실패)
  2) 예시 SQL 도 실행 + SQL 가드(sqlite·mysql 문법) 통과 여부
  3) 평가 질문과 예시 질문이 겹치지 않는지 (공백·문장부호를 뺀 문장이 같으면 겹침)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kleague.config import load_settings  # noqa: E402
from kleague.db import Database  # noqa: E402
from kleague.schema_docs import ALLOWED_TABLES  # noqa: E402
from kleague.sql_guard import check_sql  # noqa: E402


def _key(q: str) -> str:
    return re.sub(r"[\s\?\!\.,]", "", q)


def _first(df) -> str:
    if df is None or df.empty:
        return "-"
    return " | ".join(str(v) for v in df.iloc[0].tolist()).replace("\n", " ")[:120]


def check(db: Database) -> tuple[str, int, str]:
    """→ (전체 보고서, 문제 수, 공개용 요약). 전체 보고서에는 실제 기록 값(첫 줄)이 들어 있어 공개하지 않는다."""
    cases = yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text(encoding="utf-8"))
    seeds = yaml.safe_load((ROOT / "eval" / "seed_examples.yaml").read_text(encoding="utf-8"))
    problems = 0
    lines = ["# 정답 SQL 점검", "", f"- 평가 문항 {len(cases)}개 / 예시 {len(seeds)}개", "",
             "## 평가 문항 (정답 SQL 실행 결과)", "",
             "| ID | 질문 | 행 수 | 첫 줄 |", "|---|---|---|---|"]
    pub = ["# 정답 SQL 점검 (공개용 요약)", "",
           "> 질문 번호·결과 행 수·통과 여부만 적는다. 실제 기록 값이 든 전체 결과는 공개하지 않는다.", "",
           f"- 평가 문항 {len(cases)}개 / 예시 {len(seeds)}개", "",
           "## 평가 문항", "", "| ID | 행 수 | 통과 |", "|---|---|---|"]
    for c in cases:
        try:
            df = db.run_select(c["sql"], 10_000, 10_000).df
            n, first = len(df), _first(df)
        except Exception as e:  # noqa: BLE001
            n, first = "오류", type(e).__name__
        bad = n in ("오류", 0)
        problems += bad
        lines.append(f"| {c['id']} | {c['question']} | {n} | {first} |")
        pub.append(f"| {c['id']} | {n} | {'❌' if bad else '✅'} |")

    lines += ["", "## 예시 SQL (실행 + SQL 가드)", "",
              "| # | 질문 | 행 수 | 가드 sqlite | 가드 mysql |", "|---|---|---|---|---|"]
    pub += ["", "## 예시 SQL (실행 + SQL 가드)", "", "| # | 행 수 | 통과 |", "|---|---|---|"]
    for i, s in enumerate(seeds, 1):
        g1 = check_sql(s["sql"], ALLOWED_TABLES, "sqlite", 500)
        g2 = check_sql(s["sql"], ALLOWED_TABLES, "mysql", 500)
        try:
            n = len(db.run_select(g1.sql if g1.ok else s["sql"], 10_000, 10_000).df)
        except Exception as e:  # noqa: BLE001
            n = f"오류 {type(e).__name__}"
        bad = not (g1.ok and g2.ok) or not isinstance(n, int) or n == 0
        problems += bad
        lines.append(f"| {i} | {s['question']} | {n} | {'통과' if g1.ok else g1.reason} | "
                     f"{'통과' if g2.ok else g2.reason} |")
        pub.append(f"| {i} | {n} | {'❌' if bad else '✅'} |")

    eval_keys = {_key(c["question"]): c["id"] for c in cases}
    dup = [(eval_keys[_key(s["question"])], s["question"]) for s in seeds if _key(s["question"]) in eval_keys]
    problems += len(dup)
    lines += ["", f"## 평가·예시 질문 겹침: {len(dup)}건"] + [f"- {i}: {q}" for i, q in dup]
    lines += ["", f"**문제 {problems}건**"]
    pub += ["", f"## 평가·예시 질문 겹침: {len(dup)}건"] + [f"- {i}" for i, _ in dup]
    pub += ["", f"**문제 {problems}건**"]
    return "\n".join(lines) + "\n", problems, "\n".join(pub) + "\n"


def main() -> int:
    s = load_settings()
    text, problems, public = check(Database(s.query_db_url, read_only=True))
    out = ROOT / "eval" / "gold_check.md"            # 실제 기록 값 포함 → 공개하지 않음
    out.write_text(text, encoding="utf-8")
    pub = ROOT / "eval" / "gold_check_public.md"     # 공개용 요약
    pub.write_text(public, encoding="utf-8")
    print(f"문제 {problems}건 → {out} (공개 안 함) / 공개용 요약 → {pub}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
