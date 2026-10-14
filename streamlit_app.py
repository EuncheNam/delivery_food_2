from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ─────────────────────────────────────────────
# 기본 설정
# ─────────────────────────────────────────────
st.set_page_config(page_title="사용자 행동 탐색 대시보드", layout="wide")

# 실행 위치(cwd)와 상관없이, 이 .py 파일이 있는 폴더를 기준으로 CSV를 찾음
BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = "app_events.csv"
SEARCH_DIRS = [BASE_DIR, BASE_DIR / "data", Path.cwd(), Path.cwd() / "data"]


def find_data_path() -> Path:
    for d in SEARCH_DIRS:
        p = d / DATA_FILE
        if p.exists():
            return p
    # 그래도 없으면 프로젝트 폴더 전체에서 검색 (.venv 제외)
    for p in BASE_DIR.rglob(DATA_FILE):
        if ".venv" not in p.parts:
            return p
    st.error(
        f"`{DATA_FILE}` 파일을 찾을 수 없습니다. "
        f"`{BASE_DIR}` 폴더(또는 그 아래 `data/` 폴더)에 파일을 넣어 주세요."
    )
    st.stop()


DATA_PATH = find_data_path()

# 퍼널 단계 (세션 기준) : 이벤트명 → 표시 이름
# ※ search(검색)는 제외: 가게 조회 세션의 약 57%가 검색 없이 진입하므로 필수 선행 단계가 아님
FUNNEL_STEPS = {
    "app_open": "방문",
    "view_restaurant": "가게 조회",
    "add_to_cart": "장바구니",
    "begin_checkout": "결제 시작",
    "order_complete": "주문 완료",
}


# ─────────────────────────────────────────────
# 데이터 로드 & 세션 단위 테이블
# ─────────────────────────────────────────────
@st.cache_data
def load_events(path: str) -> pd.DataFrame:
    df = pd.read_csv(
        path,
        dtype={"customer_id": str, "session_id": str, "order_id": str},
        parse_dates=["event_datetime"],
    )
    # 1) 테스트 계정 제외 : 숫자가 아닌 customer_id (예: QA00001, QA00002)
    df = df[df["customer_id"].str.fullmatch(r"\d+")]

    # 2) 미완성 주 제외 : 마지막 주(월요일 시작)가 7일을 채우지 못하면 해당 주의 세션 제외
    session_start = df.groupby("session_id")["event_datetime"].transform("min")
    week_start = session_start.dt.to_period("W-SUN").dt.start_time
    last_week = week_start.max()
    if df["event_datetime"].max() < last_week + pd.Timedelta(days=7) - pd.Timedelta(seconds=1):
        df = df[week_start < last_week]
    return df


@st.cache_data
def build_sessions(df: pd.DataFrame) -> pd.DataFrame:
    """세션 1행 = 세션 시작 시각, 플랫폼, 유입경로, 퍼널 단계 도달 여부(0/1)"""
    base = df.groupby("session_id").agg(
        session_start=("event_datetime", "min"),
        platform=("platform", "first"),
        traffic_source=("traffic_source", "first"),
    )
    reached = (
        pd.crosstab(df["session_id"], df["event_name"])
        .reindex(columns=list(FUNNEL_STEPS), fill_value=0)
        .gt(0)
        .astype(int)
    )
    out = base.join(reached).reset_index()
    # 주 시작일(월요일)
    out["week"] = out["session_start"].dt.to_period("W-SUN").dt.start_time
    return out


def safe_rate(num: float, den: float) -> float:
    return num / den if den else 0.0


events = load_events(DATA_PATH)
df = build_sessions(events)  # 세션 1행 단위 테이블

# ─────────────────────────────────────────────
# 사이드바 필터
# ─────────────────────────────────────────────
st.sidebar.header("필터")

week_options = sorted(df["week"].unique())
week_start, week_end = st.sidebar.select_slider(
    "기간 (주 시작일)",
    options=week_options,
    value=(week_options[0], week_options[-1]),
    format_func=lambda d: pd.Timestamp(d).strftime("%Y-%m-%d"),
)

platform_options = sorted(df["platform"].unique())
sel_platforms = st.sidebar.multiselect("플랫폼", platform_options, default=platform_options)

source_options = sorted(df["traffic_source"].unique())
sel_sources = st.sidebar.multiselect("유입 경로", source_options, default=source_options)

# 세 필터를 모두 적용한 데이터
f = df[
    df["week"].between(week_start, week_end)
    & df["platform"].isin(sel_platforms)
    & df["traffic_source"].isin(sel_sources)
]

if f.empty:
    st.warning("선택한 조건에 해당하는 데이터가 없습니다. 필터를 바꿔 주세요.")
    st.stop()

period_start = f["session_start"].min().date()
period_end = f["session_start"].max().date()

# ─────────────────────────────────────────────
# 제목
# ─────────────────────────────────────────────
st.title("사용자 행동 탐색 대시보드")
st.caption(f"주별 세션 퍼널 | 플랫폼 · 유입 경로별 | {period_start} ~ {period_end}")

# ─────────────────────────────────────────────
# KPI 카드
# ─────────────────────────────────────────────
n_sessions = len(f)
n_orders = f["order_complete"].sum()
n_checkout = f["begin_checkout"].sum()

order_cvr = safe_rate(n_orders, n_sessions)                         # 방문 → 주문
pay_cvr = safe_rate(
    (f["begin_checkout"] & f["order_complete"]).sum(), n_checkout   # 결제 시작 → 주문
)

k1, k2, k3 = st.columns(3)
k1.metric("세션 수", f"{n_sessions:,}")
k2.metric("주문 전환율 (방문→주문)", f"{order_cvr:.1%}")
k3.metric("결제 전환율 (결제 시작→주문)", f"{pay_cvr:.1%}")

st.divider()

# ─────────────────────────────────────────────
# 차트 1 : 주별 결제 전환율 (플랫폼별)
# ─────────────────────────────────────────────
st.subheader("주별 결제 전환율 (플랫폼별)")

checkout_sessions = f[f["begin_checkout"] == 1]
weekly = (
    checkout_sessions.groupby(["week", "platform"])
    .agg(checkouts=("session_id", "count"), orders=("order_complete", "sum"))
    .reset_index()
)
weekly["pay_cvr"] = weekly["orders"] / weekly["checkouts"]

fig_weekly = px.line(
    weekly,
    x="week",
    y="pay_cvr",
    color="platform",
    markers=True,
    labels={"week": "주 (월요일 시작)", "pay_cvr": "결제 전환율", "platform": "플랫폼"},
    hover_data={"checkouts": ":,", "orders": ":,"},
)
fig_weekly.update_yaxes(tickformat=".0%")
fig_weekly.update_layout(hovermode="x unified", legend_title_text="플랫폼")
st.plotly_chart(fig_weekly, width="stretch")

# ─────────────────────────────────────────────
# 차트 2 : 세션 퍼널 | 차트 3 : 유입 경로별 주문 전환율
# ─────────────────────────────────────────────
left, right = st.columns(2)

with left:
    st.subheader("세션 퍼널")
    funnel_df = pd.DataFrame(
        {
            "단계": list(FUNNEL_STEPS.values()),
            "세션 수": [int(f[step].sum()) for step in FUNNEL_STEPS],
        }
    )
    fig_funnel = go.Figure(
        go.Funnel(
            y=funnel_df["단계"],
            x=funnel_df["세션 수"],
            textinfo="value+percent initial",
        )
    )
    fig_funnel.update_layout(margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig_funnel, width="stretch")

with right:
    st.subheader("유입 경로별 주문 전환율")
    by_source = (
        f.groupby("traffic_source")
        .agg(sessions=("session_id", "count"), orders=("order_complete", "sum"))
        .reset_index()
    )
    by_source["order_cvr"] = by_source["orders"] / by_source["sessions"]
    by_source = by_source.sort_values("order_cvr", ascending=True)

    fig_source = px.bar(
        by_source,
        x="order_cvr",
        y="traffic_source",
        orientation="h",
        text=by_source["order_cvr"].map("{:.1%}".format),
        labels={"order_cvr": "주문 전환율", "traffic_source": "유입 경로"},
        hover_data={"sessions": ":,", "orders": ":,"},
    )
    fig_source.update_xaxes(tickformat=".0%")
    fig_source.update_layout(margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig_source, width="stretch")

# ─────────────────────────────────────────────
# (참고) 지표 정의
# ─────────────────────────────────────────────
with st.expander("지표 정의"):
    st.markdown(
        """
- **세션 수** : 고유 `session_id` 수
- **주문 전환율** : `order_complete`가 발생한 세션 ÷ 전체 세션
- **결제 전환율** : `begin_checkout` 후 `order_complete`까지 간 세션 ÷ `begin_checkout` 세션
- **주** : 세션 시작 시각 기준, 월요일 시작 주
- **제외 데이터** : 테스트 계정(`QA`로 시작하는 customer_id), 7일을 채우지 못한 마지막 주의 세션
"""
    )