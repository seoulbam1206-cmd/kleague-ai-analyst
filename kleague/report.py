"""경기 리포트 자동 작성.

1) DB에서 경기 사실(스코어·팀기록·이벤트·주요 선수)을 고정 SQL로 모은다 (LLM이 SQL을 만들지 않음 → 오류 0)
2) 사실을 JSON으로 LLM에 넘겨 기사체 요약을 쓰게 한다
3) LLM이 없거나 실패해도 규칙 기반 요약으로 항상 결과를 낸다
4) 기사 속 숫자가 사실과 맞는지 자동 검증 (fact check)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import pandas as pd

from .db import Database
from .llm import LLM


@dataclass
class MatchFacts:
    key: dict
    header: dict
    teams: pd.DataFrame
    events: pd.DataFrame
    top_players: pd.DataFrame

    def as_json(self) -> str:
        return json.dumps({
            "경기": self.header,
            "팀기록": self.teams.to_dict(orient="records"),
            "주요장면": self.events[["remark"]].to_dict(orient="records") if not self.events.empty else [],
            "주요선수": self.top_players.to_dict(orient="records"),
        }, ensure_ascii=False, default=str)


@dataclass
class Report:
    title: str
    body: str
    facts: MatchFacts
    by_llm: bool
    tokens: int = 0
    warnings: list[str] = field(default_factory=list)


def _q(db: Database, sql: str, params: tuple) -> pd.DataFrame:
    with db.connection() as conn:
        cur = conn.cursor()
        cur.execute(sql if db.dialect == "sqlite" else sql.replace("?", "%s"), params)
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchall()
        cur.close()
    return pd.DataFrame(list(rows), columns=cols)


def list_games(db: Database, year: int, seq: int, team_id: str | None = None) -> pd.DataFrame:
    sql = """
        SELECT g.game_id, g.round_id, g.game_date, h.team_s_name AS home, a.team_s_name AS away,
               g.home_goal, g.away_goal
        FROM game g
        LEFT JOIN club h ON h.team_id = g.home_team_id
        LEFT JOIN club a ON a.team_id = g.away_team_id
        WHERE g.meet_year = ? AND g.meet_seq = ? AND g.is_finished = 1
    """
    params: list = [year, seq]
    if team_id:
        sql += " AND (g.home_team_id = ? OR g.away_team_id = ?)"
        params += [team_id, team_id]
    sql += " ORDER BY g.game_date DESC, g.game_id DESC"
    return _q(db, sql, tuple(params))


def load_facts(db: Database, year: int, seq: int, game_id: int) -> MatchFacts | None:
    key = (year, seq, game_id)
    head = _q(db, """
        SELECT g.league_name, g.round_id, g.game_date, g.weekday, g.game_type_name,
               h.team_s_name AS home, a.team_s_name AS away, g.home_team_id, g.away_team_id,
               g.home_goal, g.away_goal, g.home_pk_goal, g.away_pk_goal,
               s.field_name, g.audience, g.weather, g.temperature,
               mp.name AS mom
        FROM game g
        LEFT JOIN club h ON h.team_id = g.home_team_id
        LEFT JOIN club a ON a.team_id = g.away_team_id
        LEFT JOIN stadium s ON s.field_id = g.field_id
        LEFT JOIN player mp ON mp.player_id = g.mom_player_id
        WHERE g.meet_year = ? AND g.meet_seq = ? AND g.game_id = ?""", key)
    if head.empty:
        return None
    header = {k: (None if pd.isna(v) else v) for k, v in head.iloc[0].to_dict().items()}

    teams = _q(db, """
        SELECT c.team_s_name AS 구단, t.is_home AS 홈, t.result AS 결과, t.goals_for AS 득점,
               t.shots AS 슈팅, t.shots_on_target AS 유효슈팅, t.possession_pct AS 점유율,
               t.corners AS 코너킥, t.fouls AS 파울, t.yellow_cards AS 경고, t.red_cards AS 퇴장,
               t.formation AS 포메이션, t.manager_name AS 감독
        FROM game_team_stat t LEFT JOIN club c ON c.team_id = t.team_id
        WHERE t.meet_year = ? AND t.meet_seq = ? AND t.game_id = ?
        ORDER BY t.is_home DESC""", key)

    events = _q(db, """
        SELECT e.half, e.minute_raw, e.second_raw, e.event_type, e.event_code, e.team_id,
               e.player_name, e.remark, e.is_own_goal, e.is_penalty, e.is_winning_goal
        FROM game_event e
        WHERE e.meet_year = ? AND e.meet_seq = ? AND e.game_id = ?
          AND e.event_type IN ('goal', 'warn', 'inout')  -- 슈팅·파울·코너킥(etc)은 경기당 수십 건이라 제외
        ORDER BY e.half, e.minute_raw, e.second_raw""", key)

    top = _q(db, """
        SELECT p.player_name AS 선수, c.team_s_name AS 구단, p.goals AS 득점, p.assists AS 도움,
               p.shots AS 슈팅, p.minutes AS 출전분
        FROM game_player_stat p LEFT JOIN club c ON c.team_id = p.team_id
        WHERE p.meet_year = ? AND p.meet_seq = ? AND p.game_id = ? AND p.played = 1
          AND (p.goals > 0 OR p.assists > 0 OR p.shots >= 3)
        ORDER BY p.goals DESC, p.assists DESC, p.shots DESC
        LIMIT 8""", key)

    return MatchFacts({"meet_year": year, "meet_seq": seq, "game_id": game_id},
                      header, teams, events, top)


def _title(h: dict) -> str:
    return (f"[{h.get('league_name') or 'K리그'} {h.get('round_id')}R] "
            f"{h.get('home')} {h.get('home_goal')} : {h.get('away_goal')} {h.get('away')}")


def rule_based_body(f: MatchFacts) -> str:
    h = f.header
    hg, ag = h.get("home_goal") or 0, h.get("away_goal") or 0
    if hg > ag:
        lead = f"{h['home']}이(가) 홈에서 {h['away']}을(를) {hg}-{ag}로 꺾었다."
    elif hg < ag:
        lead = f"{h['away']}이(가) 원정에서 {h['home']}을(를) {ag}-{hg}로 제압했다."
    else:
        lead = f"{h['home']}과(와) {h['away']}이(가) {hg}-{ag}로 비겼다."
    lines = [f"{h.get('game_date')} {h.get('field_name') or ''}에서 열린 경기에서 {lead}"]
    goals = f.events[f.events["event_type"] == "goal"] if not f.events.empty else f.events
    if not goals.empty:
        lines.append("득점: " + ", ".join(goals["remark"].dropna().tolist()))
    if h.get("audience"):
        lines.append(f"관중 {int(h['audience']):,}명이 경기장을 찾았다.")
    if h.get("mom"):
        lines.append(f"경기 MOM은 {h['mom']}.")
    return "\n\n".join(lines)


ARTICLE_SYSTEM = """너는 K리그 경기 기사를 쓰는 기자다. 주어진 JSON 사실만 사용해 한국어 기사(4~6문단)를 쓴다.
규칙:
- JSON에 없는 숫자·선수·사건을 절대 만들지 않는다. 추측·과장 금지.
- 득점 시간 표기는 '주요장면'의 문장 표기를 그대로 쓴다 (예: 후반 45+3분).
- 첫 문단에 결과를, 이후 흐름·주요 선수·팀 기록 비교를 쓴다. 제목은 쓰지 않는다."""


def write_report(db: Database, llm: LLM | None, year: int, seq: int, game_id: int) -> Report | None:
    facts = load_facts(db, year, seq, game_id)
    if facts is None:
        return None
    title = _title(facts.header)
    if llm is None:
        return Report(title, rule_based_body(facts), facts, by_llm=False)
    try:
        reply = llm.complete(ARTICLE_SYSTEM, facts.as_json(), max_tokens=1200, temperature=0.3)
        body = reply.text.strip()
        warnings = fact_check(body, facts)
        if not body:
            raise ValueError("empty")
        return Report(title, body, facts, by_llm=True, tokens=reply.tokens, warnings=warnings)
    except Exception:
        return Report(title, rule_based_body(facts), facts, by_llm=False)


def fact_check(body: str, f: MatchFacts) -> list[str]:
    """기사 속 'N-M' / 'N대 M' 스코어가 실제 스코어와 다르면 경고."""
    h = f.header
    real = {(h.get("home_goal"), h.get("away_goal")), (h.get("away_goal"), h.get("home_goal"))}
    warnings = []
    text = re.sub(r"\d+(?:-\d+){2,}", " ", body)          # 포메이션 4-4-2 제외
    text = re.sub(r"\b\d{1,2}:\d{2}\b", " ", text)       # 시각 14:00 제외
    text = re.sub(r"\d{4}-\d{2}-\d{2}", " ", text)       # 날짜 제외
    for a, b in re.findall(r"(\d+)\s*(?:-|대)\s*(\d+)", text):
        if (int(a), int(b)) not in real and int(a) <= 15 and int(b) <= 15:
            warnings.append(f"기사 속 스코어 '{a}-{b}' 가 실제 결과와 다릅니다.")
    return warnings
