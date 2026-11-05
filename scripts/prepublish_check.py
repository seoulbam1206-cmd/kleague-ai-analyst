"""공개 저장소에 올리기 전 점검 (네트워크·LLM·DB 연결 없음).

    python scripts/prepublish_check.py                # 이 폴더에서 '올라갈 파일'만 골라 점검
    python scripts/prepublish_check.py --root ../kleague-ai-publish   # 만든 복사본 점검

점검 항목
  1. 올리면 안 되는 파일·폴더: data/ · data/backup/ · samples/ · _old/ · .env · .api_cache/ (+ *.bak, *.sqlite, secrets.toml)
     단 demo/demo.sqlite 한 파일만 예외 (8번 점검을 통과해야 함)
  2. 비밀값 모양: Groq·OpenAI·Google·GitHub 키, 개인 키, 비밀번호가 든 DB 주소, 주소에 붙은 API 인증 키 값
  3. 실제 비밀값: 로컬 .env 와 옛 git 기록(.env.bak)의 값이 파일에 들어 있는지 (값은 출력하지 않음, 키 이름만)
  4. 옛 사내 정보: 사내 DB 표 이름(P_TB_ 등), MSSQL 접속 설정, 회사 메일, 사설 IP, 내 PC 경로
  5. 연맹 로고·엠블럼 이미지 (그 밖의 이미지는 '확인 필요')
  6. eval/ 보고서의 실제 기록 값 (경고만, 로컬 DB 가 있을 때)
  7. 큰 파일 (2MB 초과, 경고)
  8. 가상 데이터 DB: demo/demo.sqlite 에 실제 구단·선수 이름, 실제 경기 기록, 로그 표가 없는지 (실패)
  9. 이메일 주소: 가짜 주소(.invalid, example.com 등)가 아닌 이메일이 파일에 있으면 실패 (관리자 이메일은 Secrets 에만)
     로컬 전용 도구(LOCAL_ONLY: 피드백 요약 등)는 복사본에 넣지 않는다

결과: 실패가 하나라도 있으면 종료 코드 1. 비밀값 원문은 어디에도 출력하지 않는다.
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 올리면 안 되는 것 (사용자 지정 + 같은 성격의 파일)
FORBIDDEN_DIRS = ("data/", "data/backup/", "samples/", "_old/", ".api_cache/")
FORBIDDEN_FILE_PATTERNS = (".env", ".env.*", "*.bak", "*.sqlite", "*.sqlite-journal",
                           "secrets.toml", "*.pkl")
ALLOWED_EXCEPTIONS = (".env.example", "secrets.toml.example")
# *.sqlite 중 공개해도 되는 것은 이 한 파일뿐 (가상 데이터 체험용). 다른 sqlite 는 계속 금지.
#   대신 '8. 가상 데이터 DB' 점검으로 실제 구단·선수 이름·경기 기록이 없는지 확인한다.
DEMO_DB = "demo/demo.sqlite"
# 실제 DB 가 없을 때(복사본·CI) 구단 이름 비교에 쓰는 K리그 구단 연고지·약칭 (공개된 정보)
REAL_CLUB_WORDS = ("울산", "포항", "제주", "전북", "서울", "대전", "인천", "강원", "광주", "수원", "안양", "김천",
                   "부천", "성남", "부산", "전남", "경남", "충남", "아산", "천안", "청주", "화성", "김포", "안산",
                   "대구", "상무", "현대", "스틸러스")
# 점검 대상에서 빼는 도구 폴더 (원래 올라가지 않음)
SKIP_DIRS = (".git/", ".venv/", "venv/", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/", "node_modules/")
# 공개하지 않는 평가 결과 (원본은 로컬에 그대로 둔다)
#   gold_check.md: 정답 SQL 결과의 첫 줄(실제 선수 이름·기록 값) → 공개용은 gold_check_public.md
#   report*.json : 평가 실행 원본 (AI SQL 등) → 공개용은 .md 보고서와 eval/RESULTS.md
FORBIDDEN_PATH_PATTERNS = ("eval/gold_check.md", "eval/report*.json", "eval/runs/*.json")
# 공개 여부를 사용자가 정하기 전까지 복사본에서 빼 두는 파일 (지금은 없음)
HOLD: dict[str, str] = {}
# 내 PC 에서만 쓰는 관리자 도구 (실제 기록 DB 를 읽음) → 복사본에 넣지 않고, 복사본에 있으면 실패
LOCAL_ONLY = ("scripts/feedback_report.py", "tests/test_feedback_report.py")
# 이메일 주소: 가짜 주소(.invalid, example.com/org/net)만 허용. 실제 주소가 파일에 있으면 실패 (값은 출력 안 함)
EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
FAKE_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".bmp")
LOGO_WORDS = ("logo", "emblem", "kleague", "k-league", "kfa", "로고", "엠블럼", "엠블렘")
TEXT_EXT = (".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".json", ".sql", ".bat", ".cfg",
            ".ini", ".example", ".gitignore", ".csv", ".html", ".js", ".css", ".sh")
BIG_BYTES = 2 * 1024 * 1024
SELF_FILES = {"scripts/prepublish_check.py", "tests/test_publish.py"}  # 검사 규칙 자체가 적힌 파일

SECRET_PATTERNS = [
    ("Groq 키", re.compile(r"gsk_[A-Za-z0-9]{20,}")),
    ("OpenAI 키", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("Google 키", re.compile(r"AIza[0-9A-Za-z_-]{30,}")),
    ("GitHub 토큰", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("개인 키", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("API 인증 키 값", re.compile(r"(?i)(?:api[_-]?key|auth[_-]?key|access[_-]?key|token)=([A-Za-z0-9%+/=_-]{16,})")),
]
DB_URL = re.compile(r"\b(?:mysql|tidb|postgres(?:ql)?|mssql)(?:\+\w+)?://([^:\s/@'\"]+):([^@\s'\"]+)@")
PLACEHOLDER_WORDS = ("비밀번호", "CHANGE_ME", "[", "<", "***", "password", "xxx", "...", "{")
PLACEHOLDER_EXACT = {"pw", "pass", "p", "secret"}

INTERNAL_PATTERNS = [
    ("사내 DB 표 이름", re.compile(r"\b[A-Z]_TB_[A-Z0-9_]+|\bReport_tb_\w+|\bTb_Real\w+")),
    ("MSSQL 접속 설정", re.compile(r"\bpyodbc\b|ODBC Driver|\bMSSQL_(?:SERVER|DATABASE|USER|PASSWORD)\b")),
    ("회사 메일", re.compile(r"[\w.+-]+@kleague\.(?:com|co\.kr|kr)\b", re.I)),
    ("사설 IP", re.compile(r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")),
    ("내 PC 경로", re.compile(r"\b[A-Za-z]:\\(?:Users|myproject)\\", re.I)),
]


@dataclass
class Check:
    name: str
    status: str = "통과"          # 통과 / 실패 / 경고
    details: list[str] = field(default_factory=list)

    def add(self, status: str, detail: str) -> None:
        order = {"통과": 0, "경고": 1, "실패": 2}
        if order[status] > order[self.status]:
            self.status = status
        self.details.append(detail)


# ---------------------------------------------------------------- 대상 파일
def _excluded(rel: str) -> str | None:
    """올리지 않을 파일이면 이유를 돌려준다."""
    name = rel.rsplit("/", 1)[-1]
    if any(rel.startswith(d) for d in SKIP_DIRS) or "/__pycache__/" in f"/{rel}" or name.endswith(".pyc"):
        return "도구 파일"
    if any(rel.startswith(d) for d in FORBIDDEN_DIRS):
        return "금지 폴더"
    if rel != DEMO_DB and name not in ALLOWED_EXCEPTIONS             and any(fnmatch.fnmatch(name, p) for p in FORBIDDEN_FILE_PATTERNS):
        return "금지 파일"
    if any(fnmatch.fnmatch(rel, p) for p in FORBIDDEN_PATH_PATTERNS):
        return "비공개 평가 결과"
    if is_logo_image(rel):
        return "로고 이미지"
    if rel in HOLD:
        return "보류"
    if rel in LOCAL_ONLY:
        return "로컬 전용"
    return None


def is_logo_image(rel: str) -> bool:
    low = rel.lower()
    return low.endswith(IMAGE_EXT) and any(w in low.rsplit("/", 1)[-1] for w in LOGO_WORDS)


def candidate_files(root: Path) -> list[str]:
    """git 저장소면 '올라갈 수 있는 파일'(추적 + .gitignore 에 안 걸린 새 파일), 아니면 폴더 전체."""
    if (root / ".git").exists():
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
                             capture_output=True, check=True).stdout.decode("utf-8")
        files = [f for f in out.split("\0") if f and (root / f).is_file()]  # 지운 파일은 제외
    else:
        files = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]
    return sorted(set(files))


def publish_files(root: Path) -> tuple[list[str], dict[str, str]]:
    """(올릴 파일, {뺀 파일: 이유})."""
    keep, dropped = [], {}
    for rel in candidate_files(root):
        why = _excluded(rel)
        if why == "도구 파일":
            continue
        if why:
            dropped[rel] = why
        else:
            keep.append(rel)
    return keep, dropped


def target_files(root: Path) -> tuple[list[str], dict[str, str]]:
    """점검 대상. git 저장소(작업 폴더)면 올라갈 파일만, 아니면(복사본) 들어 있는 파일 전부 → 금지 파일은 실패."""
    if (root / ".git").exists():
        return publish_files(root)
    return [f for f in candidate_files(root) if _excluded(f) != "도구 파일"], {}


# ---------------------------------------------------------------- 비밀값 원본
def _env_values(text: str, source: str) -> list[tuple[str, str]]:
    vals = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if len(v) >= 8 and not _is_placeholder(v) and not v.lower().startswith(("http://", "https://", "sqlite:")):
            vals.append((f"{source}:{k.strip()}", v))
            # DB 주소면 비밀번호·호스트만 따로도 찾는다
            m = DB_URL.search(v)
            if m and len(m.group(2)) >= 6:
                vals.append((f"{source}:{k.strip()}(비밀번호)", m.group(2)))
    return vals


def known_secret_values(root: Path) -> list[tuple[str, str]]:
    """(출처 이름, 값). 값은 메모리에서 비교만 하고 출력하지 않는다."""
    vals: list[tuple[str, str]] = []
    env = root / ".env"
    if env.exists():
        vals += _env_values(env.read_text(encoding="utf-8", errors="ignore"), ".env")
    if (root / ".git").exists():  # 옛 git 기록의 .env.bak (읽기만)
        r = subprocess.run(["git", "-C", str(root), "show", "HEAD:.env.bak"], capture_output=True)
        if r.returncode == 0:
            vals += _env_values(r.stdout.decode("utf-8", errors="ignore"), "옛 기록 .env.bak")
    return vals


def _is_placeholder(v: str) -> bool:
    return v.lower() in PLACEHOLDER_EXACT or any(w.lower() in v.lower() for w in PLACEHOLDER_WORDS) \
        or "발급받은" in v or v.startswith(("gsk_...", "$"))


# ---------------------------------------------------------------- 점검
def _read_text(path: Path) -> str | None:
    if not (path.suffix.lower() in TEXT_EXT or path.name.startswith(".")) or path.stat().st_size > BIG_BYTES:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _player_names(root: Path) -> set[str]:
    db = root / "data" / "kleague.sqlite"
    if not db.exists():
        return set()
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        # 흔한 낱말과 겹치는 짧은 이름(예: '포항', '한국')은 오탐이라 3글자 이상, 구단 이름 제외
        clubs = {r[0] for r in con.execute("SELECT team_s_name FROM club")}
        return {r[0] for r in con.execute("SELECT DISTINCT name FROM player WHERE length(name) >= 3")} - clubs
    finally:
        con.close()


def check_demo_db(demo: Path, real: Path | None = None) -> list[tuple[str, str]]:
    """가상 데이터 DB 에 실제 구단 이름·선수 이름·경기 기록이 없는지 본다. 값은 돌려주지 않고 개수만.
    real(실제 기록 DB)이 있으면 그것과 직접 비교하고, 없으면 구단 이름만 REAL_CLUB_WORDS 로 본다."""
    out: list[tuple[str, str]] = []
    con = sqlite3.connect(f"file:{demo.as_posix()}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        logs = sorted(t for t in tables if t.startswith("app_"))
        if logs:
            out.append(("실패", f"로그 표가 들어 있음: {len(logs)}개"))
        clubs = {v for r in con.execute("SELECT team_name, team_s_name FROM club") for v in r if v}
        players = {r[0] for r in con.execute("SELECT name FROM player WHERE name IS NOT NULL")}             | {r[0] for r in con.execute("SELECT player_name FROM game_player_stat WHERE player_name IS NOT NULL")}
        games = set(con.execute("SELECT game_date, home_team_id, away_team_id, home_goal, away_goal "
                                "FROM game WHERE is_finished = 1"))
    finally:
        con.close()
    if real is not None and real.exists():
        rc = sqlite3.connect(f"file:{real.as_posix()}?mode=ro", uri=True)
        try:
            real_clubs = {v for r in rc.execute("SELECT team_name, team_s_name FROM club") for v in r if v}
            real_players = {r[0] for r in rc.execute("SELECT name FROM player WHERE name IS NOT NULL")}                 | {r[0] for r in rc.execute("SELECT player_name FROM game_player_stat WHERE player_name IS NOT NULL")}
            real_games = set(rc.execute("SELECT game_date, home_team_id, away_team_id, home_goal, away_goal "
                                        "FROM game WHERE is_finished = 1"))
        finally:
            rc.close()
        same_club = clubs & real_clubs
        same_player = players & real_players
        same_game = games & real_games
        if same_club:
            out.append(("실패", f"실제 구단 이름과 같은 값 {len(same_club)}개"))
        if same_player:
            out.append(("실패", f"실제 선수 이름과 같은 값 {len(same_player)}개"))
        if same_game:
            out.append(("실패", f"실제 경기 기록(날짜·대진·스코어)과 같은 경기 {len(same_game)}개"))
    else:
        same_club = {c for c in clubs if any(w in c for w in REAL_CLUB_WORDS)}
        if same_club:
            out.append(("실패", f"실제 구단 이름이 들어간 값 {len(same_club)}개"))
        out.append(("경고", "실제 기록 DB 없음 → 선수 이름·경기 기록 비교는 건너뜀 (원본 폴더에서 점검할 것)"))
    return out


def run_checks(root: Path, files: list[str] | None = None,
               dropped: dict[str, str] | None = None) -> list[Check]:
    if files is None:
        files, dropped = target_files(root)
    dropped = dropped or {}

    c_forbid = Check("1. 올리면 안 되는 파일·폴더")
    for rel in files:
        why = _excluded(rel)
        if why in ("금지 폴더", "금지 파일", "비공개 평가 결과", "로컬 전용"):
            c_forbid.add("실패", f"{rel} ({why})")
    if dropped:
        groups: dict[str, int] = {}
        for rel, why in dropped.items():
            top = rel.split("/", 1)[0] + ("/" if "/" in rel else "")
            groups[f"{top} [{why}]"] = groups.get(f"{top} [{why}]", 0) + 1
        c_forbid.details.append("자동 제외: " + ", ".join(f"{k} {v}개" for k, v in sorted(groups.items())))

    c_pat = Check("2. 비밀값 모양 (키·비밀번호)")
    c_known = Check("3. 실제 비밀값 (.env·옛 기록과 비교)")
    c_int = Check("4. 옛 사내 정보")
    c_img = Check("5. 연맹 로고·이미지")
    c_real = Check("6. eval 보고서의 실제 기록 값")
    c_big = Check("7. 큰 파일 (2MB 초과)")
    c_demo = Check("8. 가상 데이터 DB (demo/demo.sqlite)")
    c_mail = Check("9. 이메일 주소 (가짜 주소만 허용)")

    # 원본 프로젝트의 .env·옛 기록은 복사본을 점검할 때도 비교 기준으로 쓴다
    secrets = known_secret_values(ROOT) + (known_secret_values(root) if root.resolve() != ROOT else [])
    if not secrets:
        c_known.details.append("비교할 원본 없음 (.env·git 기록 없음)")
    names = _player_names(ROOT)

    for rel in files:
        path = root / rel
        if path.stat().st_size > BIG_BYTES:
            c_big.add("경고", f"{rel} ({path.stat().st_size / 1024 / 1024:.1f}MB)")
        if rel.lower().endswith(IMAGE_EXT):
            if is_logo_image(rel):
                c_img.add("실패", f"{rel} (로고·엠블럼 이미지)")
            else:
                c_img.add("경고", f"{rel} (연맹 로고·엠블럼이 보이는지 직접 확인)")
        text = _read_text(path)
        if text is None:
            continue
        lines = text.splitlines()

        def where(pos: int) -> str:
            return f"{rel}:{text.count(chr(10), 0, pos) + 1}"

        for label, rx in SECRET_PATTERNS:
            for m in rx.finditer(text):
                if label == "API 인증 키 값" and _is_placeholder(m.group(1)):
                    continue
                c_pat.add("실패", f"{where(m.start())} {label}")
        for m in DB_URL.finditer(text):
            if not _is_placeholder(m.group(2)):
                c_pat.add("실패", f"{where(m.start())} 비밀번호가 든 DB 주소")
        for label, val in secrets:
            i = text.find(val)
            if i >= 0:
                c_known.add("실패", f"{where(i)} ← {label} 값과 같음")
        if rel not in SELF_FILES:
            for label, rx in INTERNAL_PATTERNS:
                for m in rx.finditer(text):
                    c_int.add("실패", f"{where(m.start())} {label}")
        for m in EMAIL_RX.finditer(text):
            dom = m.group(1).lower()
            if not (dom.endswith(".invalid") or dom in FAKE_EMAIL_DOMAINS):
                c_mail.add("실패", f"{where(m.start())} 실제 이메일로 보이는 주소 (값은 표시 안 함)")
        if rel.startswith("eval/") and names:
            hit = sorted({n for n in names if n in text})
            if hit:
                nums = sum(1 for l in lines if re.search(r"\|\s*-?\d[\d.,]*\s*\|", l))
                c_real.add("경고", f"{rel}: 선수 이름 {len(hit)}명" + (f", 기록 값이 든 표 줄 {nums}줄" if rel.endswith(".md") else ""))
    if not names:
        c_real.details.append("로컬 DB 없음 → 건너뜀")
    if DEMO_DB in files:
        for status, detail in check_demo_db(root / DEMO_DB, ROOT / "data" / "kleague.sqlite"):
            c_demo.add(status, detail)
    else:
        c_demo.details.append("포함 안 됨")
    return [c_forbid, c_pat, c_known, c_int, c_img, c_real, c_big, c_demo, c_mail]


def render(checks: list[Check], n_files: int) -> str:
    out = [f"점검한 파일: {n_files}개", "", "| 항목 | 결과 | 내용 |", "|---|---|---|"]
    for c in checks:
        mark = {"통과": "✅ 통과", "경고": "⚠️ 경고", "실패": "❌ 실패"}[c.status]
        detail = "<br>".join(c.details[:8]) + (f"<br>… 외 {len(c.details) - 8}건" if len(c.details) > 8 else "")
        out.append(f"| {c.name} | {mark} | {detail or '-'} |")
    return "\n".join(out)


def passed(checks: list[Check]) -> bool:
    return all(c.status != "실패" for c in checks)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="공개 저장소 올리기 전 점검")
    ap.add_argument("--root", default=str(ROOT))
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    files, dropped = target_files(root)
    checks = run_checks(root, files, dropped)
    print(render(checks, len(files)))
    if HOLD and root == ROOT:
        print("\n보류(복사본에서 뺌, 원본은 그대로): " + "; ".join(f"{k} – {v}" for k, v in HOLD.items()))
    ok = passed(checks)
    print("\n결과: " + ("통과 – 올릴 수 있음" if ok else "실패 – 위 ❌ 항목을 먼저 고칠 것"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
