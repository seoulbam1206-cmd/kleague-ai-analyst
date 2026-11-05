-- =========================================================
-- K리그 AI 분석관 – 데이터 스키마 (TiDB/MySQL & SQLite 공용)
-- 원칙
--   1) 공식기록만 저장 (Bepro11 부가기록은 저장하지 않음)
--   2) 개인정보 최소화 (혈액형·신체 사이즈·학력·에이전트 등은 수집 안 함)
--   3) LLM이 쓰기 쉬운 이름: 소문자 snake_case + 의미가 분명한 컬럼명
-- =========================================================

CREATE TABLE IF NOT EXISTS club (
    team_id        VARCHAR(3)  NOT NULL PRIMARY KEY,
    team_name      VARCHAR(30),           -- 예: FC서울
    team_s_name    VARCHAR(15),           -- 약칭: 서울
    team_eng_name  VARCHAR(50),
    area_name      VARCHAR(40),           -- 연고지
    league_name    VARCHAR(20),           -- 현재 소속 리그 (K리그1/K리그2)
    manager_name   VARCHAR(20),
    founded_date   DATE
);

CREATE TABLE IF NOT EXISTS stadium (
    field_id       INT         NOT NULL PRIMARY KEY,
    field_name     VARCHAR(30),
    field_fullname VARCHAR(60),
    capacity       INT,
    home_team_id   VARCHAR(3)
);

CREATE TABLE IF NOT EXISTS player (
    player_id      VARCHAR(8)  NOT NULL PRIMARY KEY,
    name           VARCHAR(20) NOT NULL,
    name_eng       VARCHAR(50),
    team_id        VARCHAR(3),
    role_name      VARCHAR(20),           -- 선수 / 코칭 스태프 (구단 임직원은 저장 안 함)
    job_name       VARCHAR(20),           -- 감독, 코치 (스태프만)
    position       VARCHAR(5),            -- FW / MF / DF / GK
    back_no        INT,
    nationality    VARCHAR(40),
    birth_year     INT,                   -- 출생 연도만 (생년월일·키·몸무게는 저장 안 함)
    is_asian_quota INT DEFAULT 0,         -- 1: 아시안쿼터
    is_active      INT,                   -- 1: 현역, 0: 퇴단 (은퇴 등)
    act_type_name  VARCHAR(10)            -- 활동구분 원문: 현역 / 은퇴 / 해외 / 대기
);

-- 경기 1건 = 1행
CREATE TABLE IF NOT EXISTS game (
    meet_year      INT         NOT NULL,  -- 시즌 (예: 2026)
    meet_seq       INT         NOT NULL,  -- 1: K리그1, 2: K리그2
    game_id        INT         NOT NULL,
    league_name    VARCHAR(20),
    round_id       INT,
    game_date      DATE,
    game_time      VARCHAR(5),
    weekday        VARCHAR(2),            -- 월~일 (한글)
    game_type_name VARCHAR(40),           -- 정규리그, 파이널A ...
    home_team_id   VARCHAR(3),
    away_team_id   VARCHAR(3),
    home_goal      INT,
    away_goal      INT,
    home_pk_goal   INT,                   -- 승부차기
    away_pk_goal   INT,
    field_id       INT,
    audience       INT,                   -- 관중 수
    weather        VARCHAR(20),
    temperature    DECIMAL(4,1),
    humidity       INT,
    referee_name   VARCHAR(20),           -- 주심
    mom_player_id  VARCHAR(8),
    is_finished    INT DEFAULT 0,         -- 1: 경기 종료
    PRIMARY KEY (meet_year, meet_seq, game_id)
);

-- 경기별 팀 기록 (경기당 2행: 홈/원정)
CREATE TABLE IF NOT EXISTS game_team_stat (
    meet_year      INT         NOT NULL,
    meet_seq       INT         NOT NULL,
    game_id        INT         NOT NULL,
    team_id        VARCHAR(3)  NOT NULL,
    opponent_id    VARCHAR(3),
    is_home        INT,                   -- 1: 홈, 0: 원정
    result         VARCHAR(1),            -- W / D / L
    goals_for      INT,
    goals_against  INT,
    goals_1st_half INT,
    goals_2nd_half INT,
    points         DECIMAL(3,1),
    shots          INT,
    shots_on_target INT,
    corners        INT,
    fouls          INT,
    fouls_suffered INT,
    offsides       INT,
    pk_attempts    INT,
    pk_goals       INT,
    yellow_cards   INT,
    red_cards      INT,                   -- 공식 기록 그대로 (직접 퇴장)
    second_yellow_cards INT DEFAULT 0,    -- 경고2회 퇴장 수 (game_event SY 에서 계산)
    possession_pct DECIMAL(5,2),
    formation      VARCHAR(20),
    manager_name   VARCHAR(20),
    substitutions  INT,
    PRIMARY KEY (meet_year, meet_seq, game_id, team_id)
);

-- 경기별 선수 공식 기록
CREATE TABLE IF NOT EXISTS game_player_stat (
    meet_year      INT         NOT NULL,
    meet_seq       INT         NOT NULL,
    game_id        INT         NOT NULL,
    team_id        VARCHAR(3)  NOT NULL,
    player_id      VARCHAR(8)  NOT NULL,
    player_name    VARCHAR(20),
    back_no        INT,
    position       VARCHAR(5),
    is_captain     INT DEFAULT 0,
    is_starter     INT,                   -- 1: 선발 11명, 0: 대기 명단
    played         INT DEFAULT 0,         -- 1: 실제 출전
    minutes        INT DEFAULT 0,
    goals          INT DEFAULT 0,
    assists        INT DEFAULT 0,
    shots          INT DEFAULT 0,
    shots_on_target INT DEFAULT 0,
    fouls          INT DEFAULT 0,
    fouls_suffered INT DEFAULT 0,
    yellow_cards   INT DEFAULT 0,
    red_cards      INT DEFAULT 0,         -- 공식 기록 그대로 (직접 퇴장)
    second_yellow_cards INT DEFAULT 0,    -- 경고2회 퇴장 수 (game_event SY 에서 계산)
    PRIMARY KEY (meet_year, meet_seq, game_id, player_id)
);

-- 경기 이벤트 (득점·경고·퇴장·교체 문자중계)
CREATE TABLE IF NOT EXISTS game_event (
    meet_year      INT         NOT NULL,
    meet_seq       INT         NOT NULL,
    game_id        INT         NOT NULL,
    event_key      VARCHAR(60) NOT NULL,
    event_type     VARCHAR(10),           -- goal / warn / inout / etc
    event_code     VARCHAR(5),            -- GL, YC, RC, CH ...
    team_id        VARCHAR(3),
    player_id      VARCHAR(8),
    player_name    VARCHAR(20),
    half           INT,                   -- 1 전반, 2 후반, 3 연장전반, 4 연장후반
    minute_raw     INT,                   -- 원천 분 (정렬용, 표시는 remark 사용)
    second_raw     INT,
    remark         VARCHAR(500),          -- 공식 문자중계 (표시용 공식 시간 포함)
    is_own_goal    INT DEFAULT 0,
    is_penalty     INT DEFAULT 0,
    is_first_goal  INT DEFAULT 0,
    is_winning_goal INT DEFAULT 0,
    sub_in_player_id VARCHAR(8),
    PRIMARY KEY (meet_year, meet_seq, game_id, event_key)
);

-- 시즌 순위 (수집 시점 기준 스냅샷)
CREATE TABLE IF NOT EXISTS team_rank (
    meet_year      INT         NOT NULL,
    meet_seq       INT         NOT NULL,
    team_id        VARCHAR(3)  NOT NULL,
    team_name      VARCHAR(15),
    ranking        INT,
    points         INT,
    played         INT,
    wins           INT,
    draws          INT,
    losses         INT,
    goals_for      INT,
    goals_against  INT,
    goal_diff      INT,
    streak         VARCHAR(10),
    PRIMARY KEY (meet_year, meet_seq, team_id)
);

-- 수집 상태 (재실행 시 이미 받은 경기는 건너뜀 → API 쿼터 절약)
CREATE TABLE IF NOT EXISTS collect_log (
    meet_year      INT         NOT NULL,
    meet_seq       INT         NOT NULL,
    game_id        INT         NOT NULL,
    collected_at   VARCHAR(19),
    PRIMARY KEY (meet_year, meet_seq, game_id)
);

CREATE INDEX idx_game_date ON game (game_date);
CREATE INDEX idx_gps_player ON game_player_stat (player_id);
CREATE INDEX idx_gps_team ON game_player_stat (meet_year, team_id);
CREATE INDEX idx_gts_team ON game_team_stat (meet_year, team_id);
CREATE INDEX idx_event_player ON game_event (player_id);
