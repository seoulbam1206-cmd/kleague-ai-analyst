"""공개 준비 점검: 데모 배너 판정 · 공개 전 점검 · 깨끗한 복사본 · 배포 Secrets 예시 · 오류 가림."""
import tomllib
from pathlib import Path

from kleague.config import is_demo_db_url, load_settings
from kleague.text2sql import _clean_db_error
from scripts import export_clean as E
from scripts import prepublish_check as P

ROOT = Path(__file__).resolve().parents[1]
FAKE_KEY = "gsk_" + "Z" * 40  # 실제 키가 아닌 가짜 값
FAKE_PW = "Re4l" + "Passw0rd"   # 이 파일 자체가 공개 전 점검에 걸리지 않게 실행할 때 조립


def _make_tree(base: Path, leak: bool) -> Path:
    (base / "kleague").mkdir(parents=True)
    (base / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (base / "kleague" / "x.py").write_text("X = 1\n", encoding="utf-8")
    if leak:
        (base / "data").mkdir()
        (base / "data" / "k.json").write_text("{}", encoding="utf-8")
        (base / "samples").mkdir()
        (base / "samples" / "s.json").write_text("{}", encoding="utf-8")
        (base / ".env").write_text("A=1\n", encoding="utf-8")
        (base / "logo.png").write_bytes(b"\x89PNG")
        (base / "leak.py").write_text(f'K = "{FAKE_KEY}"\nU = "mysql://u:{FAKE_PW}@h:4000/k"\n'
                                      "Q = 'SELECT * FROM G_TB_GAME'\n", encoding="utf-8")
    return base


# ------------------------------------------------------------ 데모 배너
def test_demo_db_detection(monkeypatch):
    assert is_demo_db_url("sqlite:///D:\\x\\demo\\demo.sqlite")
    assert is_demo_db_url("sqlite:////app/demo/demo.sqlite")
    assert not is_demo_db_url("sqlite:///data/kleague.sqlite")
    assert not is_demo_db_url("mysql://u:p@h:4000/kleague")
    monkeypatch.setenv("QUERY_DB_URL", "sqlite:///demo/demo.sqlite")
    assert load_settings().demo_mode
    monkeypatch.setenv("QUERY_DB_URL", "sqlite:///data/kleague.sqlite")
    assert not load_settings().demo_mode


def test_app_has_banner_and_no_logo():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "가상 데이터입니다. 실제 K리그 기록이 아닙니다." in src
    assert "logo" not in src.lower()
    assert (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8").count('showErrorDetails = "none"') == 1


# ------------------------------------------------------------ 공개 전 점검
def test_prepublish_flags_planted_leaks_without_printing_secret(tmp_path, capsys):
    root = _make_tree(tmp_path / "copy", leak=True)
    rc = P.main(["--root", str(root)])
    out = capsys.readouterr().out
    assert rc == 1
    for word in (".env (금지 파일)", "data/k.json", "samples/s.json", "logo.png", "Groq 키",
                 "비밀번호가 든 DB 주소", "사내 DB 표 이름"):
        assert word in out, word
    assert FAKE_KEY not in out and FAKE_PW not in out


def test_prepublish_passes_clean_tree(tmp_path):
    root = _make_tree(tmp_path / "copy", leak=False)
    files, _ = P.target_files(root)
    assert P.passed(P.run_checks(root, files, {}))


def test_publish_selection_drops_forbidden_and_hold():
    for rel in ("data/kleague.sqlite", "data/backup/a.sqlite", "samples/x.json", "_old/app.py",
                ".env", ".api_cache/a.json", "x.bak", "logo.png", "eval/gold_check.md",
                "eval/report.json", "eval/report_nofewshot.json", "eval/runs/report_fewshot_20261105_090000.json"):
        assert P._excluded(rel), rel
    for rel in (".env.example", ".streamlit/secrets.toml.example", "app.py", "eval/questions.yaml",
                "eval/gold_check_public.md", "eval/RESULTS.md", "eval/runs/report_fewshot_20261105_090000.md"):
        assert P._excluded(rel) is None, rel


# ------------------------------------------------------------ 깨끗한 복사본
def test_export_copies_only_allowed_files_and_never_deletes(tmp_path):
    src = _make_tree(tmp_path / "src", leak=False)
    (src / ".env").write_text("A=1\n", encoding="utf-8")
    (src / "data").mkdir()
    (src / "data" / "k.sqlite").write_bytes(b"x")
    dest = tmp_path / "publish"
    assert E.export(dest, src=src, log=lambda *_: None) == 0
    got = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
    assert got == ["app.py", "kleague/x.py"]
    # 이미 있는 폴더는 지우지 않고 멈춤
    assert E.export(dest, src=src, log=lambda *_: None) == 2
    assert (dest / "app.py").exists()


def test_export_refuses_when_source_check_fails(tmp_path):
    src = _make_tree(tmp_path / "src", leak=False)
    (src / "leak.py").write_text(f'K = "{FAKE_KEY}"\n', encoding="utf-8")
    dest = tmp_path / "publish"
    assert E.export(dest, src=src, log=lambda *_: None) == 1
    assert not dest.exists()


# ------------------------------------------------------------ 배포 Secrets 예시
def test_secrets_example_matches_deploy_list():
    data = tomllib.loads((ROOT / ".streamlit" / "secrets.toml.example").read_text(encoding="utf-8"))
    assert set(data) == {"QUERY_DB_URL", "APP_DB_URL", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
                         "DAILY_TOKEN_BUDGET"}
    assert ".kl_query_ro:" in data["QUERY_DB_URL"] and ".kl_app_rw:" in data["APP_DB_URL"]
    deploy = (ROOT / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
    for k in data:
        assert f"`{k}`" in deploy, k


# ------------------------------------------------------------ 오류 가림
def test_db_error_hides_host_user_ip():
    e = Exception("(2003, \"Can't connect to MySQL server on 'gateway.example.invalid'"
                  " for 'abc.kl_query_ro'@'10.1.2.3'\")")
    msg = _clean_db_error(e)
    assert "gateway.example.invalid" not in msg and "example" not in msg and "kl_query_ro" not in msg and "10.1.2.3" not in msg
    assert "no such column: g.home_goal" in _clean_db_error(Exception("no such column: g.home_goal"))


# ------------------------------------------------------------ 가상 데이터 DB (demo/demo.sqlite)
def _mini_db(path: Path, clubs, players, games, extra_table: str | None = None) -> Path:
    import sqlite3
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE club (team_id TEXT, team_name TEXT, team_s_name TEXT);"
        "CREATE TABLE player (player_id TEXT, name TEXT);"
        "CREATE TABLE game_player_stat (player_id TEXT, player_name TEXT);"
        "CREATE TABLE game (game_date TEXT, home_team_id TEXT, away_team_id TEXT,"
        " home_goal INT, away_goal INT, is_finished INT);")
    con.executemany("INSERT INTO club VALUES (?, ?, ?)", [(f"T{i}", n, s) for i, (n, s) in enumerate(clubs)])
    con.executemany("INSERT INTO player VALUES (?, ?)", [(f"P{i}", n) for i, n in enumerate(players)])
    con.executemany("INSERT INTO game_player_stat VALUES (?, ?)", [(f"P{i}", n) for i, n in enumerate(players)])
    con.executemany("INSERT INTO game VALUES (?, ?, ?, ?, ?, 1)", games)
    if extra_table:
        con.execute(f"CREATE TABLE {extra_table} (q TEXT)")
    con.commit()
    con.close()
    return path


_REAL = dict(clubs=[("실제FC 일번", "실일")], players=["실제선수갑"],
             games=[("2026-03-01", "T0", "T1", 2, 1)])
_FAKE = dict(clubs=[("가상FC 알파", "알파")], players=["가상선수001"],
             games=[("2026-03-02", "T0", "T1", 0, 0)])


def test_only_demo_sqlite_is_allowed():
    assert P._excluded("demo/demo.sqlite") is None
    for rel in ("demo/app.sqlite", "demo.sqlite", "x/demo/demo.sqlite", "demo/other.sqlite", "data/kleague.sqlite"):
        assert P._excluded(rel), rel


def test_demo_db_passes_when_fake(tmp_path):
    real = _mini_db(tmp_path / "real.sqlite", **_REAL)
    demo = _mini_db(tmp_path / "demo.sqlite", **_FAKE)
    assert P.check_demo_db(demo, real) == []


def test_demo_db_fails_on_real_club_player_game(tmp_path):
    real = _mini_db(tmp_path / "real.sqlite", **_REAL)
    demo = _mini_db(tmp_path / "demo.sqlite", clubs=_REAL["clubs"], players=_REAL["players"], games=_REAL["games"])
    res = P.check_demo_db(demo, real)
    assert [s for s, _ in res] == ["실패", "실패", "실패"]
    joined = " ".join(d for _, d in res)
    assert "구단" in joined and "선수" in joined and "경기" in joined
    assert "실제선수갑" not in joined and "실제FC" not in joined   # 값은 출력하지 않음


def test_demo_db_fails_on_log_table_and_known_club_without_real_db(tmp_path):
    demo = _mini_db(tmp_path / "demo.sqlite", clubs=[("울산 가상", "울산")], players=["가상선수001"],
                    games=[], extra_table="app_query_log")
    res = P.check_demo_db(demo, None)
    statuses = [s for s, _ in res]
    assert statuses.count("실패") == 2 and "경고" in statuses


def test_run_checks_fails_when_demo_db_has_real_names(tmp_path, monkeypatch):
    root = _make_tree(tmp_path / "copy", leak=False)
    _mini_db(root / "demo" / "demo.sqlite", clubs=[("포항 가상", "포항")], players=["가상선수001"], games=[])
    monkeypatch.setattr(P, "ROOT", tmp_path / "no_real_db")   # 실제 DB 없음 → 구단 이름 목록으로 판정
    checks = P.run_checks(root)
    demo_check = [c for c in checks if c.name.startswith("8.")][0]
    assert demo_check.status == "실패" and not P.passed(checks)


# ------------------------------------------------------------ 이메일 주소
def test_prepublish_flags_real_looking_email_but_not_fake(tmp_path):
    root = _make_tree(tmp_path / "copy", leak=False)
    (root / "kleague" / "ok.py").write_text('A = "admin@example.invalid"\nB = "x@example.com"\n', encoding="utf-8")
    assert P.passed(P.run_checks(root))
    real = "someone" + "@" + "mail-provider.co.kr"        # 실행할 때 조립 (이 파일이 점검에 걸리지 않게)
    (root / "kleague" / "bad.py").write_text(f'ADMIN = "{real}"\n', encoding="utf-8")
    checks = P.run_checks(root)
    mail = [c for c in checks if c.name.startswith("9.")][0]
    assert mail.status == "실패" and not P.passed(checks)
    assert real not in " ".join(mail.details)              # 값은 출력하지 않음


def test_local_only_tool_in_copy_fails(tmp_path):
    root = _make_tree(tmp_path / "copy", leak=False)
    (root / "scripts").mkdir()
    (root / "scripts" / "feedback_report.py").write_text("X = 1\n", encoding="utf-8")
    checks = P.run_checks(root)
    assert checks[0].status == "실패" and not P.passed(checks)
