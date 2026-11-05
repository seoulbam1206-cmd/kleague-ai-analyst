# 이 프로젝트 규칙 (K리그 AI 분석관)

## 말하는 방식
- 항상 한국어로, 쉬운 말로 답한다. 어려운 용어는 풀어서 쓴다.
- 보고는 "무엇이 바뀌었나 / 결과 / 내가 할 일" 순서로 짧게 쓴다.

## 지킬 것
- `kleague/sql_guard.py`는 허용 범위를 넓히지 않는다. 고치면 테스트를 반드시 돌린다.
- DB 값은 손으로 고치지 않는다. 데이터가 틀리면 수집기로 다시 수집한다 (수집기는 이 공개 저장소에 없다).
- `.env`, `.streamlit/secrets.toml`, 키, 비밀번호, 접속 URL은 출력·로그·보고서·코드·예시 파일에 쓰지 않는다.
  예시 파일(`.env.example`, `.streamlit/secrets.toml.example`)은 가짜 값만 쓴다.
- 아래는 공개 저장소에 넣지 않는다 (`.gitignore`에 들어 있음):
  `data/`, `samples/`, `_old/`, `.env`, `.streamlit/secrets.toml`, `.api_cache/`, 로고·엠블럼 이미지(`logo.png` 등),
  `eval/gold_check.md`, AI SQL이 든 평가 결과 JSON(`eval/report*.json`, `eval/runs/*.json`).
  (`eval/gold_check_public.md`는 공개용이라 넣어도 된다.)
- 커밋과 푸시는 내가 시킬 때만 한다.

## LLM 호출 규칙 (Groq 무료 한도)
- 하루 한도는 약 20만 토큰, 분당 약 8천 토큰이다. 사용자가 정한 토큰 상한을 넘기지 않는다
  (평가는 `--token-budget`으로 상한을 건다).
- 429가 나오면 즉시 멈추고 retry-after와 한도 종류(분당/일일)를 보고한다. 재시도로 더 부르지 않는다.
- 평가(`eval/run_eval.py`)·공격 시험은 사용자가 시킬 때만 돌린다. LLM 없이 점검할 때는 `--mock`을 쓴다.

## 평가 보고
- 정답률은 반드시 "N문항 중 M개"로 문항 수와 함께 쓴다.
- 고른 문항 기준을 적고, 한 번 측정한 차이를 "개선"이라고 쓰지 않는다.
- strict(엄격)와 lenient(관대) 점수를 같이 낸다.
- 측정 결과는 `eval/RESULTS.md`에 기록한다. 보고서는 `eval/runs/`에 날짜·시각 이름으로 쌓인다.

## 데이터 규칙(요약)
- 자책골은 팀 득점(`game.home_goal`/`away_goal`, 팀 기록 `goals_for`)에 넣고,
  선수 득점(`game_player_stat.goals`)에는 넣지 않는다.
- `game` 테이블로 합계·평균·건수를 낼 때는 `is_finished = 1`(종료 경기)만 쓴다. 일정('다음 경기') 질문만 `is_finished = 0`.
- 퇴장은 직접 퇴장(`red_cards`) + 경고 2회 퇴장(`second_yellow_cards`)으로 보여주고 내역을 나눠 쓴다.
  '레드카드', '다이렉트 퇴장'이라고 물으면 `red_cards`만 센다.
- 개인 득점·도움 순위 동률은 대회요강 제30조 4항대로 최다 → 출전경기 적은 선수 → 출전시간 적은 선수 순이다(득점왕은 1명).
  경기·구단 같은 비공식 집계의 최댓값 질문은 동점을 모두 보여 준다.
- 개인기록 범위는 라운드 번호가 아니라 경기 구분으로 정한다(대회요강 제30조 4항, 시즌마다 라운드 수가 다름): K리그1은 스플릿일반·파이널A/B 전체, K리그2는 '일반'만(Play Off 제외).
- 자세한 규칙은 `kleague/schema_docs.py`에 있다.

## 작업 전후
- 코드를 바꾸면 `.venv/Scripts/python -m pytest -q`를 돌리고 결과(통과 개수)를 보고한다.
- 위험하거나 되돌리기 어려운 작업은 먼저 물어본다.
