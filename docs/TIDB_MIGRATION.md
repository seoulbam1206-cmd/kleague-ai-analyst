# 로컬 DB → TiDB 옮기기 안내

데이터는 내 PC 의 로컬 SQLite 에 먼저 쌓고, 앱이 읽는 클라우드 DB(TiDB, MySQL 호환)에는 복사해서 올린다.
앱 코드는 두 DB 를 모두 지원하므로, **앱 쪽에서 바꿀 것은 설정(.env / Secrets)과 DB 계정**이다.

> 수집기와 복사 도구는 공식 API 명세 보호를 위해 이 공개 저장소에 넣지 않았다. 아래는 개념과 결과만 적는다.

## 1. TiDB 준비 (빈 틀만: DB 구조와 계정) – SQL Editor 에 붙여넣어 실행

Starter 는 사용자 이름에 클러스터 접두어를 붙여야 한다 (`<접두어>.이름`, Connect 화면 접속 문자열에서 확인).

1. `CREATE DATABASE IF NOT EXISTS kleague;`
2. `USE kleague;` 다음 `db/schema.sql` 전체 붙여넣기 → 데이터 표
3. 같은 곳에 `db/app_schema.sql` 전체 붙여넣기 → 앱 표 3개
4. `db/tidb_setup.sql` 에서 `PREFIX` → 내 접두어, `CHANGE_ME` 3곳 → 계정마다 다른 비밀번호로 바꾼 뒤 붙여넣기
   (표 단위 권한이라 2·3번이 먼저여야 함). 마지막 `SHOW GRANTS` 결과로 권한 확인
5. 비밀번호를 넣은 파일은 저장하지 말 것 (SQL Editor 에서만 바꿔 실행)

| 계정 | 쓰는 곳 | 권한 |
|---|---|---|
| `<접두어>.kl_collector` | 내 PC 의 수집·복사 (이 저장소에는 없음) | `kleague.*` SELECT·INSERT·UPDATE·CREATE·ALTER·INDEX (DELETE·DROP 없음) |
| `<접두어>.kl_query_ro` | 앱 질문 실행 | 데이터 표 8개 SELECT 만, 동시 접속 5 |
| `<접두어>.kl_app_rw` | 앱 로그·피드백 | `app_*` 표 3개 SELECT·INSERT·UPDATE·CREATE |

## 2. 데이터 옮기기 (개념)

수집 → 검증 → 로컬 DB → 클라우드 DB 복사 순서로 운영한다.

- 로컬 SQLite 가 원본, TiDB 는 사본이다. TiDB 에서 직접 고치지 않는다 (다음 복사 때 원본 값으로 덮어써짐).
- 복사는 표마다 기본키 기준으로 추가·갱신만 한다. 여러 번 실행해도 결과가 같다.
- 검증에서 문제가 나오면 복사하지 않는다.
- 2026-11-04 첫 복사 결과: 표별 행 수 모두 일치, 경기 기록에는 있는데 선수 표에 없는 선수 0명, 2026 K리그1 득점 상위 10명 일치.

## 3. 앱 설정

**Streamlit Cloud Secrets** (앱): `QUERY_DB_URL` = kl_query_ro 주소, `APP_DB_URL` = kl_app_rw 주소 → [`DEPLOY.md`](DEPLOY.md)

- 앱(클라우드)에는 수집 계정과 수집용 키를 **넣지 않는다**.
- 접속 주소에 SSL 이 기본으로 켜진다 (`?ssl=false` 를 붙이지 않는다).

## 4. 옮긴 뒤 확인

1. `python eval/check_gold.py` → 정답 SQL 40개·예시 17개가 TiDB 에서도 실행되는지 (문제 0건)
   - SQLite 와 MySQL 은 `ROUND`·정수 나눗셈·`GROUP_CONCAT` 정렬이 조금 다를 수 있다. 결과가 다르면 이 파일로 먼저 확인.
2. `python eval/run_eval.py --mock` → 채점기 자체 점검
3. 앱에서 `query_ro` 로 `DELETE`·`INSERT` 가 거부되는지 확인 (가드를 끈 상태가 아니라 DB 권한 자체로)
   → **2026-11-04 확인**: kl_query_ro 는 데이터 표 SELECT 허용, INSERT·DELETE·UPDATE·CREATE·app_ 표 조회 모두 거부.
     kl_app_rw 는 app_ 표 읽기·쓰기 허용, 데이터 표 조회 거부. 앱 시작(DB 연결·app_ 표 확인·예시집 읽기)이 TiDB 에서 정상.
