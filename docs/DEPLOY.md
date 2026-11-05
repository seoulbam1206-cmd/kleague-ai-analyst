# 비공개 배포 순서 (Streamlit Community Cloud)

목표: **실데이터 앱 1개**를 비공개로 배포하고, **지정한 이메일 2개만** 접속하게 한다.
외부 사람은 GitHub README 만 본다. 공개 데모 앱은 만들지 않는다.
접근 제한은 **Streamlit 공유 설정**으로만 한다 (앱 코드에 로그인 기능은 없다).

준비가 끝나 있어야 하는 것
- TiDB 에 표와 계정이 있고 데이터가 복사되어 있음 → [`TIDB_MIGRATION.md`](TIDB_MIGRATION.md)
- 공개 저장소용 깨끗한 복사본 → `python scripts/export_clean.py` (아래 1단계)

---

## 1. 새 GitHub 저장소 만들기

1. 지금 폴더에서 `python scripts/prepublish_check.py` → 결과가 **통과**인지 확인
2. `python scripts/export_clean.py` → `../kleague-ai-publish/` 생성 (원본 점검 통과할 때만 만들어짐, 복사본도 다시 점검)
3. GitHub 에서 **새 저장소**를 만든다 (README·.gitignore 자동 생성은 끔)
4. `kleague-ai-publish` 폴더에서 처음 커밋하고 새 저장소로 올린다
   - 지금 폴더(옛 비밀값이 git 기록에 있음)의 `.git` 은 **쓰지 않는다**

## 2. Streamlit Community Cloud 에서 앱 만들기

1. https://share.streamlit.io → 오른쪽 위 **Create app** → **Yup, I have an app**
2. Repository: 새 저장소 / Branch: `main` / Main file path: **`app.py`**
3. **Advanced settings**
   - Python version: **3.11** (CI 와 같은 버전. 기본값은 3.12 라 반드시 바꿀 것)
   - Secrets: 아래 3장의 6줄을 붙여넣기
4. **Save** → 배포가 시작된다

## 3. Secrets 에 넣을 항목 (6개)

예시: [`.streamlit/secrets.toml.example`](../.streamlit/secrets.toml.example)

| 이름 | 값 | 비고 |
|---|---|---|
| `QUERY_DB_URL` | `mysql://[접두어].kl_query_ro:[비밀번호]@[호스트]:4000/kleague` | 질문 실행. SELECT 전용 계정 |
| `APP_DB_URL` | `mysql://[접두어].kl_app_rw:[비밀번호]@[호스트]:4000/kleague` | 질문 로그·피드백. `app_` 표만. **꼭 넣을 것** (없으면 질문 계정으로 로그를 쓰려다 앱 시작 실패) |
| `LLM_API_KEY` | Groq 키 | |
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` | |
| `LLM_MODEL` | `openai/gpt-oss-120b` | |
| `DAILY_TOKEN_BUDGET` | `90000` | 모든 사용자 합산 하루 토큰 상한 |

**넣지 않는 것**: 수집 계정(쓰기 권한)과 수집용 키. 둘 다 내 PC 에만 둔다.

- 비밀번호에 `@ / : #` 같은 문자가 있으면 퍼센트 인코딩 (`@` → `%40`)
- SSL 은 기본으로 켜진다. `SQL_DIALECT` 는 기본값(mysql)이라 넣지 않아도 된다.
- `QUERY_DB_URL` 이 비어 있으면 앱은 가상 데이터(데모)로 동작하고 화면 맨 위에 "가상 데이터입니다" 배너가 뜬다 → 배너가 보이면 Secrets 를 다시 확인.
- 선택 항목 `ACCESS_CODE`: 넣으면 접속 코드 화면이 켜진다 (아래 4-1). 넣지 않으면 지금처럼 바로 쓴다.
- 선택 항목 `ADMIN_EMAILS`: 생성된 SQL 을 볼 관리자 이메일 (쉼표로 구분). 단, Streamlit 1.42.0 부터 Community Cloud 는 앱에 로그인(OIDC, `[auth]`)을 따로 설정하지 않으면 접속자 이메일을 주지 않는다 (비공개 공유만으로는 안 됨). 사이드바 맨 아래가 "접속자 확인: 확인 불가"이면 이 방법은 동작하지 않는 상태다.
- 선택 항목 `ADMIN_CODE` (보조 방법, 권장): 관리자 코드. 넣으면 사이드바에 '관리자 코드' 입력칸이 생기고, 맞게 입력한 세션에서만 SQL 이 보인다.
  1. 앱 Settings → Secrets 에 한 줄 추가: `ADMIN_CODE = "[길고 추측하기 어려운 코드]"` → 저장하면 앱이 다시 시작된다
  2. 코드는 관리자만 알고, 저장소·README·문서·메신저 단체방에 쓰지 않는다. `ACCESS_CODE` 와 다른 값을 쓴다
  3. 5번 연속 틀리면 60초 동안 입력을 막는다. 새로고침하면 다시 입력해야 한다
  4. 코드가 새면 Secrets 에서 바로 바꾼다. 끄려면 줄을 지운다 (입력칸도 사라짐)

## 4. 접속 제한 (Settings → Sharing)

1. 앱 화면 오른쪽 아래 **Manage app** → **⋮** → **Settings** → **Sharing**
2. "Who can view this app" 에서 **Only specific people can view this app** 선택
3. 허용할 이메일 2개를 추가하고 **Save** → 초대 메일이 간다
4. 공개 상태에서 초대하면 접속 제한이 아니라 분석 화면 권한만 생긴다 → **반드시 2번(비공개 전환)을 먼저** 한다

알아 둘 점 (Streamlit 문서 기준, 2026-11-04 확인)
- 비공개 앱은 계정당 **1개만** 만들 수 있다.
- 공개 저장소에서 배포한 앱은 처음에 **공개** 상태다. 문서에 공개 저장소 앱을 비공개로 바꿀 수 있다고 직접 적혀 있지는 않다 → 아래 5장으로 꼭 확인. 비공개로 안 바뀌면 아래 4-1 의 대안을 쓴다.
- 초대받은 사람은 그 이메일이 Google 계정이면 Google 로, 아니면 메일로 받은 1회용 링크로 로그인한다.
- 출처: https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app , https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy

## 4-1. Sharing 으로 비공개 전환이 안 될 때의 대안

5장 확인에서 로그인 없이 앱이 열리면(= 비공개 전환이 안 됨) 아래 둘 중 하나, 또는 둘 다 쓴다.

### 대안 A. 배포용 비공개 저장소를 따로 둔다 (권장)

비공개 저장소에서 배포한 앱은 처음부터 비공개다(작업 공간 개발자만 볼 수 있음). 그래서 Sharing 초대가 원래 뜻대로 동작한다.

1. GitHub 에 **비공개 저장소**를 하나 더 만든다 (예: `kleague-ai-deploy`). 공개 저장소와 같은 내용(`export_clean.py` 복사본)을 올린다.
2. Streamlit 에서 앱을 **비공개 저장소 기준으로** 다시 만든다. 공개 저장소로 만든 앱은 삭제한다 (비공개 앱은 계정당 1개).
3. Streamlit 이 비공개 저장소를 읽을 수 있게 GitHub 접근 권한을 허용한다 (Streamlit 이 처음 연결할 때 묻는다).
4. 4장 2~3번(허용 이메일 추가) → 5장 확인
5. 코드를 고치면 두 저장소에 같이 올린다. 공개 저장소 = 외부 사람이 읽는 README·코드, 비공개 저장소 = 배포용.

### 대안 B. 접속 코드 (`ACCESS_CODE`)

앱에 들어 있는 간단한 잠금이다. 대안 A 와 함께 써도 된다.

1. 앱 Settings → Secrets 에 한 줄 추가: `ACCESS_CODE = "[길고 추측하기 어려운 코드]"` → 저장하면 앱이 다시 시작된다
2. 허용한 2명에게만 코드를 따로(메신저 등) 알려 준다. 코드는 저장소·README·문서에 쓰지 않는다
3. 동작
   - 첫 화면에 코드 입력칸만 나온다. 맞기 전에는 DB 에 연결하지 않고 기록·탭도 보이지 않는다
   - 비교는 `hmac.compare_digest` (시간 차이로 코드를 추측하지 못하게)
   - 틀린 시도는 **세션당 5번**까지. 넘으면 그 세션에서는 더 입력할 수 없다
   - 코드는 화면·로그·오류 메시지에 나오지 않는다. 입력칸은 제출하면 비워진다
4. 한계 (정직하게): 세션 단위 제한이라 새로고침하면 새 세션으로 다시 5번 시도할 수 있다. 사람마다 다른 계정도 아니다(코드 하나를 나눠 씀). 그래서 **추측하기 어려운 긴 코드**를 쓰고, 코드가 새면 Secrets 에서 바로 바꾼다. 제대로 된 접근 제한은 대안 A(Streamlit 로그인)다.
5. 끄기: Secrets 에서 `ACCESS_CODE` 줄을 지운다

## 5. 접속 확인

1. **로그인하지 않은 창**(시크릿 창)으로 앱 주소 열기 → 앱 화면 대신 **로그인 요구**가 나와야 한다
2. 허용한 이메일로 로그인 → 앱이 열린다
3. 허용하지 않은 다른 계정으로 로그인 → 접근이 거부되어야 한다
4. 앱 화면 확인
   - 사이드바에 "K리그 공식기록 데이터 연결됨", **가상 데이터 배너가 없음**
   - 쉬운 질문 1개 (예: "2026 K리그1 득점 순위 TOP 10") → 표가 나오고 생성된 SQL 이 보임
   - 📊 성능·구조 탭의 질문 수가 1 늘어남 (로그 계정 정상)
5. 오류가 나면 화면에는 오류 **종류 이름만** 나온다 (`.streamlit/config.toml` 의 `showErrorDetails = "none"`). 자세한 내용은 **Manage app → 로그**에서 본다.

## 6. 운영 메모

- TiDB Starter 는 오래 쉰 연결을 끊는다. 앱은 연결을 꺼내 쓸 때 확인하고 다시 연결한다.
- Groq 무료 티어 하루 20만 토큰. `DAILY_TOKEN_BUDGET` 을 넘으면 그날은 질문을 받지 않는다.
- 비밀번호를 바꾸면 TiDB 에서 `ALTER USER` 후 Secrets 만 고치면 된다 (앱 재시작).
