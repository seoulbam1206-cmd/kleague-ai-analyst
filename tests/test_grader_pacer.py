"""채점기 관대 채점의 스코어 글자 처리, 속도 조절 어림값 (LLM 없음)."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
import run_eval as R  # noqa: E402

GOLD = pd.DataFrame({"home_goal": [2, 0, 4], "away_goal": [5, 7, 3]})


def test_score_text_counts_only_in_lenient():
    ai = pd.DataFrame({"홈팀": ["광주", "안양", "광주"], "스코어": ["2-5", "0-7", "4-3"]})
    assert not R.results_match(GOLD, ai, False)                 # 엄격: 그대로 오답
    assert R.results_match(GOLD, ai, False, lenient=True)       # 관대: (앞, 뒤) = (홈, 원정) 짝이 같음


def test_score_text_variants_and_order():
    ai = pd.DataFrame({"스코어": ["4 : 3", "2-5", "0-7"]})      # 공백·콜론, 행 순서 무관
    assert R.results_match(GOLD, ai, False, lenient=True)
    assert not R.results_match(GOLD, ai, True, lenient=True)    # 순서가 중요하면 행 순서도 맞아야 함


def test_wrong_score_text_still_wrong():
    assert not R.results_match(GOLD, pd.DataFrame({"스코어": ["2-5", "0-7", "4-2"]}), False, lenient=True)
    assert not R.results_match(GOLD, pd.DataFrame({"스코어": ["2-5", "0-7"]}), False, lenient=True)


def test_swapped_home_away_score_is_wrong():
    """홈·원정이 뒤바뀐 '5-2' 는 관대 채점에서도 오답 (2026-11-05 수정)."""
    swapped = pd.DataFrame({"스코어": ["5-2", "7-0", "3-4"]})
    assert not R.results_match(GOLD, swapped, True, lenient=True)
    assert not R.results_match(GOLD, swapped, False, lenient=True)
    assert not R.results_match(GOLD, pd.DataFrame({"스코어": ["5-2", "0-7", "4-3"]}), False, lenient=True)  # 한 줄만 뒤바뀜


def test_rows_mixed_pairs_are_wrong():
    """행마다 짝째로 비교한다: 앞 숫자 묶음·뒤 숫자 묶음이 각각 같아도 짝이 섞이면 오답."""
    mixed = pd.DataFrame({"스코어": ["2-7", "0-3", "4-5"]})     # 앞 {2,0,4}, 뒤 {7,3,5} 는 같지만 짝이 다름
    assert not R.results_match(GOLD, mixed, False, lenient=True)


def test_score_text_needs_home_away_gold_columns():
    """정답 컬럼 이름으로 홈·원정을 알 수 없으면 스코어 글자를 인정하지 않는다 (아무것도 안 늘림)."""
    g = pd.DataFrame({"a": [2, 0], "b": [5, 7]})
    assert not R.results_match(g, pd.DataFrame({"스코어": ["2-5", "0-7"]}), False, lenient=True)


def test_mixed_column_is_not_split():
    """모든 값이 '숫자-숫자' 일 때만 나눈다."""
    assert R.split_score_columns(pd.DataFrame({"x": ["2-5", "무승부"]})) == {}
    assert R.split_score_columns(pd.DataFrame({"x": [1, 2]})) == {}


def test_separate_columns_compared_as_home_away_pairs():
    """정답에 홈·원정 컬럼이 둘 다 있으면, AI 의 숫자 컬럼 두 개도 '같은 줄의 (홈, 원정) 짝'으로 비교 (엄격·관대 모두)."""
    right = pd.DataFrame({"홈득점": [2, 0, 4], "원정득점": [5, 7, 3]})
    assert R.results_match(GOLD, right, True) and R.results_match(GOLD, right, True, lenient=True)
    # 이름은 홈/원정인데 값이 뒤바뀜 ('5-2') → 오답
    swapped = pd.DataFrame({"홈득점": [5, 7, 3], "원정득점": [2, 0, 4]})
    assert not R.results_match(GOLD, swapped, True)
    assert not R.results_match(GOLD, swapped, False, lenient=True)
    # 컬럼마다 값 묶음은 같지만 줄끼리 짝이 섞임 → 오답 (예전에는 order_matters=false 에서 통과했음)
    mixed = pd.DataFrame({"홈득점": [2, 0, 4], "원정득점": [7, 3, 5]})
    assert not R.results_match(GOLD, mixed, False)
    assert not R.results_match(GOLD, mixed, False, lenient=True)


def test_pair_rule_with_extra_columns():
    """짝 비교 뒤 나머지 정답 숫자 컬럼(예: 관중)은 기존처럼 따로 비교한다."""
    gold = pd.DataFrame({"home_goal": [0, 1], "away_goal": [0, 0], "audience": [35729, 34068]})
    ok = pd.DataFrame({"홈팀": ["서울", "서울"], "홈": [0, 1], "원정": [0, 0], "관중": [35729, 34068]})
    assert R.results_match(gold, ok, True)
    assert not R.results_match(gold, pd.DataFrame({"관중": [35729, 34068]}), True)   # 스코어 없음 → 오답 (q03)


def test_pair_rule_limit_unnamed_columns():
    """한계: 이름으로 홈·원정을 알 수 없는 컬럼(x, y)은 값으로만 짝을 찾는다. 줄마다 짝은 맞아야 한다."""
    assert R.results_match(GOLD, pd.DataFrame({"x": [5, 7, 3], "y": [2, 0, 4]}), True)      # (y, x) 짝이 맞음
    assert not R.results_match(GOLD, pd.DataFrame({"x": [2, 0, 4], "y": [7, 3, 5]}), False)  # 짝이 섞이면 오답


def test_gold_without_pair_unchanged():
    """정답에 홈·원정 컬럼 짝이 없으면 기존 비교 그대로."""
    g = pd.DataFrame({"goals": [3, 2], "assists": [1, 4]})
    assert R.results_match(g, pd.DataFrame({"a": [1, 4], "b": [3, 2]}), True)
    g1 = pd.DataFrame({"home_goal": [2, 0]})          # 홈 컬럼만 있음
    assert R.results_match(g1, pd.DataFrame({"x": [2, 0]}), True)


def test_pacer_estimate_is_max_of_recent():
    p = R.TokenPacer(8000)
    assert p.last == R.TokenPacer.INITIAL_ESTIMATE
    for t in (3000, 5000, 2000):
        p.add(t)
    assert p.last == 5000            # 직전(2000)이 아니라 최근 중 가장 큰 값
    p2 = R.TokenPacer(8000)
    p2.add(1500)
    assert p2.last == R.TokenPacer.INITIAL_ESTIMATE   # 바닥값보다 작아지지 않음


def test_pacer_waits_so_minute_total_stays_under_limit(monkeypatch):
    """분당 합계 + 다음 어림값이 8,000 을 넘으면 기다린다. 2026-11-05 상황(쓴 양 4,593, 직전 문항 2,155) 재현."""
    clock = [1000.0]
    monkeypatch.setattr(R.time, "monotonic", lambda: clock[0])
    slept = []

    def fake_sleep(sec):
        slept.append(sec)
        clock[0] += sec
    monkeypatch.setattr(R.time, "sleep", fake_sleep)

    p = R.TokenPacer(8000)
    p.add(2438)            # 앞 문항
    clock[0] += 5
    p.add(2155)            # 짧은 직전 문항 → 예전 방식이면 어림값 2,155 로 4,593 + 2,155 ≤ 8,000 이라 바로 보냄
    assert p.last == R.TokenPacer.INITIAL_ESTIMATE
    waited = p.wait()
    assert waited > 0 and slept  # 4,593 + 3,600 > 8,000 → 기다림
    in_window = sum(t for ts, t in p.used if clock[0] - ts <= p.window)
    assert in_window + p.last <= 8000


def test_pacer_no_wait_when_room():
    p = R.TokenPacer(8000)
    p.add(3000)
    assert p.wait() == 0.0
