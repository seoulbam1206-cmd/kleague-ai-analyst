-- 이미 만들어진 DB 에 새 컬럼 추가 (2026-11-04). 데이터는 지우지 않는다.
-- 컬럼이 이미 있으면 "duplicate column" 오류가 나고 run_script 가 건너뛴다.
ALTER TABLE player ADD COLUMN is_active INT;
ALTER TABLE player ADD COLUMN act_type_name VARCHAR(10);
ALTER TABLE game_team_stat ADD COLUMN second_yellow_cards INT DEFAULT 0;
ALTER TABLE game_player_stat ADD COLUMN second_yellow_cards INT DEFAULT 0;
