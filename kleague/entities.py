"""질문 속 구단·선수 이름 → 코드 힌트.

LLM이 '전북'을 team_s_name LIKE '%전북%' 처럼 추측하지 않도록
정확한 team_id / player_id 를 미리 찾아 알려준다 (정답률·속도 모두 향상).
"""
from __future__ import annotations

from collections import defaultdict

from .db import Database

# 사람들이 흔히 쓰는 별칭 → 약칭 (club.team_s_name)
MANUAL_ALIASES = {
    "수원삼성": "수원", "수원 삼성": "수원", "전북현대": "전북", "전북 현대": "전북",
    "울산현대": "울산", "울산hd": "울산", "울산 hd": "울산", "포항스틸러스": "포항",
    "fc서울": "서울", "서울fc": "서울", "서울이랜드": "서울E", "서울 이랜드": "서울E",
    "이랜드": "서울E", "제주유나이티드": "제주", "제주sk": "제주", "인천유나이티드": "인천",
    "대구fc": "대구", "강원fc": "강원", "광주fc": "광주", "김천상무": "김천", "상무": "김천",
    "대전하나": "대전", "대전 하나": "대전", "수원fc": "수원FC",
    "부산아이파크": "부산", "전남드래곤즈": "전남", "아산": "충남아산", "충남 아산": "충남아산",
    "청주": "충북청주", "충북 청주": "충북청주", "천안시티": "천안", "파주프런티어": "파주",
}

# 구단 이름을 품고 있는 일반 단어 ('상대전적' 안의 '대전')
_NOT_NAMES = ("상대전적", "상대 전적")

# 지금 리그에서 뛰는 구단만 이름 힌트 대상 (과거 구단 약칭 '아산'·'국민' 등이 끼어들지 않게)
_CLUB_SQL = """
    SELECT team_id, team_name, team_s_name, team_eng_name FROM club
     WHERE league_name IN ('K리그1', 'K리그2')
        OR team_id IN (SELECT DISTINCT team_id FROM game_team_stat)"""

# 선수 이름 힌트 대상: 현역 선수 + 2025~2026 경기 기록에 나온 퇴단 선수.
# (player 에는 1980년대부터의 퇴단 선수 5천여 명도 있어, 전부 넣으면 동명이인 힌트가 넘쳐난다)
_PLAYER_SQL = """
    SELECT player_id, name, team_id, is_active FROM player
     WHERE (role_name = '선수' OR role_name IS NULL)
       AND (is_active = 1 OR is_active IS NULL
            OR player_id IN (SELECT DISTINCT player_id FROM game_player_stat))"""

# 안전망: player 에 없는 경기 기록 선수 (퇴단 선수를 아직 안 받은 DB 등)
_GPS_PLAYER_SQL = """
    SELECT DISTINCT s.player_id, s.player_name, s.team_id, s.meet_year, s.game_id
      FROM game_player_stat s
     WHERE NOT EXISTS (SELECT 1 FROM player p WHERE p.player_id = s.player_id)
     ORDER BY s.meet_year, s.game_id"""


class EntityResolver:
    def __init__(self, db: Database):
        self.team_by_alias: dict[str, tuple[str, str]] = {}  # alias(lower) → (team_id, 약칭)
        self.players: dict[str, list[tuple[str, str]]] = defaultdict(list)  # name → [(id, team)]
        self._load(db)

    def _load(self, db: Database) -> None:
        s_name_to_id: dict[str, str] = {}
        for tid, name, s_name, eng in db.query(_CLUB_SQL):
            if not tid:
                continue
            short = s_name or name
            s_name_to_id[(short or "").lower()] = tid
            for alias in (name, s_name, eng):
                if alias and len(alias) >= 2:
                    self.team_by_alias[alias.lower()] = (tid, short)
        for alias, short in MANUAL_ALIASES.items():
            tid = s_name_to_id.get(short.lower())
            if tid:
                self.team_by_alias[alias.lower()] = (tid, short)

        self.departed: set[str] = set()
        for pid, name, team, active in db.query(_PLAYER_SQL):
            if name and len(name) >= 2:
                self.players[name].append((pid, team))
                if active == 0:
                    self.departed.add(pid)

        last_team: dict[str, tuple[str, str]] = {}  # player_id → (이름, 마지막 경기 소속)
        for pid, name, team, _y, _g in db.query(_GPS_PLAYER_SQL):
            if pid and name:
                last_team[pid] = (name, team)
        for pid, (name, team) in last_team.items():
            if len(name) >= 2:
                self.players[name].append((pid, team))

    def hints(self, question: str) -> str:
        # 구단 이름이 들어 있는 일반 단어는 먼저 가린다 (같은 길이 공백으로 → 위치 유지)
        for w in _NOT_NAMES:
            question = question.replace(w, " " * len(w))
        q = question.lower()
        lines: list[str] = []
        taken: list[tuple[int, int]] = []

        def _free(start: int, end: int) -> bool:
            return all(end <= s or start >= e for s, e in taken)

        def _claim(text: str, word: str) -> bool:
            """text 안에서 아직 안 쓴 위치의 word 를 모두 차지. 하나라도 차지하면 True."""
            hit, idx = False, text.find(word)
            while idx >= 0:
                if _free(idx, idx + len(word)):
                    taken.append((idx, idx + len(word)))
                    hit = True
                idx = text.find(word, idx + 1)
            return hit

        # 긴 별칭부터 매칭 ('서울E'가 '서울'보다 먼저)
        for alias in sorted(self.team_by_alias, key=len, reverse=True):
            if _claim(q, alias):
                tid, short = self.team_by_alias[alias]
                line = f"- 구단 '{short}' → team_id = '{tid}'"
                if line not in lines:
                    lines.append(line)

        for name in sorted(self.players, key=len, reverse=True):
            if _claim(question, name):
                cands = self.players[name]
                if len(cands) == 1:
                    tag = " (퇴단 선수)" if cands[0][0] in self.departed else ""
                    lines.append(f"- 선수 '{name}' → player_id = '{cands[0][0]}'{tag}")
                else:
                    ids = ", ".join(f"'{pid}'(소속 {team})" for pid, team in cands[:5])
                    lines.append(f"- 선수 '{name}' 동명이인: {ids} – 질문 문맥으로 고르거나 모두 포함")

        if not lines:
            return ""
        return "[질문 속 이름 → 코드]\n" + "\n".join(lines)
