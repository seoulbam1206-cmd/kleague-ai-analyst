"""평가 실행기 저장 방식: 날짜·시각 붙은 새 이름, 덮어쓰기 금지, 한도 초과 실행은 _invalid. (가짜 LLM 만 사용)"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
import run_eval as R  # noqa: E402

WHEN = datetime(2026, 11, 5, 9, 30, 0)


@pytest.fixture()
def demo_env(monkeypatch, demo_path):
    monkeypatch.setenv("QUERY_DB_URL", f"sqlite:///{Path(demo_path).as_posix()}")
    monkeypatch.setenv("LLM_API_KEY", "")


def _old_report(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    old = out_dir / "report.md"
    old.write_text("옛 보고서", encoding="utf-8")
    return old


def test_mock_run_saves_new_timestamped_file_without_overwriting(demo_env, tmp_path):
    out_dir = tmp_path / "runs"
    old = _old_report(out_dir)
    s1 = R.run(mock=True, no_fewshot=False, out_dir=out_dir, now=WHEN)
    s2 = R.run(mock=True, no_fewshot=False, out_dir=out_dir, now=WHEN)   # 같은 시각에 또 실행
    p1, p2 = Path(s1["report_path"]), Path(s2["report_path"])
    assert p1.name == "report_fewshot_mock_20261105_093000.md"
    assert p2.name == "report_fewshot_mock_20261105_093000_2.md"        # 덮어쓰지 않고 새 이름
    assert p1.with_suffix(".json").exists() and p2.with_suffix(".json").exists()
    assert old.read_text(encoding="utf-8") == "옛 보고서"
    assert not s1["invalid"] and s1["total"] == 40
    # 정답 SQL 을 답으로 → 결과가 있는 문항은 전부 정답 (가상 DB 에는 2025·K리그2 등이 없어 빈 결과 문항이 있음)
    rows = json.loads(p1.with_suffix(".json").read_text(encoding="utf-8"))["rows"]
    assert all(r["correct"] for r in rows if r["status"] == "ok") and s1["correct"] > 0
    assert "측정 시각: 2026-11-05 09:30:00" in p1.read_text(encoding="utf-8")
    assert "무효" not in p1.read_text(encoding="utf-8")


def test_rate_limited_run_is_saved_as_invalid(demo_env, tmp_path):
    out_dir = tmp_path / "runs"
    old = _old_report(out_dir)
    s = R.run(mock=True, no_fewshot=True, out_dir=out_dir, mock_fail_after=5, now=WHEN)
    p = Path(s["report_path"])
    assert p.name == "report_nofewshot_mock_20261105_093000_invalid.md"
    # 첫 429(6번째 문항 q06)에서 즉시 멈춤: q06 은 채점하지 않고, q07 은 부르지도 않는다
    assert s["invalid"] and s["stopped_at"] == "q06" and s["total"] == 5 and s["planned_total"] == 40
    assert s["stop_reason"] == "rate_limit"
    ri = s["rate_limit"]
    assert ri["retry_after"] == "120" and ri["limit_kind"] == "TPD" and ri["limit_name"] == "일일 토큰"
    assert (ri["limit"], ri["used"], ri["requested"]) == (200000, 199000, 3000)
    data = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
    assert data["summary"]["invalid"] is True
    assert "org_secret" not in json.dumps(data, ensure_ascii=False)   # 오류 원문(조직 ID)은 저장 안 함
    assert all(not r["llm_error"] for r in data["rows"])
    md = p.read_text(encoding="utf-8")
    assert "무효 결과" in md and "q06 에서 한도 초과(429)로 즉시 멈춤" in md and "retry-after: 120초" in md
    assert "일일 토큰(TPD)" in md and "측정 불가" in md
    assert old.read_text(encoding="utf-8") == "옛 보고서"
    # 정상 결과와 이름이 겹치지 않음
    assert not list(out_dir.glob("report_nofewshot_mock_20261105_093000.md"))


def test_cli_exit_codes(demo_env, tmp_path, capsys):
    out_dir = tmp_path / "cli"
    assert R.main(["--mock", "--out-dir", str(out_dir)]) == 0
    assert R.main(["--mock-fail-after", "2", "--out-dir", str(out_dir)]) == 2
    names = sorted(p.name for p in out_dir.glob("*.md"))
    assert len(names) == 2 and sum(n.endswith("_invalid.md") for n in names) == 1
    assert all(re.fullmatch(r"report_fewshot_mock_\d{8}_\d{6}(_invalid)?\.md", n) for n in names)
    assert "rate limit" not in capsys.readouterr().out   # 오류 원문은 출력하지 않고 종류만


def test_rate_limit_stops_without_extra_llm_call(demo_env, tmp_path, monkeypatch):
    """429 를 엔진이 오류 답변으로 삼키고 다음 문항을 부르던 버그 방지: 호출은 정확히 fail_after+1 번."""
    made = []
    orig = R.FailingFakeLLM.__init__

    def spy(self, replies, fail_after):
        orig(self, replies, fail_after)
        made.append(self)
    monkeypatch.setattr(R.FailingFakeLLM, "__init__", spy)
    R.run(mock=True, no_fewshot=True, out_dir=tmp_path, mock_fail_after=3, now=WHEN)
    assert len(made[0].calls) == 4


def test_ids_and_token_budget(demo_env, tmp_path):
    s = R.run(mock=True, no_fewshot=True, out_dir=tmp_path / "a", now=WHEN, ids=["q02", "q15", "q26"])
    assert s["total"] == 3 and s["question_ids"] == ["q02", "q15", "q26"] and not s["invalid"]
    # 가짜 LLM 은 문항당 100 토큰 → 예산 250 이면 2문항 뒤 3번째를 부르기 전에 멈춤
    s = R.run(mock=True, no_fewshot=True, out_dir=tmp_path / "b", now=WHEN, ids=["q02", "q15", "q26"],
              token_budget=250)
    assert s["invalid"] and s["stop_reason"] == "budget" and s["total"] == 2 and s["stopped_at"] == "q26"
    with pytest.raises(SystemExit):
        R.run(mock=True, no_fewshot=True, out_dir=tmp_path / "c", now=WHEN, ids=["q99"])
