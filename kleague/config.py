"""설정 로딩.

우선순위: 환경변수(.env) → Streamlit secrets → 기본값.
비밀값(키·비밀번호)은 코드/로그 어디에도 찍지 않는다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .viewer import parse_admin_emails

try:  # .env 는 로컬 개발용 (배포 환경에선 secrets 사용)
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _secret(name: str, default: str | None = None) -> str | None:
    val = os.getenv(name)
    if val:
        return val
    try:
        import streamlit as st  # 선택 의존성

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return default


@dataclass(frozen=True)
class Settings:
    # --- DB ---
    # 질문 실행용: 반드시 SELECT 권한만 있는 계정 (db/readonly_user.sql 참고)
    query_db_url: str = field(default="", repr=False)   # 비밀번호 포함 → repr 에 안 나오게
    # 로그/피드백/예시 저장용: app_* 테이블에만 쓰기 권한
    app_db_url: str = field(default="", repr=False)
    sql_dialect: str = "mysql"  # TiDB = mysql 호환

    # --- LLM (OpenAI 호환 API: Groq / OpenAI / Gemini 모두 지원) ---
    llm_api_key: str = field(default="", repr=False)
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "openai/gpt-oss-120b"
    llm_timeout_sec: float = 30.0
    llm_reasoning_effort: str = ""   # gpt-oss 추론 강도 (low/medium/high). 비우면 서비스 기본값
    llm_tpm: int = 8000              # LLM 분당 토큰 한도 (Groq 무료 티어 gpt-oss-120b). 평가 속도 조절용

    # --- 안전장치 ---
    max_rows: int = 500               # 결과 최대 행 수 (LIMIT 강제)
    query_timeout_ms: int = 5000      # DB 쿼리 시간 제한
    max_question_len: int = 300
    max_retries: int = 2              # SQL 자가 수정 횟수
    per_session_per_min: int = 6      # 세션당 분당 질문 수
    daily_token_budget: int = 90_000  # 무료 티어 보호 (전체 사용자 합산)

    demo_mode: bool = False
    # 접속 코드 (선택). 비어 있으면 접속 확인 없이 바로 사용. 값은 화면·로그·repr 에 내지 않는다
    access_code: str = field(default="", repr=False)
    # 생성된 SQL 을 볼 수 있는 관리자 이메일 (선택, 소문자로 정리됨). 비어 있으면 실데이터에서는 아무도 못 봄
    admin_emails: frozenset = field(default_factory=frozenset, repr=False)
    extra: dict = field(default_factory=dict)


def is_demo_db_url(url: str) -> bool:
    """질문 DB 가 가상 데모 DB(demo/demo.sqlite)인가."""
    if not url.startswith("sqlite:///"):
        return False
    path = url[len("sqlite:///"):].replace("\\", "/").lower()
    return path.endswith("demo/demo.sqlite")


def load_settings() -> Settings:
    query_url = _secret("QUERY_DB_URL", "")
    app_url = _secret("APP_DB_URL", "") or query_url
    demo = is_demo_db_url(query_url)
    if not query_url:
        # DB 설정이 없으면 가상 데모 데이터(SQLite)로 동작
        demo = True
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(here, "demo", "demo.sqlite")
        query_url = f"sqlite:///{path}"
        app_url = f"sqlite:///{os.path.join(here, 'demo', 'app.sqlite')}"

    dialect = "sqlite" if query_url.startswith("sqlite") else _secret("SQL_DIALECT", "mysql")

    return Settings(
        query_db_url=query_url,
        app_db_url=app_url,
        sql_dialect=dialect,
        llm_api_key=_secret("LLM_API_KEY", "") or _secret("GROQ_API_KEY", "") or "",
        llm_base_url=_secret("LLM_BASE_URL", Settings.llm_base_url),
        llm_model=_secret("LLM_MODEL", Settings.llm_model),
        llm_reasoning_effort=(_secret("LLM_REASONING_EFFORT", "") or "").strip().lower(),
        llm_tpm=int(_secret("LLM_TPM", str(Settings.llm_tpm)) or Settings.llm_tpm),
        max_rows=int(_secret("MAX_ROWS", "500")),
        query_timeout_ms=int(_secret("QUERY_TIMEOUT_MS", "5000")),
        daily_token_budget=int(_secret("DAILY_TOKEN_BUDGET", "90000")),
        demo_mode=demo,
        access_code=(_secret("ACCESS_CODE", "") or "").strip(),
        admin_emails=parse_admin_emails(_secret("ADMIN_EMAILS", "")),
    )
