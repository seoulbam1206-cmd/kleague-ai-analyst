-- =========================================================
-- TiDB Cloud Starter 계정 만들기 (db/readonly_user.sql 의 Starter 버전)
--
-- 실행 전에 바꿀 것 (찾아 바꾸기)
--   1) PREFIX     → 내 클러스터 접두어 (Connect 화면의 접속 문자열에서 사용자 이름 앞부분, 예: 3pTAoNNegb47Uc8)
--                   TiDB Cloud Starter 는 사용자 이름에 반드시 '<접두어>.' 를 붙여야 한다 (공식 문서).
--   2) CHANGE_ME  → 계정마다 서로 다른 강한 비밀번호 (3곳). 이 파일에 실제 비밀번호를 저장해 두지 말 것.
--
-- 먼저 실행해 둘 것: kleague 데이터베이스와 표 (db/schema.sql, db/app_schema.sql).
--   표 단위 GRANT 는 표가 있어야 성공한다.
--
-- 계정 3개 (권한은 모두 kleague 데이터베이스 안에만)
--   PREFIX.kl_collector : 수집기 전용. 데이터 표 쓰기. 허용 IP 가 등록된 내 PC 에서만 사용.
--   PREFIX.kl_query_ro  : 앱의 질문 실행 전용. 데이터 표 8개 SELECT 만. app_* 표 접근 불가.
--   PREFIX.kl_app_rw    : 앱 로그·피드백 전용. app_* 표 3개만.
--
-- readonly_user.sql 과 다른 점
--   - 사용자 이름: 'collector_user' 등 → 'PREFIX.kl_collector' 등 (Starter 접두어 규칙)
--   - FLUSH PRIVILEGES 뺌: GRANT 는 바로 적용되므로 필요 없음
--   - kl_collector 에 ALTER 추가: 수집기가 시작할 때 db/migrate.sql (새 컬럼 추가) 을 실행하기 때문
--   - kl_app_rw 에 app_* 표 한정 CREATE 추가: 앱이 시작할 때 CREATE TABLE IF NOT EXISTS 를 실행하기 때문
--   - MAX_USER_CONNECTIONS 는 유지: TiDB 가 지원하고 Starter 제한 목록에도 없음.
--     만약 이 줄만 오류가 나면 그 줄을 빼고 나머지를 실행해도 된다 (보안 핵심은 SELECT 전용 권한).
-- =========================================================

-- 1) 수집기 계정 (쓰기). DELETE·DROP 권한은 주지 않는다 (수집기는 지우지 않고 upsert 만 함)
CREATE USER IF NOT EXISTS 'PREFIX.kl_collector'@'%' IDENTIFIED BY 'CHANGE_ME';
GRANT SELECT, INSERT, UPDATE, CREATE, ALTER, INDEX ON kleague.* TO 'PREFIX.kl_collector'@'%';

-- 2) 질문 실행 계정 (SELECT 전용, 데이터 표만)
CREATE USER IF NOT EXISTS 'PREFIX.kl_query_ro'@'%' IDENTIFIED BY 'CHANGE_ME';
GRANT SELECT ON kleague.club             TO 'PREFIX.kl_query_ro'@'%';
GRANT SELECT ON kleague.stadium          TO 'PREFIX.kl_query_ro'@'%';
GRANT SELECT ON kleague.player           TO 'PREFIX.kl_query_ro'@'%';
GRANT SELECT ON kleague.game             TO 'PREFIX.kl_query_ro'@'%';
GRANT SELECT ON kleague.game_team_stat   TO 'PREFIX.kl_query_ro'@'%';
GRANT SELECT ON kleague.game_player_stat TO 'PREFIX.kl_query_ro'@'%';
GRANT SELECT ON kleague.game_event       TO 'PREFIX.kl_query_ro'@'%';
GRANT SELECT ON kleague.team_rank        TO 'PREFIX.kl_query_ro'@'%';
-- 무거운 쿼리 폭주 방지: 동시 접속 5개까지
ALTER USER 'PREFIX.kl_query_ro'@'%' WITH MAX_USER_CONNECTIONS 5;

-- 3) 앱 로그 계정 (app_* 표만)
CREATE USER IF NOT EXISTS 'PREFIX.kl_app_rw'@'%' IDENTIFIED BY 'CHANGE_ME';
GRANT SELECT, INSERT, UPDATE, CREATE ON kleague.app_sql_example TO 'PREFIX.kl_app_rw'@'%';
GRANT SELECT, INSERT, UPDATE, CREATE ON kleague.app_query_log   TO 'PREFIX.kl_app_rw'@'%';
GRANT SELECT, INSERT, UPDATE, CREATE ON kleague.app_usage       TO 'PREFIX.kl_app_rw'@'%';

-- 4) 확인 (결과에 위 권한만 나와야 함)
SHOW GRANTS FOR 'PREFIX.kl_collector'@'%';
SHOW GRANTS FOR 'PREFIX.kl_query_ro'@'%';
SHOW GRANTS FOR 'PREFIX.kl_app_rw'@'%';
