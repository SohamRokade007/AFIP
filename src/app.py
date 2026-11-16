from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

try:
    from src.live_fare_index import (
        calculate_live_fare_history,
        get_latest_ignav_quotes,
    )
except ModuleNotFoundError:
    from live_fare_index import (
        calculate_live_fare_history,
        get_latest_ignav_quotes,
    )


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

    forecast_path = DATA_DIR / "live_fare_forecast_30d.csv"
    forecast = pd.read_csv(forecast_path) if forecast_path.exists() else pd.DataFrame()
    if not forecast.empty and "date" in forecast.columns:
        forecast["date"] = pd.to_datetime(forecast["date"], errors="coerce")
        forecast = forecast.dropna(subset=["date"]).sort_values("date")

    status_path = DATA_DIR / "live_fare_forecast_status.json"
    forecast_status = {}
    if status_path.exists():
        import json
        forecast_status = json.loads(status_path.read_text(encoding="utf-8"))

    # Provider-specific observations share the schema but remain separated by source_platform.
    # The older Ixigo rows remain visible as a separate series; no synthetic quote file is used.
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

    return weights, daily, mospi, quotes, forecast, forecast_status


try:
    df_weights, df_daily, df_mospi, df_quotes, df_forecast, forecast_status = load_data()
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

# Keep API-provider rows distinct from legacy Ixigo observations.
df_ignav_quotes = df_quotes[
    df_quotes.get("source_platform", pd.Series(index=df_quotes.index, dtype=str))
    .astype(str).str.lower().eq("ignav")
].copy()

# Build actual collection-day snapshots from verified Ignav rows.
try:
    df_live_history = calculate_live_fare_history(
        df_ignav_quotes,
        df_weights,
        selected_sector=(
            None
            if selected_sector == "All Corridors (National Composite)"
            else selected_sector
        ),
    )
    df_latest_ignav_batch = get_latest_ignav_quotes(df_ignav_quotes)
except Exception as exc:
    df_live_history = pd.DataFrame()
    df_latest_ignav_batch = pd.DataFrame()
    st.warning(f"Could not calculate the live fare-basket snapshot: {exc}")

latest_live_snapshot = (
    df_live_history.iloc[-1] if not df_live_history.empty else None
)

# Current data coverage and freshness.
today_ist = pd.Timestamp.now(tz="Asia/Kolkata").date()
latest_index_date = df_daily["date"].max().date()
latest_quote_date = None
if not df_ignav_quotes.empty and "scrape_timestamp" in df_ignav_quotes.columns:
    latest_scrape = df_ignav_quotes["scrape_timestamp"].max()
    if pd.notna(latest_scrape):
        latest_quote_date = latest_scrape.date()

if latest_index_date < today_ist:
    if latest_live_snapshot is not None:
        st.warning(
            f"The historical modelled APIx line ends on {latest_index_date:%d %b %Y}. "
            f"A separate verified live fare-basket snapshot is available for "
            f"{latest_live_snapshot['snapshot_date']:%d %b %Y}. "
            "The gap contains no daily APIx observations and is intentionally not interpolated."
        )
    else:
        st.warning(
            f"The historical modelled APIx series currently ends on {latest_index_date:%d %b %Y}. "
            "It is historical reference data, not a live index calculated from current fares."
        )

if latest_quote_date is None:
    st.warning("No verified airfare observations are available in the current dataset.")
elif latest_quote_date < today_ist:
    st.warning(
        f"The latest verified fare observations are dated {latest_quote_date:%d %b %Y}. "
        "The fare observations are stale until the next successful provider collection."
    )
else:
    st.success(f"Latest verified fare observations: {latest_quote_date:%d %b %Y} (India time/date).")

st.info(
    "Methodology note: the historical APIx series and forecast remain modelled outputs. "
    "The live fare-basket snapshot is a separate reference measure calculated from verified "
    "Ignav fares, existing DGCA route weights and the project's configured reference fares. "
    "It is shown on a separate chart axis and is not directly comparable with historical APIx. "
    "No daily values are invented for dates without a collection."
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

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Latest modelled APIx", f"{latest_apix:.2f}", f"{pct_change:+.2f}% vs previous day")
k2.metric(
    "Live fare basket (reference = 100)",
    f"{latest_live_snapshot['snapshot_index']:.2f}" if latest_live_snapshot is not None else "N/A",
)
k3.metric("Latest MoSPI airfare CPI in file", f"{latest_mospi:.2f}" if pd.notna(latest_mospi) else "N/A")
k4.metric("MoSPI inflation in file", f"{latest_inflation:.2f}%" if pd.notna(latest_inflation) else "N/A")
k5.metric("Verified Ignav fare observations", f"{len(df_ignav_quotes):,}")

coverage_caption = (
    f"Historical modelled APIx coverage: {df_daily['date'].min():%d %b %Y} to "
    f"{df_daily['date'].max():%d %b %Y}."
)
if latest_live_snapshot is not None:
    coverage_caption += (
        f" Latest separate live fare-basket snapshot: "
        f"{latest_live_snapshot['snapshot_date']:%d %b %Y} "
        f"({int(latest_live_snapshot['route_count'])} covered routes, "
        f"{int(latest_live_snapshot['observation_count'])} observations)."
    )
st.caption(coverage_caption)
st.markdown("---")

# -----------------------------------------------------------------------------
# 2. Historical modelled index and live fare-basket snapshots
# -----------------------------------------------------------------------------
st.markdown("## 2. Historical Modelled APIx and Live Fare-Basket Snapshots")
st.caption(
    "Left axis: the existing modelled APIx history and monthly MoSPI benchmark. "
    "Right axis: actual verified fare-basket snapshots on collection dates only. "
    "The live basket uses configured reference fares = 100 and is not directly comparable "
    "with the modelled historical APIx scale."
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

if not df_live_history.empty:
    live_text = [""] * len(df_live_history)
    live_text[-1] = "Latest live snapshot"
    live_customdata = df_live_history[
        ["observation_count", "route_count", "lead_windows"]
    ].to_numpy()
    fig_trend.add_trace(
        go.Scatter(
            x=df_live_history["snapshot_date"],
            y=df_live_history["snapshot_index"],
            yaxis="y2",
            mode="lines+markers+text",
            name="Live fare-basket snapshot",
            text=live_text,
            textposition="top center",
            line=dict(color="#ffaa00", width=2, dash="dot"),
            marker=dict(color="#ffaa00", size=11, symbol="diamond"),
            customdata=live_customdata,
            connectgaps=False,
            hovertemplate=(
                "<b>Verified live fare basket</b><br>"
                "Collection date: %{x|%d %b %Y}<br>"
                "Reference-basket points: %{y:.2f}<br>"
                "Verified fares: %{customdata[0]}<br>"
                "Covered routes: %{customdata[1]}<br>"
                "Booking windows: %{customdata[2]}"
                "<extra></extra>"
            ),
        )
    )

    latest_live_date = pd.Timestamp(df_live_history["snapshot_date"].max())
    if latest_live_date > pd.Timestamp(latest_index_date) + pd.Timedelta(days=1):
        fig_trend.add_vrect(
            x0=pd.Timestamp(latest_index_date) + pd.Timedelta(days=1),
            x1=latest_live_date - pd.Timedelta(days=1),
            fillcolor="rgba(255, 170, 0, 0.045)",
            line_width=0,
            layer="below",
            annotation_text="No daily historical APIx observations",
            annotation_position="top left",
            annotation_font_color="#ffaa00",
        )
else:
    st.warning(
        "No verified live fare-basket snapshot is available for the selected route."
    )

right_axis_max = (
    max(150.0, float(df_live_history["snapshot_index"].max()) * 1.2)
    if not df_live_history.empty
    else 150.0
)
fig_trend.update_layout(
    template="plotly_dark",
    hovermode="x unified",
    xaxis_title="Date",
    yaxis=dict(title="Historical modelled APIx / MoSPI index"),
    yaxis2=dict(
        title="Live fare basket (configured reference fares = 100)",
        overlaying="y",
        side="right",
        showgrid=False,
        rangemode="tozero",
        range=[0, right_axis_max],
    ),
    margin=dict(l=20, r=35, t=30, b=20),
    legend=dict(orientation="h", y=1.12, x=0),
)
st.plotly_chart(fig_trend, width="stretch")
st.caption(
    "Live snapshots appear only on dates when the API collector ran. The first live point is "
    "not connected to the older modelled line; as daily collections accumulate, only actual "
    "live collection points will be connected to one another."
)
st.markdown("---")

# -----------------------------------------------------------------------------
# 3. Live fare outlook and 30-day forecast
# -----------------------------------------------------------------------------
st.markdown("## 3. Live Fare Outlook and 30-Day Forecast")
st.caption(
    "The fare outlook below uses actual verified Ignav quotes from the latest collection batch. "
    "It shows observed prices by departure lead time, not a prediction. The SARIMAX forecast "
    "uses the daily live fare-basket index only after sufficient real observations accumulate."
)

if not df_latest_ignav_batch.empty:
    current_view = df_latest_ignav_batch.copy()
    if selected_sector != "All Corridors (National Composite)":
        current_view = current_view[current_view["sector"] == selected_sector]

    if current_view.empty:
        st.info(
            "The selected corridor is not part of the five-route live Ignav collection basket. "
            "Choose one of the monitored routes to see current quotes."
        )
    else:
        outlook = (
            current_view.groupby(["sector", "lead_time_days"], as_index=False)
            .agg(
                median_fare_inr=("parsed_fare_inr", "median"),
                average_fare_inr=("parsed_fare_inr", "mean"),
                minimum_fare_inr=("parsed_fare_inr", "min"),
                maximum_fare_inr=("parsed_fare_inr", "max"),
                observations=("parsed_fare_inr", "count"),
                departure_date=("departure_date", "first"),
            )
            .sort_values(["sector", "lead_time_days"])
        )

        lowest_fare = float(current_view["parsed_fare_inr"].min())
        median_fare = float(current_view["parsed_fare_inr"].median())
        o1, o2, o3 = st.columns(3)
        o1.metric("Lowest fare in latest batch", f"₹{lowest_fare:,.0f}")
        o2.metric("Median quoted fare", f"₹{median_fare:,.0f}")
        o3.metric("Quotes in latest batch", f"{len(current_view):,}")

        fig_outlook = go.Figure()
        for sector, route_data in outlook.groupby("sector"):
            route_data = route_data.sort_values("lead_time_days")
            median = route_data["median_fare_inr"].to_numpy()
            fig_outlook.add_trace(
                go.Scatter(
                    x=route_data["lead_time_days"],
                    y=median,
                    mode="lines+markers",
                    name=sector.replace("-", " – "),
                    customdata=route_data[
                        ["departure_date", "minimum_fare_inr", "maximum_fare_inr", "observations"]
                    ].to_numpy(),
                    error_y=dict(
                        type="data",
                        symmetric=False,
                        array=(route_data["maximum_fare_inr"] - route_data["median_fare_inr"]).clip(lower=0),
                        arrayminus=(route_data["median_fare_inr"] - route_data["minimum_fare_inr"]).clip(lower=0),
                        visible=True,
                        thickness=1.2,
                        width=4,
                    ),
                    hovertemplate=(
                        "<b>%{fullData.name}</b><br>"
                        "Advance booking: %{x} days<br>"
                        "Departure date: %{customdata[0]}<br>"
                        "Median fare: ₹%{y:,.0f}<br>"
                        "Range: ₹%{customdata[1]:,.0f} – ₹%{customdata[2]:,.0f}<br>"
                        "Returned itineraries: %{customdata[3]}"
                        "<extra></extra>"
                    ),
                )
            )
        fig_outlook.update_layout(
            template="plotly_dark",
            hovermode="x unified",
            xaxis=dict(
                title="Days before departure",
                tickmode="array",
                tickvals=[1, 7, 15, 30, 45],
            ),
            yaxis_title="Observed fare (INR)",
            legend=dict(orientation="h", y=1.12, x=0),
            margin=dict(l=20, r=20, t=30, b=20),
        )
        st.plotly_chart(fig_outlook, width="stretch")
        st.dataframe(
            outlook.rename(
                columns={
                    "sector": "Route",
                    "lead_time_days": "Advance days",
                    "departure_date": "Departure date",
                    "median_fare_inr": "Median fare (INR)",
                    "average_fare_inr": "Average fare (INR)",
                    "minimum_fare_inr": "Lowest fare (INR)",
                    "maximum_fare_inr": "Highest fare (INR)",
                    "observations": "Itineraries",
                }
            ),
            width="stretch",
            hide_index=True,
        )
else:
    st.warning("No current Ignav collection batch is available for the fare outlook.")

if not df_forecast.empty:
    forecast_latest = df_forecast["date"].max().date()
    st.success(
        f"Live fare-basket forecast available through {forecast_latest:%d %b %Y}. "
        f"Training observations: {int(df_forecast['training_observations'].iloc[-1])} daily snapshots."
    )
    fig_fc = go.Figure()
    fig_fc.add_trace(
        go.Scatter(
            x=df_live_history["snapshot_date"],
            y=df_live_history["snapshot_index"],
            mode="lines+markers",
            name="Actual live fare basket",
            line=dict(color="#00d2ff", width=2),
        )
    )
    fig_fc.add_trace(
        go.Scatter(
            x=df_forecast["date"],
            y=df_forecast["predicted_live_fare_basket"],
            mode="lines+markers",
            name="30-day SARIMAX forecast",
            line=dict(color="#ffaa00", width=2.5, dash="dot"),
        )
    )
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
        yaxis_title="Live fare-basket index (reference = 100)",
        legend=dict(orientation="h", y=1.12, x=0),
        margin=dict(l=20, r=20, t=30, b=20),
    )
    st.plotly_chart(fig_fc, width="stretch")
else:
    available = int(
        forecast_status.get(
            "available_daily_observations",
            df_live_history["snapshot_date"].nunique() if not df_live_history.empty else 0,
        )
    )
    required = int(forecast_status.get("required_daily_observations", 60))
    progress = min(available / max(required, 1), 1.0)
    st.info(
        f"The live-data SARIMAX forecast is active but waiting for sufficient history: "
        f"{available}/{required} actual daily fare-basket observations. "
        "No synthetic days are added. The current fare outlook above is already based on "
        "verified API quotes."
    )
    st.progress(progress, text=f"Forecast training history: {available} of {required} daily snapshots")
    if forecast_status.get("reason"):
        st.caption(f"Forecast status: {forecast_status['reason']}")

st.markdown("---")

# -----------------------------------------------------------------------------
# 4. Historical advance-booking curves from all retained providers
# -----------------------------------------------------------------------------
st.markdown("## 4. Historical Advance-Booking Curves by Provider")
st.caption(
    "This longer-view chart includes retained successful observations from each provider. "
    "Provider lines are shown separately; it is distinct from the latest-batch fare outlook above."
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
