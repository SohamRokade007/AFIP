from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


# Resolve paths from this file so Streamlit Cloud and local runs behave the same.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data" / "processed"

st.set_page_config(
    page_title="Airfare Price Intelligence | AFIP",
    page_icon="✈️",
    layout="wide",
)

st.markdown(
    """
    <style>
    .main { background-color: #0e1117; }
    .stMetric {
        background-color: #161b22;
        padding: 15px;
        border-radius: 8px;
        border: 1px solid #30363d;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Airfare Price Index & Intelligence Dashboard")
st.caption(
    "AFIP — an independent academic/research prototype for domestic airfare "
    "monitoring in India. Not an official Government of India or MoSPI service."
)
st.markdown("---")


@st.cache_data(ttl=600)
def load_data():
    """Load the repository's committed datasets. Cache briefly for dashboard use."""
    required_files = {
        "weights": DATA_DIR / "sector_weights.csv",
        "daily": DATA_DIR / "apix_daily_series.csv",
        "mospi": DATA_DIR / "mospi_benchmark_clean.csv",
        "quotes": DATA_DIR / "live_scraped_quotes_real.csv",
    }
    missing = [str(path.relative_to(PROJECT_ROOT)) for path in required_files.values() if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing required data files: " + ", ".join(missing))

    weights = pd.read_csv(required_files["weights"])
    daily = pd.read_csv(required_files["daily"])
    mospi = pd.read_csv(required_files["mospi"])
    quotes = pd.read_csv(required_files["quotes"])

    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
    mospi["date"] = pd.to_datetime(mospi["date"], errors="coerce")
    daily = daily.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    mospi = mospi.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    forecast_path = DATA_DIR / "apix_forecast_30d.csv"
    forecast = pd.read_csv(forecast_path) if forecast_path.exists() else pd.DataFrame()
    if not forecast.empty and "date" in forecast.columns:
        forecast["date"] = pd.to_datetime(forecast["date"], errors="coerce")
        forecast = forecast.dropna(subset=["date"]).sort_values("date")

    # Provider-specific observations share the schema but remain separated by source_platform.
    # Do not substitute the older generated/synthetic quote file here.
    route_code_map = {
        "DEL-BOM": "DELHI-MUMBAI",
        "DEL-BLR": "DELHI-BANGALORE",
        "BOM-BLR": "MUMBAI-BANGALORE",
        "DEL-HYD": "DELHI-HYDERABAD",
        "DEL-CCU": "DELHI-KOLKATA",
    }
    if not quotes.empty:
        quotes["sector"] = quotes["sector"].astype(str).replace(route_code_map)
        if "parsed_fare_inr" in quotes.columns:
            quotes["parsed_fare_inr"] = pd.to_numeric(quotes["parsed_fare_inr"], errors="coerce")
        if "lead_time_days" in quotes.columns:
            quotes["lead_time_days"] = pd.to_numeric(quotes["lead_time_days"], errors="coerce")
        if "scrape_timestamp" in quotes.columns:
            quotes["scrape_timestamp"] = pd.to_datetime(quotes["scrape_timestamp"], errors="coerce")
        if "status" in quotes.columns:
            quotes = quotes[quotes["status"].eq("ok")].copy()
        quotes = quotes.dropna(subset=["parsed_fare_inr", "lead_time_days"])
        quotes = quotes[quotes["parsed_fare_inr"] > 0]

    return weights, daily, mospi, quotes, forecast


try:
    df_weights, df_daily, df_mospi, df_quotes, df_forecast = load_data()
except Exception as exc:
    st.error(f"The dashboard could not load its datasets: {exc}")
    st.stop()

if df_daily.empty or df_weights.empty:
    st.error("The index or route-weight dataset is empty. Please check the processed data files.")
    st.stop()

# Sidebar controls.
with st.sidebar:
    st.header("Dashboard Controls")
    if st.button("Refresh dashboard data", width="stretch"):
        st.cache_data.clear()
        st.rerun()

    st.caption("Dashboard cache refreshes automatically every 10 minutes.")
    st.markdown("---")
    st.header("Route Filter")
    all_sectors = sorted(df_weights["sector"].dropna().unique().tolist())
    selected_sector = st.selectbox(
        "Select flight corridor",
        options=["All Corridors (National Composite)"] + all_sectors,
    )
    st.markdown("---")
    st.caption("Verified fare collection runs separately through the scheduled Ignav API workflow.")

# Current data coverage and freshness.
today_ist = pd.Timestamp.now(tz="Asia/Kolkata").date()
latest_index_date = df_daily["date"].max().date()
latest_quote_date = None
if not df_quotes.empty and "scrape_timestamp" in df_quotes.columns:
    latest_scrape = df_quotes["scrape_timestamp"].max()
    if pd.notna(latest_scrape):
        latest_quote_date = latest_scrape.date()

if latest_index_date < today_ist:
    st.warning(
        f"The modelled APIx series currently ends on {latest_index_date:%d %b %Y}. "
        "It is historical reference data, not a live index calculated from today's scraped fares."
    )

if latest_quote_date is None:
    st.warning("No successful scraped airfare observations are available in the current dataset.")
elif latest_quote_date < today_ist:
    st.warning(
        f"The latest successful scraped fare observations are dated {latest_quote_date:%d %b %Y}. "
        "The airfare observations are stale until the scraper runs successfully again."
    )
else:
    st.success(f"Latest successful scraped fare observations: {latest_quote_date:%d %b %Y} (India time/date).")

st.info(
    "Methodology note: the historical APIx series and forecast are still modelled outputs, "
    "not a live index calculated from these fares. The booking-window chart separates data "
    "providers so legacy Ixigo observations are not silently pooled with new Ignav API results."
)

# -----------------------------------------------------------------------------
# 1. Executive summary
# -----------------------------------------------------------------------------
st.markdown("## 1. Executive Summary")
latest_apix = float(df_daily["apix_daily"].iloc[-1])
prev_apix = float(df_daily["apix_daily"].iloc[-2]) if len(df_daily) > 1 else latest_apix
pct_change = ((latest_apix - prev_apix) / prev_apix * 100) if prev_apix else 0.0

latest_mospi = float(df_mospi["mospi_cpi_airfare"].iloc[-1]) if not df_mospi.empty else float("nan")
latest_inflation = float(df_mospi["inflation"].iloc[-1]) if not df_mospi.empty and "inflation" in df_mospi else float("nan")

k1, k2, k3, k4 = st.columns(4)
k1.metric("Latest modelled APIx", f"{latest_apix:.2f}", f"{pct_change:+.2f}% vs previous day")
k2.metric("Latest MoSPI airfare CPI in file", f"{latest_mospi:.2f}" if pd.notna(latest_mospi) else "N/A")
k3.metric("MoSPI inflation in file", f"{latest_inflation:.2f}%" if pd.notna(latest_inflation) else "N/A")
k4.metric("Verified fare observations", f"{len(df_quotes):,}")

st.caption(
    f"Modelled APIx data coverage: {df_daily['date'].min():%d %b %Y} to "
    f"{df_daily['date'].max():%d %b %Y}."
)
st.markdown("---")

# -----------------------------------------------------------------------------
# 2. Modelled daily index vs monthly benchmark
# -----------------------------------------------------------------------------
st.markdown("## 2. Modelled Daily Index vs Monthly CPI Benchmark")
st.caption(
    "The daily historical series is generated by the current index model. "
    "The monthly CPI line is the benchmark dataset committed with this project."
)

if selected_sector == "All Corridors (National Composite)":
    y_data = df_daily["apix_daily"]
else:
    matching_col = next(
        (col for col in df_daily.columns if col.strip().upper() == selected_sector.strip().upper()),
        None,
    )
    y_data = df_daily[matching_col] if matching_col else df_daily["apix_daily"]

fig_trend = go.Figure()
fig_trend.add_trace(
    go.Scatter(
        x=df_daily["date"],
        y=y_data,
        mode="lines",
        name="Modelled APIx",
        line=dict(color="#00d2ff", width=1.8),
        hovertemplate="<b>Date</b>: %{x|%d %b %Y}<br><b>Index</b>: %{y:.2f}<extra></extra>",
    )
)
if selected_sector == "All Corridors (National Composite)" and not df_mospi.empty:
    fig_trend.add_trace(
        go.Scatter(
            x=df_mospi["date"],
            y=df_mospi["mospi_cpi_airfare"],
            mode="lines+markers",
            name="MoSPI CPI airfare benchmark",
            marker=dict(size=7, color="#ff3366", symbol="diamond"),
            line=dict(color="#ff3366", dash="dash", width=2),
            hovertemplate="<b>Month</b>: %{x|%B %Y}<br><b>CPI</b>: %{y:.2f}<extra></extra>",
        )
    )
fig_trend.update_layout(
    template="plotly_dark",
    hovermode="x unified",
    xaxis_title="Date",
    yaxis_title="Index value (base 2024 = 100)",
    margin=dict(l=20, r=20, t=30, b=20),
    legend=dict(orientation="h", y=1.12, x=0),
)
st.plotly_chart(fig_trend, width="stretch")
st.markdown("---")

# -----------------------------------------------------------------------------
# 3. Forecast
# -----------------------------------------------------------------------------
st.markdown("## 3. Modelled 30-Day Airfare Forecast")
if df_forecast.empty:
    st.info("No forecast file is available yet. Run the forecasting pipeline to generate one.")
else:
    latest_forecast_date = df_forecast["date"].max().date()
    if latest_forecast_date < today_ist:
        st.warning(
            f"The committed forecast ends on {latest_forecast_date:%d %b %Y}; "
            "it has expired and needs to be regenerated."
        )
    recent_daily = df_daily.tail(45)
    fig_fc = go.Figure()
    fig_fc.add_trace(
        go.Scatter(
            x=recent_daily["date"],
            y=recent_daily["apix_daily"],
            mode="lines",
            name="Historical modelled APIx",
            line=dict(color="#00d2ff", width=2),
        )
    )
    fig_fc.add_trace(
        go.Scatter(
            x=df_forecast["date"],
            y=df_forecast["predicted_apix"],
            mode="lines+markers",
            name="Model forecast",
            line=dict(color="#ffaa00", width=2.5, dash="dot"),
        )
    )
    if {"lower_ci", "upper_ci"}.issubset(df_forecast.columns):
        fig_fc.add_trace(
            go.Scatter(
                x=pd.concat([df_forecast["date"], df_forecast["date"][::-1]]),
                y=pd.concat([df_forecast["upper_ci"], df_forecast["lower_ci"][::-1]]),
                fill="toself",
                fillcolor="rgba(255, 170, 0, 0.12)",
                line=dict(color="rgba(255,255,255,0)"),
                name="95% confidence interval",
                hoverinfo="skip",
            )
        )
    fig_fc.update_layout(
        template="plotly_dark",
        hovermode="x unified",
        xaxis_title="Date",
        yaxis_title="Modelled index value",
        margin=dict(l=20, r=20, t=30, b=20),
        legend=dict(orientation="h", y=1.12, x=0),
    )
    st.plotly_chart(fig_fc, width="stretch")
st.markdown("---")

# -----------------------------------------------------------------------------
# 4. Advance booking curves from actual scraper output
# -----------------------------------------------------------------------------
st.markdown("## 4. Advance Booking Price Curves — Verified Fare Observations")
st.caption(
    "Averages use only successful fare observations. Different providers are shown separately; "
    "routes/windows without verified observations are not filled with synthetic prices."
)

quotes_view = df_quotes.copy()
if selected_sector != "All Corridors (National Composite)":
    quotes_view = quotes_view[quotes_view["sector"] == selected_sector]

if quotes_view.empty:
    st.info("There are no verified fare observations for this route in the current dataset.")
else:
    lead_summary = (
        quotes_view.groupby(["source_platform", "sector", "lead_time_days"], as_index=False)
        .agg(average_fare_inr=("parsed_fare_inr", "mean"), observations=("parsed_fare_inr", "count"))
        .sort_values("lead_time_days")
    )
    fig_curves = px.line(
        lead_summary,
        x="lead_time_days",
        y="average_fare_inr",
        color="sector",
        line_dash="source_platform",
        markers=True,
        hover_data={"observations": True, "lead_time_days": True, "average_fare_inr": ":,.0f"},
        labels={
            "lead_time_days": "Days before departure",
            "average_fare_inr": "Average observed fare (INR)",
            "sector": "Flight route",
            "source_platform": "Data provider",
        },
    )
    fig_curves.update_layout(
        template="plotly_dark",
        hovermode="x unified",
        xaxis=dict(tickmode="array", tickvals=[1, 7, 15, 30, 45]),
        yaxis_title="Average observed fare (INR)",
        margin=dict(l=20, r=20, t=30, b=20),
        legend=dict(orientation="h", y=1.12, x=0),
    )
    st.plotly_chart(fig_curves, width="stretch")
    st.dataframe(
        lead_summary.rename(
            columns={
                "source_platform": "Data provider",
                "sector": "Route",
                "lead_time_days": "Lead time (days)",
                "average_fare_inr": "Average fare (INR)",
                "observations": "Successful observations",
            }
        ),
        width="stretch",
        hide_index=True,
    )
st.markdown("---")

# -----------------------------------------------------------------------------
# 5. Route passenger weights
# -----------------------------------------------------------------------------
st.markdown("## 5. Route Passenger-Weight Distribution")
st.caption("Route weights are based on the passenger-volume figures included in the project dataset.")
col_w1, col_w2 = st.columns([1.1, 0.9])
with col_w1:
    fig_donut = px.pie(
        df_weights,
        names="sector",
        values="weight",
        hole=0.5,
        color_discrete_sequence=px.colors.sequential.RdBu,
    )
    fig_donut.update_layout(template="plotly_dark", margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig_donut, width="stretch")
with col_w2:
    st.markdown("#### Corridor weight rankings")
    st.dataframe(
        df_weights[["sector", "total_passengers", "weight"]].sort_values(
            by="weight", ascending=False
        ),
        width="stretch",
        height=350,
        hide_index=True,
    )

st.markdown("---")
st.caption(
    "Research prototype only. API fare observations can change and may differ from final checkout prices. "
    "A provider quote is not a ticket reservation. Do not use this dashboard as an official inflation "
    "statistic or as financial/policy advice."
)
