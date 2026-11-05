-- =========================================================
-- TiDB/MySQL 계정 분리 (보안의 핵심)
--   collector_user : 수집기 전용. 데이터 테이블 쓰기 가능. 내 PC에서만 사용.
--   query_ro       : 앱의 질문 실행 전용. SELECT 만 가능, app_* 테이블 접근 불가.
--   app_rw         : 앱 로그/피드백 전용. app_* 테이블만 쓰기 가능.
-- 비밀번호는 반드시 바꿔서 실행하세요.
-- =========================================================

CREATE USER IF NOT EXISTS 'collector_user'@'%' IDENTIFIED BY 'CHANGE_ME_1';
GRANT SELECT, INSERT, UPDATE, CREATE, INDEX ON kleague.* TO 'collector_user'@'%';

CREATE USER IF NOT EXISTS 'query_ro'@'%' IDENTIFIED BY 'CHANGE_ME_2';
GRANT SELECT ON kleague.club             TO 'query_ro'@'%';
GRANT SELECT ON kleague.stadium          TO 'query_ro'@'%';
GRANT SELECT ON kleague.player           TO 'query_ro'@'%';
GRANT SELECT ON kleague.game             TO 'query_ro'@'%';
GRANT SELECT ON kleague.game_team_stat   TO 'query_ro'@'%';
GRANT SELECT ON kleague.game_player_stat TO 'query_ro'@'%';
GRANT SELECT ON kleague.game_event       TO 'query_ro'@'%';
GRANT SELECT ON kleague.team_rank        TO 'query_ro'@'%';
-- 무거운 쿼리 방지 (TiDB/MySQL: 동시 접속 수 제한)
ALTER USER 'query_ro'@'%' WITH MAX_USER_CONNECTIONS 5;

CREATE USER IF NOT EXISTS 'app_rw'@'%' IDENTIFIED BY 'CHANGE_ME_3';
GRANT SELECT, INSERT, UPDATE ON kleague.app_sql_example TO 'app_rw'@'%';
GRANT SELECT, INSERT, UPDATE ON kleague.app_query_log   TO 'app_rw'@'%';
GRANT SELECT, INSERT, UPDATE ON kleague.app_usage       TO 'app_rw'@'%';

FLUSH PRIVILEGES;
