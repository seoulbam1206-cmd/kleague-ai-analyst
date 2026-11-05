"""차트: 가짜 표로 종류별 시험 (판단 로직 + Streamlit 시험 도구로 실제 화면). LLM·DB 없음."""
import json

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from kleague.charts import OTHER, PIE_MAX, default_axes, plan_chart

TEAMS = pd.DataFrame({"구단": ["울산", "전북", "서울", "포항"], "골": [61, 55, 48, 40], "실점": [30, 35, 41, 44]})
# 라운드가 뒤섞여 있고 '10R' 처럼 글자가 붙은 경우
ROUNDS = pd.DataFrame({"라운드": ["3R", "1R", "10R", "2R"], "골": [7, 5, 9, 6]})
DATES = pd.DataFrame({"경기일": ["2026-05-03", "2026-03-01", "2026-04-12"], "관중": [9000, 15000, 12000]})
MANY = pd.DataFrame({"선수": [f"선수{i:02d}" for i in range(20)], "골": list(range(20, 0, -1))})


# ------------------------------------------------------------ 판단 로직
@pytest.mark.parametrize("kind", ["막대", "꺾은선", "영역", "원형"])
def test_every_kind_draws_team_table(kind):
    p = plan_chart(TEAMS, kind, "구단", "골")
    assert p.ok and len(p.data) == 4 and set(p.data.columns) == {"구단", "골"}


def test_default_axes_pick_name_and_first_number():
    assert default_axes(TEAMS) == ("구단", "골")
    assert default_axes(ROUNDS) == ("라운드", "골")


@pytest.mark.parametrize("kind", ["꺾은선", "영역"])
def test_rounds_are_drawn_in_order(kind):
    p = plan_chart(ROUNDS, kind, "라운드", "골")
    assert p.ok and p.time_ordered
    assert list(p.data["라운드"]) == ["1R", "2R", "3R", "10R"]   # 글자 순서(1,10,2,3)가 아니라 숫자 순서
    assert list(p.data["골"]) == [5, 6, 7, 9]


def test_dates_are_drawn_in_order():
    p = plan_chart(DATES, "꺾은선", "경기일", "관중")
    assert list(p.data["경기일"]) == ["2026-03-01", "2026-04-12", "2026-05-03"]


def test_non_time_line_keeps_table_order():
    p = plan_chart(TEAMS, "꺾은선", "구단", "골")
    assert not p.time_ordered and list(p.data["구단"]) == ["울산", "전북", "서울", "포항"]


def test_pie_groups_extra_items_into_other():
    p = plan_chart(MANY, "원형", "선수", "골")
    assert p.ok and len(p.data) == PIE_MAX
    assert p.data["선수"].iloc[-1] == OTHER
    assert p.data["골"].sum() == MANY["골"].sum()               # 합계는 그대로
    assert any("막대 차트" in n for n in p.notes)              # 막대를 권함


@pytest.mark.parametrize("df, kind, x, y, words", [
    (pd.DataFrame({"구단": ["울산", "전북"], "감독": ["가", "나"]}), "원형", "구단", "감독", "숫자 열이 없어"),
    (TEAMS.head(1), "꺾은선", "구단", "골", "2개 이상"),
    (TEAMS.head(1), "영역", "구단", "골", "2개 이상"),
    (pd.DataFrame({"구단": ["울산", "전북"], "득실": [5, -3]}), "원형", "구단", "득실", "음수"),
    (pd.DataFrame({"구단": ["울산", "전북"], "퇴장": [0, 0]}), "원형", "구단", "퇴장", "모두 0"),
    (TEAMS.head(1), "원형", "구단", "골", "항목이 하나뿐"),
    (TEAMS, "막대", "골", "골", "같은 열"),
    (TEAMS, "막대", "골", "구단", "숫자 열이 아니라"),
    (TEAMS.iloc[0:0], "막대", "구단", "골", "결과가 없어"),
])
def test_bad_combinations_give_plain_message(df, kind, x, y, words):
    p = plan_chart(df, kind, x, y)
    assert not p.ok and words in p.message and p.data is None


def test_one_row_bar_is_fine():
    p = plan_chart(TEAMS.head(1), "막대", "구단", "골")
    assert p.ok and len(p.data) == 1


def test_bar_limits_rows_and_numeric_codes_become_labels():
    df = pd.DataFrame({"코드": list(range(100, 140)), "골": list(range(40))})
    p = plan_chart(df, "막대", "코드", "골")
    assert p.ok and len(p.data) == 30 and all(isinstance(v, str) for v in p.data["코드"])


# ------------------------------------------------------------ 실제 화면 (Streamlit 시험 도구)
def _chart_app(rows, cols):
    import pandas as pd

    from kleague.chart_ui import render_chart
    render_chart(pd.DataFrame(rows, columns=cols), "t")


def _run(df: pd.DataFrame, kind: str | None = None, x: str | None = None, y: str | None = None) -> AppTest:
    at = AppTest.from_function(_chart_app, args=(df.values.tolist(), list(df.columns)), default_timeout=30).run()
    assert not at.exception
    if kind:
        at.radio[0].set_value(kind)
    if x:
        at.selectbox[0].set_value(x)
    if y:
        at.selectbox[1].set_value(y)
    if kind or x or y:
        at.run()
    assert not at.exception
    return at


def _plain(v):
    """Plotly 7 은 숫자 배열을 {dtype, bdata}(base64) 로 담는다 → 일반 리스트로."""
    if isinstance(v, dict) and "bdata" in v:
        import base64

        import numpy as np
        return np.frombuffer(base64.b64decode(v["bdata"]), dtype=v["dtype"]).tolist()
    return v


def _traces(at: AppTest) -> list[dict]:
    charts = at.get("plotly_chart")
    if not charts:
        return []
    return [{k: _plain(v) for k, v in tr.items()} for tr in json.loads(charts[0].proto.spec)["data"]]


@pytest.mark.parametrize("kind, trace_type", [("막대", "bar"), ("꺾은선", "scatter"), ("영역", "scatter"), ("원형", "pie")])
def test_screen_each_kind(kind, trace_type):
    at = _run(TEAMS, kind)
    assert at.radio[0].options == ["막대", "꺾은선", "영역", "원형"]
    assert [s.label for s in at.selectbox] == ["기준 (X)", "값 (Y)"]
    tr = _traces(at)
    assert len(tr) == 1 and tr[0]["type"] == trace_type
    if kind == "영역":
        assert tr[0].get("fill") or tr[0].get("stackgroup")       # 영역 = 채운 선
    if kind == "원형":
        assert tr[0]["labels"] == ["울산", "전북", "서울", "포항"] and tr[0]["hole"] == 0.4


def test_screen_default_is_bar_with_name_axis():
    at = _run(TEAMS)
    assert at.radio[0].value == "막대"
    assert at.selectbox[0].value == "구단" and at.selectbox[1].value == "골"
    assert _traces(at)[0]["x"] == ["울산", "전북", "서울", "포항"]


def test_screen_change_y_axis():
    at = _run(TEAMS, "막대", y="실점")
    assert _traces(at)[0]["y"] == [30, 35, 41, 44]


def test_screen_line_rounds_in_order():
    at = _run(ROUNDS, "꺾은선")
    tr = _traces(at)[0]
    assert tr["x"] == ["1R", "2R", "3R", "10R"] and tr["y"] == [5, 6, 7, 9]
    assert any("순서대로" in str(c.value) for c in at.caption)


def test_screen_pie_many_items_shows_other_and_tip():
    at = _run(MANY, "원형")
    tr = _traces(at)[0]
    assert len(tr["labels"]) == PIE_MAX and tr["labels"][-1] == OTHER
    assert any("막대 차트" in str(c.value) for c in at.caption)


@pytest.mark.parametrize("df, kind, words", [
    (TEAMS.head(1), "꺾은선", "2개 이상"),
    (pd.DataFrame({"구단": ["울산", "전북"], "득실": [5, -3]}), "원형", "음수"),
])
def test_screen_bad_combination_shows_message_not_error(df, kind, words):
    at = _run(df, kind)
    assert not at.get("plotly_chart")
    assert any(words in str(i.value) for i in at.info)


def test_screen_no_numeric_column_message():
    at = _run(pd.DataFrame({"구단": ["울산", "전북"], "감독": ["가", "나"]}))
    assert not at.radio and not at.get("plotly_chart")
    assert any("숫자 열이 없어" in str(i.value) for i in at.info)


def test_screen_single_column_shows_nothing():
    at = _run(pd.DataFrame({"골": [3]}))
    assert not at.get("plotly_chart") and not at.info and not at.radio
