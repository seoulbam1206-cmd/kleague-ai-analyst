"""결과 표 차트 (Streamlit + Plotly). 판단 로직은 kleague/charts.py.

- 차트 종류: 막대 / 꺾은선 / 영역 / 원형 (가로 라디오)
- 기준(X): 모든 열, 값(Y): 숫자 열
- 그릴 수 없는 조합은 오류 대신 안내 문구
- 상호작용은 Plotly 기본 기능: 마우스를 올리면 값 표시, 끌어서 확대, 두 번 눌러 원래대로,
  원형 범례를 누르면 그 조각을 숨기거나 다시 보임
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from .charts import CHART_TYPES, default_axes, numeric_columns, plan_chart

BAR_COLOR = "#1D4ED8"
LINE_COLOR = "#0F766E"


def build_figure(plan):
    import plotly.express as px

    d, x, y = plan.data, plan.x, plan.y
    if plan.kind == "막대":
        fig = px.bar(d, x=x, y=y, text=y)
        fig.update_traces(marker_color=BAR_COLOR, textposition="outside", cliponaxis=False)
        fig.update_xaxes(type="category")
    elif plan.kind == "꺾은선":
        fig = px.line(d, x=x, y=y, markers=True)
        fig.update_traces(line_color=LINE_COLOR)
    elif plan.kind == "영역":
        fig = px.area(d, x=x, y=y, markers=True)
        fig.update_traces(line_color=LINE_COLOR)
    else:
        fig = px.pie(d, names=x, values=y, hole=0.4)
        fig.update_traces(textinfo="percent+label", sort=False)
    if plan.kind in ("꺾은선", "영역") and not plan.time_ordered:
        fig.update_xaxes(type="category", categoryorder="array", categoryarray=list(d[x].astype(str)))
    fig.update_layout(height=400, margin=dict(l=10, r=10, t=20, b=10))
    return fig


def render_chart(df: pd.DataFrame, key: str) -> None:
    """결과 표 아래에 차트 영역을 그린다. 1행 이상·2열 이상일 때만."""
    if df is None or df.empty or len(df.columns) < 2:
        return
    with st.expander("📈 차트", expanded=True):
        nums = numeric_columns(df)
        if not nums:
            st.info("숫자 열이 없어 차트를 그릴 수 없습니다. 표로 확인해 주세요.")
            return
        dx, dy = default_axes(df)
        cols = list(df.columns)
        c1, c2, c3 = st.columns([2, 1, 1])
        kind = c1.radio("차트 종류", CHART_TYPES, horizontal=True, key=f"kind_{key}")
        x = c2.selectbox("기준 (X)", cols, index=cols.index(dx) if dx in cols else 0, key=f"x_{key}")
        y_opts = [c for c in nums if c != x] or nums
        y = c3.selectbox("값 (Y)", y_opts, index=y_opts.index(dy) if dy in y_opts else 0, key=f"y_{key}")
        plan = plan_chart(df, kind, x, y)
        if not plan.ok:
            st.info(plan.message)
            return
        st.plotly_chart(build_figure(plan), width="stretch", key=f"fig_{key}")
        for n in plan.notes:
            st.caption(n)
