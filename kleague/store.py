"""앱 운영 데이터 저장소 (질문 로그·피드백·예시집·토큰 사용량).

기존 코드의 json 파일·txt 파일 저장은 Streamlit Cloud 재배포 때 사라졌다.
→ DB 테이블에 저장해 영구 보존. 질문 실행 계정과는 다른 계정을 쓴다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import Database

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]


def _now() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


class AppStore:
    def __init__(self, db: Database):
        self.db = db

    def init_schema(self) -> None:
        self.db.run_script((ROOT / "db" / "app_schema.sql").read_text(encoding="utf-8"))

    # -- 예시집 ---------------------------------------------------------------
    def approved_examples(self) -> list[tuple[str, str]]:
        return [(q, s) for q, s in self.db.query(
            "SELECT question, sql_text FROM app_sql_example WHERE approved = 1 ORDER BY id")]

    def pending_examples(self) -> list[tuple[int, str, str]]:
        return [tuple(r) for r in self.db.query(
            "SELECT id, question, sql_text FROM app_sql_example WHERE approved = 0 ORDER BY id")]

    def add_example(self, question: str, sql: str, approved: bool, source: str) -> None:
        existing = self.db.query(
            "SELECT id FROM app_sql_example WHERE question = ? AND sql_text = ?", (question, sql))
        if existing:
            return
        self.db.execute(
            "INSERT INTO app_sql_example (question, sql_text, approved, source, created_at) "
            "VALUES (?, ?, ?, ?, ?)", (question[:300], sql, int(approved), source, _now()))

    def set_example_approved(self, example_id: int, approved: bool) -> None:
        self.db.execute("UPDATE app_sql_example SET approved = ? WHERE id = ?",
                        (1 if approved else -1, example_id))

    # -- 로그 -----------------------------------------------------------------
    def log_query(self, question: str, sql: str | None, status: str, attempts: int,
                  rows: int, latency_ms: int, tokens: int, cache_hit: bool) -> int:
        return self.db.execute(
            "INSERT INTO app_query_log (created_at, question, sql_text, status, attempts, "
            "rows_returned, latency_ms, tokens, cache_hit) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_now(), question[:300], sql, status, attempts, rows, latency_ms, tokens, int(cache_hit)))

    def set_feedback(self, log_id: int, value: int) -> None:
        self.db.execute("UPDATE app_query_log SET feedback = ? WHERE id = ?", (value, log_id))

    def stats(self, days: int = 7) -> dict:
        since = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = self.db.query(
            "SELECT status, COUNT(*), AVG(latency_ms), SUM(cache_hit) FROM app_query_log "
            "WHERE created_at >= ? GROUP BY status", (since,))
        out = {"total": 0, "by_status": {}, "avg_latency_ms": 0, "cache_hits": 0}
        lat_sum = 0.0
        for status, cnt, avg_lat, hits in rows:
            out["by_status"][status] = int(cnt)
            out["total"] += int(cnt)
            lat_sum += float(avg_lat or 0) * int(cnt)
            out["cache_hits"] += int(hits or 0)
        if out["total"]:
            out["avg_latency_ms"] = int(lat_sum / out["total"])
        return out

    # -- 토큰 사용량 -----------------------------------------------------------
    def tokens_today(self) -> int:
        rows = self.db.query("SELECT tokens FROM app_usage WHERE usage_date = ?", (_today(),))
        return int(rows[0][0]) if rows else 0

    def add_usage(self, tokens: int) -> None:
        if tokens <= 0:
            return
        if not self.db.query("SELECT 1 FROM app_usage WHERE usage_date = ?", (_today(),)):
            self.db.upsert("app_usage", ["usage_date"],
                           [{"usage_date": _today(), "tokens": 0, "requests": 0}])
        self.db.execute("UPDATE app_usage SET tokens = tokens + ?, requests = requests + 1 "
                        "WHERE usage_date = ?", (tokens, _today()))
