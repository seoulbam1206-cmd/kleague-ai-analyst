"""LLM에게 줄 '테이블 설명서'.

INFORMATION_SCHEMA 를 매번 조회하던 방식 대신, 사람이 쓴 설명을 코드로 관리한다.
  - 컬럼 의미·코드값을 정확히 알려줘 정답률↑
  - DB 조회 0회 → 응답 속도↑
  - 여기 없는 테이블은 SQL 가드가 차단 (화이트리스트와 한 곳에서 관리)
"""
from __future__ import annotations

TABLES: dict[str, dict] = {
    "club": {
        "desc": "구단 정보 (1구단 1행, 과거 구단 포함. 그 시즌에 뛴 구단은 game_team_stat 으로 판단)",
        "cols": {
            "team_id": "구단 코드 PK (예: K09=서울, K01=울산)",
            "team_name": "구단명 (예: FC서울)", "team_s_name": "약칭 (예: 서울)",
            "team_eng_name": "영문명", "area_name": "연고지",
            "league_name": "현재 소속 리그 (K리그1/K리그2)", "manager_name": "현재 감독",
            "founded_date": "창단일 DATE",
        },
    },
    "stadium": {
        "desc": "경기장",
        "cols": {"field_id": "경기장 코드 PK", "field_name": "경기장명", "field_fullname": "정식 명칭",
                 "capacity": "수용 인원", "home_team_id": "주 사용 구단"},
    },
    "player": {
        "desc": ("선수·코칭스태프 프로필 (1인 1행). 현역(is_active=1)과 K리그를 떠난 선수(is_active=0)가 모두 있다. "
                 "2025~2026 경기 기록의 선수는 모두 이 표에 있으므로 국적·포지션 질문은 현역/퇴단 모두 JOIN 으로 답한다. "
                 "선수 이름은 game_player_stat.player_name 을 쓴다"),
        "cols": {
            "player_id": "선수 코드 PK", "name": "이름(한글)", "name_eng": "영문 이름",
            "team_id": "현재 소속 구단", "role_name": "'선수' 또는 '코칭 스태프'",
            "job_name": "스태프 직책 (감독/코치)",
            "position": "등록 포지션 FW/MF/DF/GK ('수비수' 같은 포지션별 질문은 이 값 기준)",
            "back_no": "등번호", "nationality": "국적 (예: 한국, 브라질)",
            "birth_year": "출생 연도 INT (예: 2000). 나이 = 시즌 연도 - birth_year (대략값)",
            "is_asian_quota": "1=아시안쿼터",
            "is_active": "1=현역(지금 K리그 등록), 0=퇴단(K리그를 떠남). '현역 선수만', '떠난 선수' 질문에 사용",
            "act_type_name": "활동구분 원문 ('현역','은퇴'). 연맹은 리그를 떠난 선수를 나이와 관계없이 '은퇴'로 표시",
        },
    },
    "game": {
        "desc": ("경기 1건 = 1행. 일정·결과·관중·날씨. 아직 안 끝난 예정 경기(is_finished=0)도 들어 있다 "
                 "(스코어·관중은 NULL)"),
        "cols": {
            "meet_year": "시즌 연도", "meet_seq": "1=K리그1, 2=K리그2", "game_id": "시즌 내 경기번호",
            "league_name": "K리그1/K리그2 (예정 경기는 NULL → 리그 구분은 meet_seq 사용)",
            "round_id": "라운드", "game_date": "경기일 DATE",
            "game_time": "킥오프 HH:MM", "weekday": "요일 한 글자 (월,화,수,목,금,토,일)",
            "game_type_name": ("경기 구분. K리그1: '스플릿일반'(1~33R), '파이널A'·'파이널B'(34~38R, 스플릿 후). "
                               "K리그2: '일반', 'Play Off'(승격 플레이오프, 승점 없음)"),
            "home_team_id": "홈 구단", "away_team_id": "원정 구단",
            "home_goal": "홈 득점 (자책골 포함)", "away_goal": "원정 득점 (자책골 포함)",
            "home_pk_goal": "승부차기 홈 (현재 데이터는 모두 0)", "away_pk_goal": "승부차기 원정",
            "field_id": "경기장", "audience": "관중 수",
            "weather": "날씨: '맑음','흐림','비','흐리고 비','눈' (비 온 경기 = weather LIKE '%비%')",
            "temperature": "기온 (원천 데이터에 265 같은 오류값이 일부 있음)", "humidity": "습도",
            "referee_name": "주심",
            "mom_player_id": "경기 MOM 선수코드", "is_finished": "1=종료된 경기, 0=예정 경기",
        },
    },
    "game_team_stat": {
        "desc": "경기별 팀 기록. 종료 경기만, 경기당 2행(홈/원정). 팀 승패·득점 집계는 이 테이블 사용",
        "cols": {
            "meet_year": "", "meet_seq": "", "game_id": "", "team_id": "구단",
            "opponent_id": "상대 구단", "is_home": "1=홈, 0=원정", "result": "W/D/L",
            "goals_for": "득점 (상대 자책골 포함)", "goals_against": "실점",
            "goals_1st_half": "전반 득점", "goals_2nd_half": "후반 득점",
            "points": "획득 승점 (승 3, 무 1, 패 0. Play Off 경기는 0)",
            "shots": "슈팅", "shots_on_target": "유효슈팅", "corners": "코너킥",
            "fouls": "파울", "fouls_suffered": "피파울", "offsides": "오프사이드",
            "pk_attempts": "PK 시도", "pk_goals": "PK 성공",
            "yellow_cards": "경고 (경고2회 퇴장의 두 번째 경고 포함)",
            "red_cards": "직접 퇴장(레드카드)만. 공식 기록 그대로",
            "second_yellow_cards": "경고2회 퇴장 수 (문자중계 SY 에서 계산)",
            "possession_pct": "점유율(%)", "formation": "포메이션", "manager_name": "그 경기 감독",
            "substitutions": "교체 인원",
        },
    },
    "game_player_stat": {
        "desc": ("경기별 선수 공식 기록 (종료 경기만, 출전 명단 전원, played=1 이 실제 출전). "
                 "시즌 중 이적한 선수는 팀마다 행이 나뉘므로 시즌 합계는 player_id 로 묶는다"),
        "cols": {
            "meet_year": "", "meet_seq": "", "game_id": "", "team_id": "그 경기 소속 구단",
            "player_id": "선수 코드", "player_name": "선수 이름", "back_no": "등번호",
            "position": "그 경기 선발 위치 FW/MF/DF/GK. 대기 명단에서 시작한 선수는 '대기'",
            "is_captain": "1=주장",
            "is_starter": "1=선발 11명, 0=대기 명단 (교체 출전은 is_starter=0 AND played=1)",
            "played": "1=출전",
            "minutes": "출전 시간(분)", "goals": "득점 (자책골은 들어가지 않음)", "assists": "도움",
            "shots": "슈팅", "shots_on_target": "유효슈팅", "fouls": "파울",
            "fouls_suffered": "피파울", "yellow_cards": "경고 (경고2회 퇴장의 두 번째 경고 포함)",
            "red_cards": "직접 퇴장(레드카드)만. 공식 기록 그대로",
            "second_yellow_cards": "경고2회 퇴장 수 (문자중계 SY 에서 계산)",
        },
    },
    "game_event": {
        "desc": "경기 이벤트 문자중계 (종료 경기만: 득점·경고·퇴장·교체·슈팅·파울 등)",
        "cols": {
            "meet_year": "", "meet_seq": "", "game_id": "", "event_key": "이벤트 키",
            "event_type": "goal / warn / inout / etc",
            "event_code": ("goal: GL=골 | warn: YC=경고, RC=직접 퇴장, SY=경고2회 퇴장 | inout: CH=교체 | "
                           "etc: ST=슈팅, FO=파울, CK=코너킥, OS=오프사이드, PK=페널티킥 차기"),
            "team_id": ("이벤트 팀. 골(GL)이면 득점을 인정받은 팀 "
                        "(자책골이면 자책한 선수의 상대 팀)"),
            "player_id": "이벤트 선수 (자책골이면 자책한 선수, 교체면 나간 선수)", "player_name": "",
            "half": "1 전반, 2 후반, 3 연장전반, 4 연장후반",
            "minute_raw": "해당 하프 안에서의 분 (정렬용)", "second_raw": "초",
            "remark": "공식 문자중계 문장 (화면에는 이 값 그대로 표시)",
            "is_own_goal": "1=자책골", "is_penalty": "1=PK골", "is_first_goal": "1=선제골",
            "is_winning_goal": "1=결승골", "sub_in_player_id": "교체 투입 선수 (inout)",
        },
    },
    "team_rank": {
        "desc": ("시즌 순위표 (수집 시점 스냅샷). '현재 순위' 질문에 사용. "
                 "K리그2 played 에는 Play Off 경기도 포함"),
        "cols": {
            "meet_year": "", "meet_seq": "", "team_id": "", "team_name": "약칭",
            "ranking": "순위", "points": "승점", "played": "경기수", "wins": "승",
            "draws": "무", "losses": "패", "goals_for": "득점", "goals_against": "실점",
            "goal_diff": "득실차", "streak": "최근 연속 기록 (예: 2승)",
        },
    },
}

ALLOWED_TABLES = frozenset(TABLES)

RULES = """\
[작성 규칙]
1. 시즌은 meet_year, 리그는 meet_seq(1=K리그1, 2=K리그2)로 거른다. 리그 언급이 없으면 K리그1(meet_seq=1).
   데이터는 2025, 2026 시즌만 있다. 2026 시즌은 진행 중이다.
2. 연도 언급이 없거나 '올해'·'이번 시즌'이면 가장 최근 시즌: (SELECT MAX(meet_year) FROM game).
3. [종료 경기만] game 테이블로 합계·평균·건수·최대·최소를 낼 때는 반드시 is_finished = 1 을 넣는다
   (예정 경기는 스코어·관중이 NULL 이라 결과가 틀어진다). 일정('다음 경기')만 is_finished = 0 을 쓴다.
   game_team_stat, game_player_stat, game_event 는 종료 경기만 들어 있다.
4. 선수 시즌 기록은 game_player_stat 에서 player_id 로 GROUP BY 해 SUM 한다. 출장 수는 SUM(played).
   시즌 중 이적한 선수는 팀마다 행이 나뉘므로 team_id 로 묶지 않는다.
   구단을 같이 보여줄 땐 GROUP_CONCAT(DISTINCT c.team_s_name) 처럼 합쳐 보여준다.
5. [자책골] 선수 득점(game_player_stat.goals)에는 자책골이 들어가지 않는다.
   팀 득점(game_team_stat.goals_for, game.home_goal/away_goal)에는 들어간다.
   game_event 에서 골(event_type='goal')의 team_id 는 득점을 인정받은 팀이고,
   자책골(is_own_goal=1)의 player_id 는 그 상대 팀 선수다. 선수 득점 순위는 game_player_stat.goals 로 낸다.
6. [경기 구분] K리그1 '파이널A/파이널B 성적'은 game_type_name = '파이널A'/'파이널B',
   '스플릿 전(정규 라운드)'은 game_type_name = '스플릿일반'. K리그2 '플레이오프'는 'Play Off'.
   K리그2 정규 리그 승패 집계는 game_type_name = '일반' 만 쓴다.
7. 팀 승/무/패·득실 집계는 game_team_stat(result, goals_for, goals_against). '현재 순위'는 team_rank.
8. [퇴장] 퇴장 횟수·퇴장 선수 질문은 직접 퇴장(red_cards)과 경고 2회 퇴장(second_yellow_cards)을 합쳐서 센다
   (SUM(red_cards) + SUM(second_yellow_cards)). '레드카드', '직접 퇴장', '다이렉트 퇴장'이라고 물으면 red_cards 만 센다.
   '경고 2회 퇴장'만 물으면 second_yellow_cards 만 센다.
   합친 퇴장 횟수를 낼 때는 내역도 항상 같은 행에 함께 보여준다: 컬럼 별칭은 '퇴장', '직접퇴장', '경고2회퇴장'
   (예: SELECT SUM(red_cards) + SUM(second_yellow_cards) AS 퇴장, SUM(red_cards) AS 직접퇴장,
        SUM(second_yellow_cards) AS 경고2회퇴장 ...).
9. 구단 이름을 보여줄 땐 club 을 JOIN 해 team_s_name 을 쓴다. 선수 이름은 game_player_stat.player_name.
   국적·등록 포지션이 필요할 때만 player 를 JOIN 한다 (현역·퇴단 선수 모두 있다).
   '현역 선수' 조건이 있을 때만 player.is_active = 1 을 넣는다.
10. 결과 컬럼 별칭은 한글로 짧게 (예: AS 선수명, AS 득점). 'TOP N'·'상위 N명'처럼 개수를 정한 순위 질문은 ORDER BY + LIMIT N.
11. 관중 집계는 game.audience (is_finished=1 만). 홈 관중은 home_team_id 기준.
12. 날짜 함수는 쓰지 말고 meet_year, weekday, game_date 비교로 해결한다.
13. SELECT 문 하나만 작성. 제공된 테이블 외 사용 금지.
14. [동점] 1위·최다·최고처럼 최댓값을 묻는 질문은 동점을 모두 보여 준다. LIMIT 1로 한 줄만 자르지 말고
    최댓값과 같은 행을 모두 가져온다 (예: 값 = (SELECT MAX(값) ...) 로 비교하거나 RANK() OVER (...) = 1).15. [평균] 득점·비율처럼 작은 수의 평균은 소수 둘째 자리까지 ROUND(값, 2)로 보여 준다.
    관중처럼 큰 수의 평균은 정수 반올림 ROUND(값)도 된다.
16. [개인기록 범위] 선수 개인 득점·도움 기록은 경기 구분(game_type_name)으로 범위를 정한다.
    K리그1 은 전체 라운드('스플릿일반', '파이널A', '파이널B' 모두). K리그2 는 '일반'(정규 라운드)만 세고 'Play Off' 는 뺀다.
    game_player_stat 에는 구분이 없으므로 K리그2 는 game 을 JOIN 한다
    (ON g.meet_year = s.meet_year AND g.meet_seq = s.meet_seq AND g.game_id = s.game_id, g.game_type_name = '일반').
    라운드 수는 시즌마다 다르므로 round_id 숫자로 범위를 거르지 않는다.
"""


def schema_prompt(tables: list[str] | None = None) -> str:
    """테이블 설명서 + 작성 규칙. tables 를 주면 그 표 설명만 넣는다 (토큰 절약). 규칙은 항상 전부."""
    lines = ["[테이블 설명서]"]
    for t, spec in TABLES.items():
        if tables is not None and t not in tables:
            continue
        cols = ", ".join(f"{c}({d})" if d else c for c, d in spec["cols"].items())
        lines.append(f"- {t}: {spec['desc']}\n  컬럼: {cols}")
    return "\n".join(lines) + "\n\n" + RULES


# ---------------------------------------------------------------------------
# 질문과 관련된 표 고르기 (SQL 생성 프롬프트를 줄이기 위해). SQL 가드의 허용 표 목록과는 무관하다.
# ---------------------------------------------------------------------------
ALWAYS_TABLES = ("club", "game")   # 구단 이름·시즌·종료 경기 조건에 거의 늘 쓰인다
_TABLE_WORDS: dict[str, tuple[str, ...]] = {
    "game_player_stat": ("선수", "득점", "득점왕", "도움", "어시스트", "공격포인트", "출전", "출장", "선발",
                         "교체", "분", "주장", "등번호", "경고", "퇴장", "레드", "슈팅", "골", "누구", "명단", "뛴"),
    "game_team_stat": ("팀", "구단", "승", "무승부", "패", "득점", "실점", "슈팅", "코너", "점유율", "포메이션",
                       "홈", "원정", "경고", "퇴장", "레드", "파울", "오프사이드", "PK", "승률", "전반", "후반"),
    "game_event": ("자책", "PK", "페널티", "결승골", "선제골", "전반", "후반", "분에", "몇 분", "문자중계",
                   "경고 2회", "경고2회", "교체 투입", "이벤트", "연장"),
    "team_rank": ("순위", "승점", "무승부", "승", "패", "1위", "2위", "3위", "꼴찌", "최하위", "현재", "선두"),
    "player": ("국적", "외국인", "브라질", "한국인", "포지션", "수비수", "공격수", "미드필더", "골키퍼", "DF", "FW",
               "MF", "GK", "나이", "출생", "살", "현역", "퇴단", "떠난", "은퇴", "아시안", "등록", "영문"),
    "stadium": ("경기장", "구장", "스타디움", "수용"),
    # game 은 늘 넣지만, 이 낱말만 있는 질문(관중·날씨 등)도 '단서 있음'으로 보고 관련 표만 넣게 한다
    "game": ("관중", "날씨", "비 오는", "기온", "요일", "토요일", "일요일", "스코어", "경기 수", "몇 경기",
             "남은 경기", "일정", "다음 경기", "라운드", "주심", "MOM", "경기"),
}
_FROM_TABLE = None  # re 패턴 (지연 생성)


def tables_in_sql(sql: str) -> set[str]:
    global _FROM_TABLE
    import re
    if _FROM_TABLE is None:
        _FROM_TABLE = re.compile(r"\b(?:FROM|JOIN)\s+([a-z_]+)", re.I)
    return {t.lower() for t in _FROM_TABLE.findall(sql or "") if t.lower() in TABLES}


def relevant_tables(question: str, extra_sql: list[str] | None = None) -> list[str] | None:
    """질문 낱말 + (예시) SQL 에 나온 표. 질문에서 아무 단서도 못 찾으면 None(= 전체 설명서)."""
    q = question or ""
    hit = {t for t, words in _TABLE_WORDS.items() if any(w in q for w in words)}
    for s in extra_sql or []:
        hit |= tables_in_sql(s)
    if not hit:
        return None
    chosen = set(ALWAYS_TABLES) | hit
    return [t for t in TABLES if t in chosen]   # 설명서 순서 유지
