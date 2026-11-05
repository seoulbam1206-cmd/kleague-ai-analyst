"""데모·테스트용 가상 데이터 DB 생성 (demo/demo.sqlite).

⚠️ 구단·선수·기록은 모두 무작위로 만든 가상 데이터입니다. 실제 기록이 아닙니다.
   구단은 '가상 ○○FC', 선수는 '가상' 으로 시작하는 이름이라 실제 구단·선수와 겹치지 않는다.
   (scripts/prepublish_check.py 의 '8. 가상 데이터 DB' 점검이 실제 이름과 겹치지 않는지 확인한다)

API 키 없이도 앱·테스트·평가를 돌려볼 수 있게 하기 위한 용도.
    python scripts/make_demo_db.py
"""
from __future__ import annotations

import random
import sqlite3
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kleague.db import Database  # noqa: E402

# 가상 구단: 이름·연고지 모두 지어낸 값 (실제 K리그 구단·도시 이름을 쓰지 않는다)
_GREEK = ["알파", "베타", "감마", "델타", "엡실론", "제타", "에타", "세타", "이오타", "카파", "람다", "오메가"]
CLUBS = [  # team_id, 구단명, 약칭, 연고지
    (f"D{i + 1:02d}", f"가상 {g}FC", g, f"가상시 {g}구") for i, g in enumerate(_GREEK)
]
SURNAMES = list("김이박최정강조윤장임한오서신권황안송류홍")
SYLLABLES = list("민준서도현우지훈재영성호진태윤수하은규석동철상원")
FOREIGN = ["루카스", "다니엘", "마르코", "파블로", "안드레", "티아고", "펠리페", "가브리엘", "레안드로", "호드리고", "알렉스", "빅토르"]
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
POS = ["GK"] * 2 + ["DF"] * 7 + ["MF"] * 7 + ["FW"] * 4


def make_names(rng: random.Random, n: int) -> list[str]:
    """'가상' + 성 + 이름 두 글자 (예: 가상김민준). 실제 선수 이름과 겹치지 않게 '가상' 을 붙인다."""
    names = set()
    while len(names) < n:
        names.add("가상" + rng.choice(SURNAMES) + rng.choice(SYLLABLES) + rng.choice(SYLLABLES))
    return sorted(names)


def build(path: Path, seed: int = 7) -> None:
    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    db = Database(f"sqlite:///{path}", read_only=False)
    db.run_script((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))

    db.upsert("club", ["team_id"], [{
        "team_id": t, "team_name": n, "team_s_name": s, "team_eng_name": None, "area_name": a,
        "league_name": "K리그1", "manager_name": f"감독{i + 1:02d}", "founded_date": "2000-01-01",
    } for i, (t, n, s, a) in enumerate(CLUBS)])
    db.upsert("stadium", ["field_id"], [{
        "field_id": 100 + i, "field_name": f"{s} 경기장", "field_fullname": f"{s} 축구전용경기장",
        "capacity": rng.choice([15000, 20000, 25000, 35000, 40000]), "home_team_id": t,
    } for i, (t, _, s, _) in enumerate(CLUBS)])

    names = iter(make_names(rng, len(CLUBS) * 20))
    roster: dict[str, list[dict]] = {}
    players = []
    pid = 20260001
    for t, *_ in CLUBS:
        roster[t] = []
        for j, pos in enumerate(POS):
            foreign = pos == "FW" and j % 2 == 0
            fname = f"가상{FOREIGN[len(roster) - 1]}{'AB'[j % 4 // 2]}"  # 가상 외국인 이름
            p = {"player_id": str(pid), "name": fname if foreign else next(names),
                 "name_eng": None, "team_id": t, "role_name": "선수", "job_name": None,
                 "position": pos, "back_no": j + 1, "nationality": "브라질" if foreign else "한국",
                 "birth_year": rng.randint(1990, 2006),
                 "is_asian_quota": 0}
            pid += 1
            players.append(p)
            roster[t].append(p)
    db.upsert("player", ["player_id"], players)

    team_ids = [c[0] for c in CLUBS]
    fixtures = list(combinations(team_ids, 2))
    rng.shuffle(fixtures)
    games, tstats, pstats, events = [], [], [], []
    month_day = [(m, d) for m in range(3, 11) for d in (1, 8, 15, 22)]
    for gid, (home, away) in enumerate(fixtures, start=1):
        rnd = (gid - 1) // 6 + 1
        m, d = month_day[min(rnd - 1, len(month_day) - 1)]
        wd = WEEKDAYS[(gid * 3 + rnd) % 7] if gid % 5 else "토"
        hg, ag = rng.choices([0, 1, 2, 3, 4], [25, 33, 24, 12, 6])[0], rng.choices([0, 1, 2, 3], [30, 35, 25, 10])[0]
        hidx = team_ids.index(home)
        games.append({
            "meet_year": 2026, "meet_seq": 1, "game_id": gid, "league_name": "K리그1", "round_id": rnd,
            "game_date": f"2026-{m:02d}-{d:02d}", "game_time": rng.choice(["14:00", "16:30", "19:00"]),
            "weekday": wd, "game_type_name": "정규리그", "home_team_id": home, "away_team_id": away,
            "home_goal": hg, "away_goal": ag, "home_pk_goal": 0, "away_pk_goal": 0,
            "field_id": 100 + hidx, "audience": rng.randint(3000, 30000) + (6000 if wd in "토일" else 0),
            "weather": rng.choice(["맑음", "흐림", "비"]), "temperature": round(rng.uniform(5, 30), 1),
            "humidity": rng.randint(30, 90), "referee_name": f"심판{rng.randint(1, 15):02d}",
            "mom_player_id": None, "is_finished": 1,
        })
        goal_log = []
        for team, opp, gf, ga, is_home in ((home, away, hg, ag, 1), (away, home, ag, hg, 0)):
            res = "W" if gf > ga else ("L" if gf < ga else "D")
            shots = gf + rng.randint(4, 12)
            first = rng.randint(0, gf)
            yc = rng.randint(0, 4)
            tstats.append({
                "meet_year": 2026, "meet_seq": 1, "game_id": gid, "team_id": team, "opponent_id": opp,
                "is_home": is_home, "result": res, "goals_for": gf, "goals_against": ga,
                "goals_1st_half": first, "goals_2nd_half": gf - first,
                "points": {"W": 3, "D": 1, "L": 0}[res], "shots": shots,
                "shots_on_target": gf + rng.randint(0, max(1, shots - gf) // 2), "corners": rng.randint(1, 10),
                "fouls": rng.randint(6, 18), "fouls_suffered": rng.randint(6, 18), "offsides": rng.randint(0, 4),
                "pk_attempts": 0, "pk_goals": 0, "yellow_cards": yc, "red_cards": 1 if rng.random() < 0.05 else 0,
                "possession_pct": None, "formation": rng.choice(["4-4-2", "4-3-3", "3-5-2", "4-2-3-1"]),
                "manager_name": f"감독{team_ids.index(team) + 1:02d}", "substitutions": rng.randint(3, 5),
            })
            squad = roster[team]
            starters = [squad[0]] + rng.sample(squad[2:], 10)
            subs = rng.sample([p for p in squad if p not in starters], 3)
            attackers = [p for p in starters + subs if p["position"] in ("FW", "MF")] or starters
            scorer_w = [5 if p["position"] == "FW" else 2 for p in attackers]
            goals_by, assists_by = {}, {}
            for gi in range(gf):
                s = rng.choices(attackers, scorer_w)[0]
                goals_by[s["player_id"]] = goals_by.get(s["player_id"], 0) + 1
                if rng.random() < 0.7:
                    a = rng.choice([p for p in starters if p is not s])
                    assists_by[a["player_id"]] = assists_by.get(a["player_id"], 0) + 1
                half = 1 if gi < first else 2
                minute = rng.randint(1, 45)
                goal_log.append((half, minute, team, s))
            cards = rng.sample(starters, min(yc, len(starters)))
            for p in squad:
                played = p in starters or p in subs
                mins = 90 if p in starters else (rng.randint(5, 35) if p in subs else 0)
                g = goals_by.get(p["player_id"], 0)
                pstats.append({
                    "meet_year": 2026, "meet_seq": 1, "game_id": gid, "team_id": team,
                    "player_id": p["player_id"], "player_name": p["name"], "back_no": p["back_no"],
                    "position": p["position"], "is_captain": 1 if p is starters[1] else 0,
                    "is_starter": 1 if p in starters else 0,
                    "played": int(played), "minutes": mins, "goals": g,
                    "assists": assists_by.get(p["player_id"], 0),
                    "shots": g + (rng.randint(0, 3) if played and p["position"] != "GK" else 0),
                    "shots_on_target": g, "fouls": rng.randint(0, 2) if played else 0,
                    "fouls_suffered": rng.randint(0, 2) if played else 0,
                    "yellow_cards": 1 if p in cards else 0, "red_cards": 0,
                })
            for c in cards:
                half, minute = rng.randint(1, 2), rng.randint(1, 45)
                events.append({
                    "meet_year": 2026, "meet_seq": 1, "game_id": gid,
                    "event_key": f"warn|YC|{team}_{c['player_id']}|{half}|{minute}",
                    "event_type": "warn", "event_code": "YC", "team_id": team, "player_id": c["player_id"],
                    "player_name": c["name"], "half": half, "minute_raw": minute, "second_raw": 0,
                    "remark": f"{'전반' if half == 1 else '후반'} {minute}분 {c['name']} 경고",
                    "is_own_goal": 0, "is_penalty": 0, "is_first_goal": 0, "is_winning_goal": 0,
                    "sub_in_player_id": None,
                })
        goal_log.sort(key=lambda x: (x[0], x[1]))
        for k, (half, minute, team, s) in enumerate(goal_log):
            events.append({
                "meet_year": 2026, "meet_seq": 1, "game_id": gid,
                "event_key": f"goal|GL|{k}|{half}|{minute}|{s['player_id']}",
                "event_type": "goal", "event_code": "GL", "team_id": team, "player_id": s["player_id"],
                "player_name": s["name"], "half": half, "minute_raw": minute, "second_raw": 0,
                "remark": f"{'전반' if half == 1 else '후반'} {minute}분 {s['name']} 득점",
                "is_own_goal": 0, "is_penalty": 0, "is_first_goal": int(k == 0), "is_winning_goal": 0,
                "sub_in_player_id": None,
            })
    db.upsert("game", ["meet_year", "meet_seq", "game_id"], games)
    db.upsert("game_team_stat", ["meet_year", "meet_seq", "game_id", "team_id"], tstats)
    db.upsert("game_player_stat", ["meet_year", "meet_seq", "game_id", "player_id"], pstats)
    db.upsert("game_event", ["meet_year", "meet_seq", "game_id", "event_key"], events)

    # 순위표 = 결과 집계
    conn = sqlite3.connect(path)
    rows = conn.execute("""
        SELECT t.team_id, c.team_s_name, SUM(points), COUNT(*),
               SUM(result='W'), SUM(result='D'), SUM(result='L'),
               SUM(goals_for), SUM(goals_against)
        FROM game_team_stat t JOIN club c ON c.team_id = t.team_id GROUP BY t.team_id
        ORDER BY SUM(points) DESC, SUM(goals_for) - SUM(goals_against) DESC, SUM(goals_for) DESC
    """).fetchall()
    conn.close()
    db.upsert("team_rank", ["meet_year", "meet_seq", "team_id"], [{
        "meet_year": 2026, "meet_seq": 1, "team_id": r[0], "team_name": r[1], "ranking": i + 1,
        "points": int(r[2]), "played": r[3], "wins": r[4], "draws": r[5], "losses": r[6],
        "goals_for": r[7], "goals_against": r[8], "goal_diff": r[7] - r[8], "streak": None,
    } for i, r in enumerate(rows)])
    print(f"데모 DB 생성: {path} (경기 {len(games)}, 선수기록 {len(pstats)}, 이벤트 {len(events)})")


if __name__ == "__main__":
    build(ROOT / "demo" / "demo.sqlite")
