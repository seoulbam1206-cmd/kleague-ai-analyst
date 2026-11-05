"""차트 계획 세우기 (Streamlit·Plotly 없이 시험 가능한 순수 로직).

결과 표(DataFrame) + 사용자가 고른 차트 종류·기준(X)·값(Y) → 그릴 데이터와 안내 문구.
그릴 수 없는 조합이면 오류 대신 쉬운 안내 문구를 돌려준다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

CHART_TYPES = ("막대", "꺾은선", "영역", "원형")
PIE_MAX = 12          # 원형 조각 최대 수 (넘으면 상위 PIE_MAX-1 개 + 기타)
BAR_MAX_ROWS = 30     # 막대는 앞 30행까지
SERIES_MAX_ROWS = 100  # 꺾은선·영역은 100점까지
OTHER = "기타"

# 시간 순서가 있는 열 이름 (날짜·라운드·월·연도 등)
_TIME_WORDS = ("날짜", "일자", "일시", "경기일", "date", "라운드", "round", "회차", "월", "month",
               "연도", "년도", "시즌", "season", "year", "주차", "week")
# 기준(X)으로 먼저 고를 열 이름
_X_WORDS = ("선수", "구단", "팀", "이름", "name", "상대", "경기장", "포지션", "국적")


@dataclass
class ChartPlan:
    ok: bool
    kind: str
    x: str | None = None
    y: str | None = None
    data: pd.DataFrame | None = None
    message: str = ""                 # ok=False 일 때 보여줄 안내
    notes: list[str] = field(default_factory=list)   # ok=True 일 때 덧붙일 설명
    time_ordered: bool = False


def numeric_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])]


def is_time_column(df: pd.DataFrame, col: str) -> bool:
    if pd.api.types.is_datetime64_any_dtype(df[col]):
        return True
    name = str(col).lower()
    return any(w in name for w in _TIME_WORDS)


def default_axes(df: pd.DataFrame) -> tuple[str | None, str | None]:
    """기본 기준(X)·값(Y). X: 이름류 글자 열 → 시간 열 → 첫 글자 열 → 첫 열. Y: X 가 아닌 첫 숫자 열."""
    if df is None or df.empty or len(df.columns) == 0:
        return None, None
    nums = numeric_columns(df)
    texts = [c for c in df.columns if c not in nums]
    x = next((c for c in texts if any(w in str(c).lower() for w in _X_WORDS)), None) \
        or next((c for c in df.columns if is_time_column(df, c)), None) \
        or (texts[0] if texts else df.columns[0])
    y = next((c for c in nums if c != x), None)
    return x, y


def _order_key(series: pd.Series) -> pd.Series | None:
    """시간 열을 순서대로 놓기 위한 정렬 값. 숫자 → 그대로, '12R'·'3라운드' → 12·3, 날짜 글자 → 날짜."""
    if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_datetime64_any_dtype(series):
        return series
    s = series.astype(str)
    if s.str.fullmatch(r"\s*\d{4}[-./]\d{1,2}[-./]\d{1,2}.*").all():   # 날짜 모양일 때만 날짜로 읽음
        dates = pd.to_datetime(s.str.slice(0, 10).str.replace(r"[./]", "-", regex=True), errors="coerce")
        if dates.notna().all():
            return dates
    nums = s.map(lambda v: (lambda m: float(m.group()) if m else None)(re.search(r"-?\d+(?:\.\d+)?", v)))
    if nums.notna().all():
        return nums.astype(float)
    return None


def plan_chart(df: pd.DataFrame, kind: str, x: str | None, y: str | None) -> ChartPlan:
    plan = ChartPlan(ok=False, kind=kind, x=x, y=y)
    if kind not in CHART_TYPES:
        plan.message = "지원하지 않는 차트 종류입니다."
        return plan
    if df is None or df.empty:
        plan.message = "결과가 없어 차트를 그릴 수 없습니다."
        return plan
    nums = numeric_columns(df)
    if not nums:
        plan.message = "숫자 열이 없어 차트를 그릴 수 없습니다. 표로 확인해 주세요."
        return plan
    if x not in df.columns or y not in df.columns:
        plan.message = "기준과 값을 골라 주세요."
        return plan
    if x == y:
        plan.message = "기준과 값에 같은 열을 골랐습니다. 서로 다른 열을 골라 주세요."
        return plan
    if y not in nums:
        plan.message = f"값 '{y}' 은 숫자 열이 아니라 차트를 그릴 수 없습니다. 숫자 열을 골라 주세요."
        return plan

    data = df[[x, y]].dropna(subset=[y]).copy()
    if data.empty:
        plan.message = f"'{y}' 에 값이 없어 차트를 그릴 수 없습니다."
        return plan

    if kind in ("꺾은선", "영역"):
        if len(data) < 2:
            plan.message = f"{kind} 차트는 점이 2개 이상 있어야 합니다. 결과가 1행뿐이라 막대 차트를 써 보세요."
            return plan
        if is_time_column(df, x):
            key = _order_key(data[x])
            if key is not None:
                data = data.assign(_k=key.values).sort_values("_k", kind="stable").drop(columns="_k")
                plan.time_ordered = True
                plan.notes.append(f"'{x}' 순서대로 그렸습니다.")
        if len(data) > SERIES_MAX_ROWS:
            data = data.head(SERIES_MAX_ROWS)
            plan.notes.append(f"앞 {SERIES_MAX_ROWS}개 점만 그렸습니다.")
    elif kind == "막대":
        if is_time_column(df, x):
            key = _order_key(data[x])
            if key is not None:
                data = data.assign(_k=key.values).sort_values("_k", kind="stable").drop(columns="_k")
                plan.time_ordered = True
                plan.notes.append(f"'{x}' 순서대로 그렸습니다.")
        if len(data) > BAR_MAX_ROWS:
            data = data.head(BAR_MAX_ROWS)
            plan.notes.append(f"앞 {BAR_MAX_ROWS}행만 그렸습니다.")
        data[x] = data[x].astype(str)   # 숫자 코드도 항목 이름으로 (축 간격이 벌어지지 않게)
    else:  # 원형
        if (data[y] < 0).any():
            plan.message = f"'{y}' 에 음수가 있어 원형 차트로 나타낼 수 없습니다. 막대 차트를 써 보세요."
            return plan
        data[x] = data[x].astype(str)
        data = data.groupby(x, as_index=False, sort=False)[y].sum()
        if data[y].sum() == 0:
            plan.message = f"'{y}' 가 모두 0 이라 원형 차트로 나타낼 수 없습니다."
            return plan
        if len(data) < 2:
            plan.message = "항목이 하나뿐이라 원형 차트가 의미가 없습니다. 막대 차트를 써 보세요."
            return plan
        if len(data) > PIE_MAX:
            n = len(data)
            data = data.sort_values(y, ascending=False, kind="stable")
            top, rest = data.head(PIE_MAX - 1), data.iloc[PIE_MAX - 1:]
            data = pd.concat([top, pd.DataFrame({x: [OTHER], y: [rest[y].sum()]})], ignore_index=True)
            plan.notes.append(f"항목이 {n}개라 상위 {PIE_MAX - 1}개 + '{OTHER}'({n - PIE_MAX + 1}개)로 묶었습니다. "
                              "항목별로 비교하려면 막대 차트가 더 보기 좋습니다.")

    plan.ok, plan.data = True, data.reset_index(drop=True)
    return plan
