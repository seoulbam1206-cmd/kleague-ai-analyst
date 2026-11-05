import pandas as pd

from eval.run_eval import results_match
from kleague.fewshot import Example, ExampleIndex
from kleague.llm import FakeLLM
from kleague.ratelimit import SlidingWindowLimiter
from kleague.report import fact_check, load_facts, write_report


def test_results_match_ignores_alias_and_extra_columns():
    gold = pd.DataFrame({"player_name": ["A", "B"], "g": [5, 3]})
    pred = pd.DataFrame({"선수명": ["B", "A"], "구단": ["x", "y"], "득점": [3, 5]})
    assert results_match(gold, pred, order_matters=False)
    assert not results_match(gold, pred, order_matters=True)
    assert not results_match(gold, pred.head(1), order_matters=False)


def test_fewshot_ranks_similar_question_first():
    idx = ExampleIndex(min_score=0.1)
    idx.fit([Example("2026 K리그1 득점 순위", "S1"), Example("구단별 평균 관중", "S2"),
             Example("경고 많이 받은 구단", "S3")])
    hits = idx.search("올해 득점 순위 알려줘")
    assert hits and hits[0][0].sql == "S1"
    assert ExampleIndex(min_score=0.9).search("아무거나") == []


def test_rate_limiter():
    lim = SlidingWindowLimiter(2, 60)
    assert lim.allow(0)[0] and lim.allow(1)[0]
    ok, wait = lim.allow(2)
    assert not ok and wait > 0
    assert lim.allow(61)[0]


def test_report_rule_based_and_llm(ro_db):
    f = load_facts(ro_db, 2026, 1, 1)
    assert f is not None and len(f.teams) == 2
    r = write_report(ro_db, None, 2026, 1, 1)
    assert not r.by_llm and str(f.header["home_goal"]) in r.title
    r2 = write_report(ro_db, FakeLLM(["경기는 9-9로 끝났다."]), 2026, 1, 1)
    assert r2.by_llm and r2.warnings  # 잘못된 스코어 → 팩트체크 경고


def test_fact_check_ignores_formation_and_time(ro_db):
    f = load_facts(ro_db, 2026, 1, 1)
    hg, ag = f.header["home_goal"], f.header["away_goal"]
    text = f"4-4-2 포메이션, 14:00 킥오프, 최종 {hg}-{ag}."
    assert fact_check(text, f) == []


def test_app_store_roundtrip(app_store):
    app_store.add_example("q", "SELECT 1", approved=False, source="feedback")
    assert app_store.approved_examples() == []
    (eid, _, _), = app_store.pending_examples()
    app_store.set_example_approved(eid, True)
    assert app_store.approved_examples() == [("q", "SELECT 1")]
    app_store.add_usage(100)
    app_store.add_usage(50)
    assert app_store.tokens_today() == 150
    lid = app_store.log_query("q", "SELECT 1", "ok", 1, 3, 120, 150, False)
    app_store.set_feedback(lid, 1)
    assert app_store.stats()["total"] == 1
