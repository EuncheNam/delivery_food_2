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

# ─────────────────────────────────────────────
# 집단 비교
# ─────────────────────────────────────────────
st.divider()
st.subheader("집단 비교")

GROUP_COLS = {"플랫폼": "platform", "유입경로": "traffic_source"}
CMP_STAGES = {
    "app_open": "세션수",
    "view_restaurant": "조회세션",
    "add_to_cart": "장바구니세션",
    "begin_checkout": "결제시작세션",
    "order_complete": "주문세션",
}
MIN_GROUP_SESSIONS = 500

cmp_label = st.radio("비교 기준", list(GROUP_COLS), horizontal=True)
cmp_col = GROUP_COLS[cmp_label]

# 집단별 퍼널 5단계 세션 수 (long 형태: 집단 × 단계)
cmp_wide = f.groupby(cmp_col)[list(CMP_STAGES)].sum()
cmp_long = (
    cmp_wide.rename(columns=CMP_STAGES)
    .reset_index()
    .melt(id_vars=cmp_col, var_name="단계", value_name="세션")
)
stage_order = list(CMP_STAGES.values())
cmp_long["단계"] = pd.Categorical(cmp_long["단계"], categories=stage_order, ordered=True)
cmp_long = cmp_long.sort_values([cmp_col, "단계"]).reset_index(drop=True)

# 누적 전환율(첫 단계 대비 %) / 단계 전환율(직전 단계 대비 %)
first_stage = cmp_long.groupby(cmp_col)["세션"].transform("first")
cmp_long["기준세션"] = cmp_long.groupby(cmp_col)["세션"].shift(1)
cmp_long["누적전환율"] = cmp_long["세션"] / first_stage * 100
cmp_long["단계전환율"] = cmp_long["세션"] / cmp_long["기준세션"] * 100

cmp_left, cmp_right = st.columns(2)

with cmp_left:
    st.markdown("**누적 전환율 (첫 단계 대비 %)**")
    fig_cmp_funnel = px.funnel(
        cmp_long,
        x="누적전환율",
        y="단계",
        color=cmp_col,
        text=cmp_long["누적전환율"].map("{:.1f}%".format),
        category_orders={"단계": stage_order},
        labels={cmp_col: cmp_label},
        hover_data={"세션": ":,", "누적전환율": ":.1f"},
    )
    fig_cmp_funnel.update_traces(textinfo="text", textposition="inside")
    # px가 y축 카테고리를 뒤집어 저장하므로, 위→아래 '세션수 → 주문세션' 순서로 다시 지정
    fig_cmp_funnel.update_yaxes(categoryorder="array", categoryarray=stage_order)
    fig_cmp_funnel.update_layout(funnelmode="group", margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig_cmp_funnel, width="stretch")

with cmp_right:
    st.markdown("**단계 전환율 (직전 단계 대비 %)**")
    step_df = cmp_long.dropna(subset=["기준세션"]).copy()
    step_df["기준세션"] = step_df["기준세션"].astype(int)
    fig_cmp_step = px.bar(
        step_df,
        x="단계",
        y="단계전환율",
        color=cmp_col,
        barmode="group",
        text=step_df["단계전환율"].map("{:.1f}".format),
        category_orders={"단계": stage_order[1:]},
        labels={cmp_col: cmp_label, "단계전환율": "단계 전환율 (%)"},
        hover_data={"기준세션": ":,", "세션": ":,", "단계전환율": ":.1f"},
    )
    fig_cmp_step.update_traces(textposition="outside", cliponaxis=False)
    fig_cmp_step.update_layout(margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig_cmp_step, width="stretch")

# 세션 수가 적은 집단 안내
small_groups = cmp_wide.loc[cmp_wide["app_open"] < MIN_GROUP_SESSIONS, "app_open"]
if not small_groups.empty:
    detail = ", ".join(f"{name}({int(n):,}세션)" for name, n in small_groups.items())
    st.caption(
        f"⚠️ 세션 수가 {MIN_GROUP_SESSIONS:,}개 미만인 집단이 있습니다: {detail}. "
        "이 집단의 전환율은 값이 크게 흔들릴 수 있으니 해석에 주의하세요."
    )

# ─────────────────────────────────────────────
# 드릴다운: 언제부터, 무엇 때문일까
# ─────────────────────────────────────────────
st.divider()
st.subheader("드릴다운: 언제부터, 무엇 때문일까")

DRILL_STEPS = {
    "방문 → 식당조회": ("app_open", "view_restaurant"),
    "식당조회 → 장바구니": ("view_restaurant", "add_to_cart"),
    "장바구니 → 결제시작": ("add_to_cart", "begin_checkout"),
    "결제시작 → 주문": ("begin_checkout", "order_complete"),
}

# 비교 기준이 아닌 다른 축 (플랫폼 ↔ 유입경로)
other_label = next(label for label in GROUP_COLS if label != cmp_label)
other_col = GROUP_COLS[other_label]

sel_left, sel_right = st.columns(2)
with sel_left:
    drill_group = st.selectbox(
        f"자세히 볼 집단 ({cmp_label})", sorted(f[cmp_col].unique())
    )
with sel_right:
    drill_step = st.selectbox("단계", list(DRILL_STEPS))
prev_stage, cur_stage = DRILL_STEPS[drill_step]

drill_weeks = sorted(f["week"].unique())  # f의 기간 = 두 차트의 공통 기간

# ── 차트 ① : 고른 집단의 주별 단계 전환율, 다른 축별로 쪼개 보기
st.markdown(f"**① {drill_group}의 주별 {drill_step} 전환율 — {other_label}별로 쪼개 보기**")

g = f[f[cmp_col] == drill_group]
drill_rate = g.groupby(["week", other_col])[[prev_stage, cur_stage]].sum()
# 데이터가 없는 (주, 갈래) 조합도 빈 값으로 남겨서 선이 이어지지 않게 함
full_idx = pd.MultiIndex.from_product(
    [drill_weeks, sorted(g[other_col].unique())], names=["week", other_col]
)
drill_rate = drill_rate.reindex(full_idx).reset_index()
drill_rate = drill_rate.rename(columns={prev_stage: "기준세션", cur_stage: "전환세션"})
# 직전 단계 세션이 0인 주는 0%로 채우지 않고 빈 값(NaN)으로 둠
drill_rate["전환율"] = (
    drill_rate["전환세션"] / drill_rate["기준세션"].where(drill_rate["기준세션"] > 0) * 100
)

fig_drill_rate = px.line(
    drill_rate,
    x="week",
    y="전환율",
    color=other_col,
    markers=True,
    labels={"week": "주 (월요일 시작)", "전환율": f"{drill_step} 전환율 (%)", other_col: other_label},
    hover_data={"기준세션": ":,", "전환세션": ":,", "전환율": ":.1f"},
)
fig_drill_rate.update_traces(connectgaps=False)
fig_drill_rate.update_layout(hovermode="x unified", margin=dict(l=10, r=10, t=10, b=10))
st.plotly_chart(fig_drill_rate, width="stretch")
st.caption("모든 갈래가 같은 시점에 함께 움직였다면, 그 축은 원인이 아닙니다.")

# ── 차트 ② : 같은 기간 결제실패 건수, 고른 집단 vs 그 외
st.markdown(f"**② 주별 결제실패 건수 — {drill_group} vs 그 외**")

# 결제실패는 세션 테이블에 없는 이벤트이므로, f에 남은 세션의 이벤트만 골라 건수를 셈
fail_events = events.loc[
    (events["event_name"] == "payment_fail") & events["session_id"].isin(f["session_id"]),
    ["session_id"],
]
fail = fail_events.merge(f[["session_id", "week", cmp_col]], on="session_id", how="left")
fail["집단"] = fail[cmp_col].where(fail[cmp_col] == drill_group, "그 외")

group_names = [drill_group, "그 외"]
fail_weekly = (
    fail.groupby(["week", "집단"])
    .size()
    .reindex(pd.MultiIndex.from_product([drill_weeks, group_names], names=["week", "집단"]), fill_value=0)
    .rename("결제실패")
    .reset_index()
)

fig_drill_fail = px.bar(
    fail_weekly,
    x="week",
    y="결제실패",
    color="집단",
    barmode="group",
    category_orders={"집단": group_names},
    color_discrete_map={drill_group: "#B71C1C", "그 외": "#9E9E9E"},
    labels={"week": "주 (월요일 시작)", "결제실패": "결제실패 건수"},
    hover_data={"결제실패": ":,"},
)
fig_drill_fail.update_layout(hovermode="x unified", margin=dict(l=10, r=10, t=10, b=10))
st.plotly_chart(fig_drill_fail, width="stretch")
st.caption("문제가 생긴 시점과 함께 움직였다면 원인 후보입니다. 함께 움직였다고 원인이 확정되지는 않습니다.")