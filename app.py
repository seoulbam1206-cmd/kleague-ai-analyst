"""K리그 AI 분석관 – Streamlit 화면.

화면 코드는 '보여주기'만 담당하고, 실제 로직은 kleague/ 패키지에 있다 (테스트 가능 구조).
실행: streamlit run app.py
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from kleague.access import MAX_FAILS, AccessGate
from kleague.chart_ui import render_chart
from kleague.config import load_settings
from kleague.db import Database
from kleague.entities import EntityResolver
from kleague.llm import OpenAICompatLLM
from kleague.ratelimit import SlidingWindowLimiter, budget_left
from kleague.report import list_games, write_report
from kleague.store import AppStore
from kleague.text2sql import Answer, Text2SQL, Turn
from kleague import viewer

ROOT = Path(__file__).resolve().parent

st.set_page_config(page_title="K리그 AI 분석관", page_icon="⚽", layout="wide")


# ---------------------------------------------------------------------------
# 접속 코드 (ACCESS_CODE 가 있을 때만). 통과 전에는 DB 연결도, 기록 화면도 열지 않는다.
# ---------------------------------------------------------------------------
def access_gate() -> None:
    gate = AccessGate(load_settings().access_code)
    if gate.is_open(st.session_state):
        return
    st.title("⚽ K리그 AI 분석관")
    if gate.locked(st.session_state):
        st.error(f"접속 코드를 {MAX_FAILS}번 틀려 이 세션에서는 더 입력할 수 없습니다. 관리자에게 문의하세요.")
        st.stop()
    with st.form("access", clear_on_submit=True):  # 제출 뒤 입력칸을 비워 코드가 화면에 남지 않게
        attempt = st.text_input("접속 코드", type="password")
        submitted = st.form_submit_button("들어가기")
    if submitted:
        result = gate.try_code(st.session_state, attempt)
        if result == "ok":
            st.rerun()
        if result == "locked":
            st.error(f"접속 코드를 {MAX_FAILS}번 틀려 이 세션에서는 더 입력할 수 없습니다. 관리자에게 문의하세요.")
        else:
            st.warning(f"접속 코드가 맞지 않습니다. 남은 기회: {gate.remaining(st.session_state)}번")
    st.stop()


access_gate()


# ---------------------------------------------------------------------------
# 서비스 초기화 (프로세스당 1회)
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner="데이터베이스 연결 중...")
def services():
    s = load_settings()
    demo_file = Path(s.query_db_url[len("sqlite:///"):]) if s.demo_mode else None
    if demo_file and not demo_file.exists():  # 설정된 데모 DB 자리에만 만든다 (다른 곳에 파일을 남기지 않음)
        from scripts.make_demo_db import build
        build(demo_file)

    query_db = Database(s.query_db_url, read_only=True)
    store = AppStore(Database(s.app_db_url, read_only=False))
    store.init_schema()  # app_* 표가 없으면 만들기 (TiDB 로그 계정은 app_* 표에만 CREATE 권한)
    seeds = yaml.safe_load((ROOT / "eval" / "seed_examples.yaml").read_text(encoding="utf-8"))
    for e in seeds:
        store.add_example(e["question"], e["sql"].strip(), approved=True, source="seed")

    llm = OpenAICompatLLM(s.llm_api_key, s.llm_base_url, s.llm_model, s.llm_timeout_sec,
                          s.llm_reasoning_effort) \
        if s.llm_api_key else None
    engine = Text2SQL(query_db, llm, max_rows=s.max_rows, timeout_ms=s.query_timeout_ms,
                      max_retries=s.max_retries, max_question_len=s.max_question_len,
                      entities=EntityResolver(query_db)) if llm else None
    return s, query_db, store, llm, engine


@st.cache_data(ttl=600, show_spinner=False)
def approved_examples_cached(_store: AppStore) -> list[tuple[str, str]]:
    return _store.approved_examples()


@st.cache_data(ttl=3600, show_spinner=False)
def seasons(_db: Database) -> list[int]:
    return [int(r[0]) for r in _db.query("SELECT DISTINCT meet_year FROM game ORDER BY meet_year DESC")]


@st.cache_data(ttl=3600, show_spinner=False)
def clubs(_db: Database) -> pd.DataFrame:
    rows = _db.query("SELECT team_id, team_s_name FROM club ORDER BY team_s_name")
    return pd.DataFrame(rows, columns=["team_id", "name"])


try:
    settings, qdb, store, llm, engine = services()
except Exception as e:  # 오류 원문에는 접속 주소·계정이 섞일 수 있어 종류 이름만 보여준다
    st.error("데이터베이스에 연결하지 못했습니다. 관리자가 Secrets 의 DB 설정(QUERY_DB_URL, APP_DB_URL)과 "
             f"계정 권한을 확인해야 합니다. (오류 종류: {type(e).__name__})")
    st.stop()

if settings.demo_mode:
    st.warning("**가상 데이터입니다. 실제 K리그 기록이 아닙니다.**")

# 생성된 SQL·오류 상세·평가 기록(SQL 포함)은 관리자에게만: 관리자 이메일(ADMIN_EMAILS)이거나,
# 관리자 코드(ADMIN_CODE)를 입력한 세션이거나, 데모 모드. 표·요약·차트·👍/👎 는 모두에게 그대로 보인다.
# SHOW_SQL 은 사이드바(관리자 코드 입력) 뒤에서 정한다. 이메일·코드는 화면·로그에 내지 않는다.
VIEWER_EMAIL = viewer.read_viewer_email()
ADMIN_GATE = viewer.AdminCodeGate(settings.admin_code)

if engine:
    engine.load_examples(approved_examples_cached(store))

if "limiter" not in st.session_state:
    st.session_state.limiter = SlidingWindowLimiter(settings.per_session_per_min, 60)
if "chat" not in st.session_state:
    st.session_state.chat = []  # [{"role": "user"/"assistant", "text": str, "answer": Answer, "log_id": int}]


# ---------------------------------------------------------------------------
# 사이드바
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("⚽ K리그 AI 분석관")
    st.caption("자연어로 묻고, SQL로 답하는 K리그 공식기록 분석 서비스")
    if settings.demo_mode:
        st.warning("**데모 모드** – 선수·기록은 무작위로 만든 가상 데이터입니다.")
    else:
        st.success("K리그 공식기록 데이터 연결됨")

    used = store.tokens_today()
    st.markdown("**오늘 AI 사용량**")
    st.progress(min(1.0, used / max(1, settings.daily_token_budget)))
    st.caption(f"{used:,} / {settings.daily_token_budget:,} 토큰 (모든 사용자 합산)")
    if not llm:
        st.error("LLM_API_KEY 가 설정되지 않아 질문 기능이 꺼져 있습니다.")

    if st.button("대화 지우기", use_container_width=True):
        st.session_state.chat = []
        st.rerun()

    # 관리자 코드 (ADMIN_CODE 가 있을 때만). 맞으면 이 세션에서만 SQL 표시. 입력값·결과는 기록하지 않는다.
    if ADMIN_GATE.enabled and not settings.demo_mode:
        if ADMIN_GATE.is_ok(st.session_state):
            st.caption("SQL 표시: 관리자 코드로 켜짐 (이 세션만)")
        else:
            with st.form("admin_code", clear_on_submit=True):   # 제출 뒤 입력칸을 비움
                attempt = st.text_input("관리자 코드", type="password", key="admin_code_input")
                sent = st.form_submit_button("확인", key="admin_code_submit")
            if sent:
                result = ADMIN_GATE.try_code(st.session_state, attempt)
                if result == "ok":
                    st.rerun()
                elif result == "locked":
                    st.caption(f"여러 번 틀려 잠시 막았습니다. {ADMIN_GATE.wait_sec(st.session_state)}초 뒤 다시 시도하세요.")
                else:
                    st.caption("관리자 코드가 맞지 않습니다.")
            elif ADMIN_GATE.wait_sec(st.session_state) > 0:
                st.caption(f"여러 번 틀려 잠시 막았습니다. {ADMIN_GATE.wait_sec(st.session_state)}초 뒤 다시 시도하세요.")

    # 진단: 접속자 이메일을 읽었는지만 (주소는 보이지 않음)
    st.caption(viewer.email_status_text(VIEWER_EMAIL))

SHOW_SQL = viewer.can_view_sql(VIEWER_EMAIL, settings.admin_emails, settings.demo_mode,
                               code_ok=ADMIN_GATE.is_ok(st.session_state))


# ---------------------------------------------------------------------------
# 결과 렌더링
# ---------------------------------------------------------------------------
def render_answer(ans: Answer, idx: int, log_id: int | None) -> None:
    st.markdown(ans.summary)
    if ans.df is not None and not ans.df.empty:
        df = ans.df.copy()
        df.index = range(1, len(df) + 1)
        st.dataframe(df, use_container_width=True)
        if ans.truncated:
            st.caption(f"결과가 많아 상위 {settings.max_rows}행만 표시했습니다.")
        render_chart(df, str(idx))   # 막대·꺾은선·영역·원형 (kleague/chart_ui.py)
        st.download_button("CSV 다운로드", df.to_csv(index=False).encode("utf-8-sig"),
                           file_name="kleague_result.csv", mime="text/csv", key=f"dl_{idx}")
    meta = [f"{ans.latency_ms:,}ms"]
    if ans.from_cache:
        meta.append("캐시")
    if ans.attempts > 1:
        meta.append(f"자가 수정 {ans.attempts - 1}회")
    if ans.tokens:
        meta.append(f"{ans.tokens:,} 토큰")
    if ans.sql and SHOW_SQL:
        with st.expander("생성된 SQL · " + " · ".join(meta)):
            st.code(ans.sql, language="sql")
            for n in ans.notes:   # 오류 원문·자가 수정 내역 (SQL 조각이 섞일 수 있음)
                st.caption(n)
    elif ans.sql:
        st.caption(" · ".join(meta))   # SQL·오류 상세 없이 응답 시간·토큰만
    if log_id and ans.status == "ok":
        fb_key = f"fb_{idx}"
        if st.session_state.get(fb_key):
            st.caption("피드백 고맙습니다.")
        else:
            c1, c2, _ = st.columns([1, 1, 8])
            if c1.button("👍", key=f"up_{idx}", help="정확해요"):
                store.set_feedback(log_id, 1)
                # 승인 대기열로만 저장 → 관리자가 검토해야 예시로 쓰임 (예시 오염 방지)
                store.add_example(ans.question, ans.sql, approved=False, source="feedback")
                st.session_state[fb_key] = True
                st.rerun()
            if c2.button("👎", key=f"down_{idx}", help="틀렸어요"):
                store.set_feedback(log_id, -1)
                st.session_state[fb_key] = True
                st.rerun()


# ---------------------------------------------------------------------------
# 탭
# ---------------------------------------------------------------------------
tab_ask, tab_report, tab_about = st.tabs(["💬 기록 질문", "📰 경기 리포트", "📊 성능 · 구조"])

EXAMPLES = [
    "2026 K리그1 득점 순위 TOP 10",
    "구단별 홈 경기 평균 관중 비교",
    "전북의 올해 홈/원정 성적",
    "2026시즌 결승골을 가장 많이 넣은 선수",
]

with tab_ask:
    st.subheader("K리그 기록, 말로 물어보세요")
    if not st.session_state.chat:
        cols = st.columns(len(EXAMPLES))
        for c, q in zip(cols, EXAMPLES):
            if c.button(q, use_container_width=True):
                st.session_state.pending = q
                st.rerun()

    for i, m in enumerate(st.session_state.chat):
        with st.chat_message(m["role"]):
            if m["role"] == "user":
                st.write(m["text"])
            else:
                render_answer(m["answer"], i, m.get("log_id"))

    question = st.chat_input("예: 2026 K리그1 도움 순위 5위까지", disabled=engine is None)
    question = question or st.session_state.pop("pending", None)

    if question and engine:
        ok, wait = st.session_state.limiter.allow()
        if not ok:
            st.warning(f"질문이 너무 빠릅니다. {wait}초 뒤에 다시 시도해 주세요.")
        elif budget_left(store.tokens_today(), settings.daily_token_budget) <= 0:
            st.warning("오늘 AI 사용량이 모두 소진되었습니다. 내일 다시 이용해 주세요.")
        else:
            st.session_state.chat.append({"role": "user", "text": question})
            history = [Turn(m["answer"].question, m["answer"].sql)
                       for m in st.session_state.chat if m["role"] == "assistant"][-1:]
            with st.spinner("SQL을 만들고 기록을 찾는 중..."):
                ans = engine.ask(question, history=history)
            log_id = None
            try:
                store.add_usage(ans.tokens)
                log_id = store.log_query(ans.question, ans.sql, ans.status, ans.attempts,
                                         0 if ans.df is None else len(ans.df), ans.latency_ms,
                                         ans.tokens, ans.from_cache)
            except Exception:
                pass  # 로그 실패가 사용자 응답을 막지 않도록
            st.session_state.chat.append({"role": "assistant", "answer": ans, "log_id": log_id})
            st.rerun()


with tab_report:
    st.subheader("경기 리포트 자동 작성")
    st.caption("공식기록·문자중계를 근거로 AI가 기사를 씁니다. 기사 속 스코어는 자동으로 사실 검증합니다.")
    yrs = seasons(qdb)
    if not yrs:
        st.info("아직 수집된 경기가 없습니다.")
    else:
        c1, c2, c3 = st.columns(3)
        year = c1.selectbox("시즌", yrs)
        seq = c2.selectbox("리그", [1, 2], format_func=lambda v: f"K리그{v}")
        cl = clubs(qdb)
        team = c3.selectbox("구단 (선택)", [None, *cl["team_id"].tolist()],
                            format_func=lambda t: "전체" if t is None
                            else cl.set_index("team_id").loc[t, "name"])
        games = list_games(qdb, year, seq, team)
        if games.empty:
            st.info("조건에 맞는 종료 경기가 없습니다.")
        else:
            labels = {int(r.game_id): f"{r.game_date} · {r.round_id}R  {r.home} {r.home_goal}:{r.away_goal} {r.away}"
                      for r in games.itertuples()}
            gid = st.selectbox("경기", list(labels), format_func=labels.get)
            cache_key = f"report_{year}_{seq}_{gid}"
            if st.button("리포트 만들기", type="primary"):
                if llm and budget_left(store.tokens_today(), settings.daily_token_budget) <= 0:
                    st.warning("오늘 AI 사용량이 소진되어 규칙 기반 요약으로 작성합니다.")
                use_llm = llm if budget_left(store.tokens_today(), settings.daily_token_budget) > 0 else None
                with st.spinner("기사 작성 중..."):
                    rep = write_report(qdb, use_llm, year, seq, gid)
                if rep:
                    store.add_usage(rep.tokens)
                st.session_state[cache_key] = rep
            rep = st.session_state.get(cache_key)
            if rep:
                st.markdown(f"### {rep.title}")
                for w in rep.warnings:
                    st.error(f"사실 검증 경고: {w}")
                st.write(rep.body)
                st.caption("AI 작성" if rep.by_llm else "규칙 기반 자동 요약")
                left, right = st.columns([3, 2])
                with left:
                    st.markdown("**팀 기록**")
                    if not rep.facts.teams.empty:
                        st.dataframe(rep.facts.teams.set_index("구단").T, use_container_width=True)
                with right:
                    st.markdown("**주요 장면**")
                    ev = rep.facts.events
                    if ev.empty:
                        st.caption("이벤트 기록 없음")
                    else:
                        icon = {"goal": "⚽", "warn": "🟨", "inout": "🔁"}
                        for r in ev.itertuples():
                            mark = "🟥" if r.event_code in ("RC", "SY") else icon.get(r.event_type, "•")
                            st.write(f"{mark} {r.remark}")


with tab_about:
    st.subheader("어떻게 동작하나요")
    st.markdown("""
1. **수집** – 내 PC에서 공식기록 수집 → 검증 → 클라우드 DB 복사 (앱은 읽기만 함)
2. **질문** – 이름→코드 힌트 + 유사 예시 검색 → LLM이 SQL 생성 (JSON 응답)
3. **검사** – SQL 구문 트리 분석: 조회문 1개 / 허용 테이블만 / 위험 함수 차단 / LIMIT 강제
4. **실행** – SELECT 전용 계정 + 읽기 전용 트랜잭션 + 시간 제한
5. **자가 수정** – 실패하면 에러를 근거로 최대 2회 고침
6. **요약** – 표에 있는 숫자만 쓰도록 지시
""")
    st.markdown("#### 최근 7일 운영 지표")
    stt = store.stats()
    c1, c2, c3 = st.columns(3)
    c1.metric("질문 수", f"{stt['total']:,}")
    ok_rate = stt["by_status"].get("ok", 0) / stt["total"] if stt["total"] else 0
    c2.metric("성공률", f"{ok_rate * 100:.0f}%")
    c3.metric("평균 응답", f"{stt['avg_latency_ms']:,}ms")
    rep_md = ROOT / "eval" / "RESULTS.md"  # 정답률 기록 (실행별 보고서는 eval/runs/). 정답 SQL 조각이 있어 관리자만
    if rep_md.exists() and SHOW_SQL:
        st.markdown("#### 정답률 평가")
        st.markdown(rep_md.read_text(encoding="utf-8"))
