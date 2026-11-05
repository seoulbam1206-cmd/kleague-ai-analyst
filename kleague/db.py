"""DB 접근 계층 (SQLite / TiDB·MySQL 공용, 순수 DB-API).

보안 3중 방어 중 'DB 계층' 담당:
  - 질문 실행은 읽기 전용 연결로만 (SQLite: mode=ro + authorizer, MySQL: READ ONLY 트랜잭션 + SELECT 전용 계정)
  - 쿼리 시간 제한 (SQLite: progress handler, MySQL/TiDB: max_execution_time)
  - 결과 행 수 상한 (fetchmany)
"""
from __future__ import annotations

import queue
import re
import sqlite3
import ssl
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd


class QueryTimeout(Exception):
    pass


@dataclass
class QueryResult:
    df: pd.DataFrame
    truncated: bool
    elapsed_ms: int


# ---------------------------------------------------------------------------
# 연결 정보 파싱
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DbTarget:
    kind: str  # "sqlite" | "mysql"
    sqlite_path: str = ""
    host: str = ""
    port: int = 4000
    user: str = ""
    password: str = ""
    database: str = ""
    use_ssl: bool = True


def parse_url(url: str) -> DbTarget:
    """sqlite:///path.db  또는  mysql://user:pass@host:4000/db?ssl=true"""
    if url.startswith("sqlite:///"):
        return DbTarget(kind="sqlite", sqlite_path=url[len("sqlite:///"):])
    u = urlparse(url)
    if u.scheme not in ("mysql", "mysql+pymysql", "tidb"):
        raise ValueError("지원하지 않는 DB URL 형식입니다 (sqlite:/// 또는 mysql://)")
    qs = parse_qs(u.query)
    return DbTarget(
        kind="mysql",
        host=u.hostname or "",
        port=u.port or 4000,
        user=unquote(u.username or ""),
        password=unquote(u.password or ""),
        database=(u.path or "/").lstrip("/"),
        use_ssl=qs.get("ssl", ["true"])[0].lower() != "false",
    )


# ---------------------------------------------------------------------------
# SQLite 읽기 전용 가드 (데모/테스트 모드)
# ---------------------------------------------------------------------------
_SQLITE_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}


def _sqlite_authorizer(action, arg1, arg2, dbname, source):
    # sqlite_master 등 내부 테이블 조회 차단
    if action == sqlite3.SQLITE_READ and arg1 and arg1.lower().startswith("sqlite_"):
        return sqlite3.SQLITE_DENY
    if action in _SQLITE_ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
class Database:
    """간단한 연결 풀을 가진 DB 래퍼. Streamlit 멀티스레드에서 안전하게 동작."""

    def __init__(self, url: str, read_only: bool, pool_size: int = 4):
        self.target = parse_url(url)
        self.read_only = read_only
        self._pool: queue.LifoQueue = queue.LifoQueue(maxsize=pool_size)
        self._lock = threading.Lock()

    @property
    def dialect(self) -> str:
        return "sqlite" if self.target.kind == "sqlite" else "mysql"

    # -- 연결 생성 -----------------------------------------------------------
    def _new_conn(self):
        t = self.target
        if t.kind == "sqlite":
            if self.read_only:
                conn = sqlite3.connect(f"file:{t.sqlite_path}?mode=ro", uri=True,
                                       check_same_thread=False)
                conn.set_authorizer(_sqlite_authorizer)
            else:
                conn = sqlite3.connect(t.sqlite_path, check_same_thread=False)
            return conn

        import pymysql  # 지연 import (SQLite만 쓸 때는 불필요)

        conn = pymysql.connect(
            host=t.host, port=t.port, user=t.user, password=t.password,
            database=t.database, charset="utf8mb4",
            ssl=ssl.create_default_context() if t.use_ssl else None,
            connect_timeout=5, read_timeout=15, write_timeout=15,
            autocommit=not self.read_only,
        )
        return conn

    @contextmanager
    def connection(self):
        try:
            conn = self._pool.get_nowait()
            if self.target.kind != "sqlite":
                try:  # TiDB Starter 는 오래 쉰 연결을 끊는다 → 꺼내 쓸 때 살아있는지 확인
                    conn.ping(reconnect=True)
                except Exception:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn = self._new_conn()
        except queue.Empty:
            conn = self._new_conn()
        ok = True
        try:
            yield conn
        except Exception:
            ok = False
            raise
        finally:
            if ok:
                try:
                    self._pool.put_nowait(conn)
                except queue.Full:
                    conn.close()
            else:  # 오류 난 연결은 재사용하지 않음
                try:
                    conn.close()
                except Exception:
                    pass

    # -- 읽기 전용 질의 -------------------------------------------------------
    def run_select(self, sql: str, max_rows: int, timeout_ms: int) -> QueryResult:
        start = time.perf_counter()
        with self.connection() as conn:
            if self.target.kind == "sqlite":
                deadline = start + timeout_ms / 1000

                def _guard():
                    return 1 if time.perf_counter() > deadline else 0

                conn.set_progress_handler(_guard, 10_000)
                try:
                    cur = conn.execute(sql)
                    rows = cur.fetchmany(max_rows + 1)
                except sqlite3.OperationalError as e:
                    if "interrupted" in str(e):
                        raise QueryTimeout(f"쿼리가 {timeout_ms}ms 를 넘어 중단되었습니다.") from e
                    raise
                finally:
                    conn.set_progress_handler(None, 0)
                cols = [d[0] for d in cur.description or []]
            else:
                cur = conn.cursor()
                try:
                    for setup in ("SET SESSION TRANSACTION READ ONLY",
                                  f"SET SESSION max_execution_time = {int(timeout_ms)}"):
                        try:  # DB 버전에 따라 미지원이어도 실행은 계속 (권한 분리가 최종 방어선)
                            cur.execute(setup)
                        except Exception:
                            pass
                    cur.execute(sql)
                    rows = cur.fetchmany(max_rows + 1)
                    cols = [d[0] for d in cur.description or []]
                except Exception as e:
                    if "max_execution_time" in str(e).lower() or "3024" in str(e):
                        raise QueryTimeout(f"쿼리가 {timeout_ms}ms 를 넘어 중단되었습니다.") from e
                    raise
                finally:
                    cur.close()
                    conn.rollback()

        truncated = len(rows) > max_rows
        df = pd.DataFrame(list(rows[:max_rows]), columns=cols)
        return QueryResult(df=df, truncated=truncated,
                           elapsed_ms=int((time.perf_counter() - start) * 1000))

    # -- 쓰기 (수집기 / 앱 로그 전용) -----------------------------------------
    def execute(self, sql: str, params: tuple | list = ()) -> int:
        if self.read_only:
            raise PermissionError("읽기 전용 연결에서는 쓰기를 할 수 없습니다.")
        sql = self._adapt_params(sql)
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            last = cur.lastrowid
            conn.commit()
            cur.close()
            return last or 0

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        if self.read_only:
            raise PermissionError("읽기 전용 연결에서는 쓰기를 할 수 없습니다.")
        if not rows:
            return
        sql = self._adapt_params(sql)
        with self.connection() as conn:
            cur = conn.cursor()
            cur.executemany(sql, rows)
            conn.commit()
            cur.close()

    def query(self, sql: str, params: tuple | list = ()) -> list[tuple]:
        """내부용 파라미터 바인딩 조회 (사용자 SQL 아님)."""
        sql = self._adapt_params(sql)
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
            cur.close()
            return list(rows)

    def run_script(self, script: str) -> None:
        """DDL 스크립트 실행. 이미 있는 인덱스 등은 건너뜀."""
        if self.target.kind == "sqlite":
            script = script.replace("AUTO_INCREMENT", "AUTOINCREMENT")
        statements = [s.strip() for s in _strip_sql_comments(script).split(";") if s.strip()]
        for stmt in statements:
            try:
                self.execute(stmt)
            except Exception as e:  # 인덱스 중복 등
                msg = str(e).lower()
                if "already exists" in msg or "duplicate" in msg:
                    continue
                raise

    def _adapt_params(self, sql: str) -> str:
        # 내부 코드는 '?' 로 작성 → MySQL 은 '%s'
        return sql if self.target.kind == "sqlite" else sql.replace("?", "%s")

    def upsert(self, table: str, key_cols: list[str], rows: list[dict]) -> int:
        """dict 리스트를 PK 기준으로 넣거나 갱신. 테이블/컬럼명은 내부 상수만 사용."""
        if not rows:
            return 0
        cols = list(rows[0].keys())
        for name in [table, *cols]:
            if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
                raise ValueError(f"잘못된 식별자: {name}")
        placeholders = ", ".join(["?"] * len(cols))
        col_list = ", ".join(cols)
        non_key = [c for c in cols if c not in key_cols]
        if self.target.kind == "sqlite":
            update = ", ".join(f"{c}=excluded.{c}" for c in non_key) or f"{key_cols[0]}={key_cols[0]}"
            sql = (f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
                   f"ON CONFLICT({', '.join(key_cols)}) DO UPDATE SET {update}")
        else:
            update = ", ".join(f"{c}=VALUES({c})" for c in non_key) or f"{key_cols[0]}={key_cols[0]}"
            sql = (f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
                   f"ON DUPLICATE KEY UPDATE {update}")
        self.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])
        return len(rows)


def _strip_sql_comments(script: str) -> str:
    return "\n".join(line for line in script.splitlines() if not line.strip().startswith("--"))
