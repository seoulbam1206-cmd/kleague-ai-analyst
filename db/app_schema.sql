-- 앱 운영 테이블 (APP_DB_URL 쪽에 생성). 질문 실행 계정은 이 테이블에 접근 불가.

-- 정답 SQL 예시집: approved=1 인 것만 Few-shot 에 사용
-- (사용자 👍 는 approved=0 으로 쌓이고, 관리자가 scripts/review_examples.py 로 승인)
CREATE TABLE IF NOT EXISTS app_sql_example (
    id           INTEGER PRIMARY KEY AUTO_INCREMENT,
    question     VARCHAR(300) NOT NULL,
    sql_text     TEXT         NOT NULL,
    approved     INT          DEFAULT 0,
    source       VARCHAR(20)  DEFAULT 'feedback',
    created_at   VARCHAR(19)
);

-- 질문 로그: 성능/정확도 모니터링용
CREATE TABLE IF NOT EXISTS app_query_log (
    id           INTEGER PRIMARY KEY AUTO_INCREMENT,
    created_at   VARCHAR(19),
    question     VARCHAR(300),
    sql_text     TEXT,
    status       VARCHAR(20),     -- ok / empty / blocked / error / no_sql
    attempts     INT,
    rows_returned INT,
    latency_ms   INT,
    tokens       INT,
    cache_hit    INT DEFAULT 0,
    feedback     INT DEFAULT 0    -- 1: 👍, -1: 👎
);

-- 일별 토큰 사용량 (재시작해도 유지)
CREATE TABLE IF NOT EXISTS app_usage (
    usage_date   VARCHAR(10) NOT NULL PRIMARY KEY,
    tokens       INT DEFAULT 0,
    requests     INT DEFAULT 0
);
