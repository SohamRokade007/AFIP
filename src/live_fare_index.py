"""Build transparent live fare-basket snapshots from verified provider observations.

This is intentionally a separate reference-basket measure, not a rewrite of
historical APIx. It uses the project's existing reference fares and DGCA route
weights, and creates one point per actual collection batch (no interpolation).
"""
from __future__ import annotations

import math

import pandas as pd


REFERENCE_BASE_FARES_INR = {
    "DELHI-BANGALORE": 5600.0,
    "DELHI-MUMBAI": 5100.0,
    "DELHI-HYDERABAD": 4800.0,
    "DELHI-KOLKATA": 4600.0,
    "MUMBAI-BANGALORE": 3900.0,
}

LEAD_WINDOWS_DAYS = (1, 7, 15, 30, 45)


def _prepare_latest_batches(quotes: pd.DataFrame) -> pd.DataFrame:
    required = {
        "source_platform", "status", "sector", "scrape_timestamp",
        "parsed_fare_inr", "lead_time_days",
    }
    if quotes.empty or not required.issubset(quotes.columns):
        return pd.DataFrame()

    data = quotes.copy()
    data = data[
        data["source_platform"].astype(str).str.lower().eq("ignav")
        & data["status"].astype(str).str.lower().eq("ok")
    ].copy()
    if data.empty:
        return pd.DataFrame()

    data["scrape_timestamp"] = pd.to_datetime(data["scrape_timestamp"], errors="coerce")
    data["parsed_fare_inr"] = pd.to_numeric(data["parsed_fare_inr"], errors="coerce")
    data["lead_time_days"] = pd.to_numeric(data["lead_time_days"], errors="coerce")
    data = data.dropna(
        subset=["scrape_timestamp", "parsed_fare_inr", "lead_time_days", "sector"]
    )
    data = data[
        (data["parsed_fare_inr"] > 0)
        & data["lead_time_days"].isin(LEAD_WINDOWS_DAYS)
        & data["sector"].isin(REFERENCE_BASE_FARES_INR)
    ].copy()
    if data.empty:
        return pd.DataFrame()

    # New collector runs stamp all rows with one collection_batch in notes.
    # For the first legacy run (which predates that field), retain the latest
    # ten-minute collection window as a single batch instead of mixing old runs.
    notes = data.get("notes", pd.Series("", index=data.index)).fillna("").astype(str)
    if "collection_batch" in data.columns:
        explicit_batch = data["collection_batch"].fillna("").astype(str)
    else:
        explicit_batch = pd.Series("", index=data.index, dtype="object")

    note_batch = notes.str.extract(r"(?:^|;\\s*)collection_batch=([^;]+)", expand=False)
    data["_batch"] = explicit_batch.where(explicit_batch.ne(""), note_batch)
    missing_batch = data["_batch"].isna() | data["_batch"].astype(str).eq("")
    if missing_batch.any():
        legacy = data.loc[missing_batch]
        latest_legacy_time = legacy["scrape_timestamp"].max()
        legacy_cutoff = latest_legacy_time - pd.Timedelta(minutes=10)
        legacy_mask = missing_batch & (
            data["scrape_timestamp"] >= legacy_cutoff
        )
        if legacy_mask.any():
            legacy_id = "legacy-" + latest_legacy_time.strftime("%Y%m%dT%H%M%S")
            data.loc[legacy_mask, "_batch"] = legacy_id

    data = data[data["_batch"].notna() & data["_batch"].astype(str).ne("")].copy()
    return data


def _snapshot_for_batch(
    batch: pd.DataFrame,
    weights: pd.DataFrame,
    selected_sector: str | None,
) -> dict | None:
    weight_data = weights[["sector", "weight"]].copy()
    weight_data["weight"] = pd.to_numeric(weight_data["weight"], errors="coerce")
    weight_data = weight_data.dropna(subset=["sector", "weight"])

    # First average offers within each route/lead-time window. Then use the
    # Jevons geometric mean across booking windows, matching the engine's
    # geometric treatment of its five lead-time observations.
    by_window = (
        batch.groupby(["sector", "lead_time_days"], as_index=False)
        .agg(
            window_average_fare=("parsed_fare_inr", "mean"),
            observations=("parsed_fare_inr", "count"),
        )
    )
    components = []
    for sector, route_windows in by_window.groupby("sector"):
        window_fares = route_windows["window_average_fare"].astype(float)
        if window_fares.empty or (window_fares <= 0).any():
            continue
        geometric_fare = math.exp(
            sum(math.log(value) for value in window_fares) / len(window_fares)
        )
        base_fare = REFERENCE_BASE_FARES_INR[sector]
        route_weight = weight_data.loc[weight_data["sector"].eq(sector), "weight"]
        if route_weight.empty or float(route_weight.iloc[0]) <= 0:
            continue
        components.append(
            {
                "sector": sector,
                "average_fare_inr": geometric_fare,
                "reference_fare_inr": base_fare,
                "snapshot_index": geometric_fare / base_fare * 100.0,
                "passenger_weight": float(route_weight.iloc[0]),
                "observations": int(route_windows["observations"].sum()),
                "booking_windows": int(route_windows["lead_time_days"].nunique()),
            }
        )

    if not components:
        return None

    component_frame = pd.DataFrame(components)
    if selected_sector:
        selected = component_frame[component_frame["sector"].eq(selected_sector)]
        if selected.empty:
            return None
        index_value = float(selected["snapshot_index"].iloc[0])
        observation_count = int(selected["observations"].sum())
        route_count = 1
        windows = int(selected["booking_windows"].iloc[0])
    else:
        weight_total = float(component_frame["passenger_weight"].sum())
        if weight_total <= 0:
            return None
        index_value = float(
            (component_frame["snapshot_index"] * component_frame["passenger_weight"]).sum()
            / weight_total
        )
        observation_count = int(component_frame["observations"].sum())
        route_count = int(len(component_frame))
        windows = int(component_frame["booking_windows"].max())

    return {
        "snapshot_index": index_value,
        "observation_count": observation_count,
        "route_count": route_count,
        "lead_windows": windows,
        "components": component_frame,
    }


def calculate_live_fare_history(
    quotes: pd.DataFrame,
    weights: pd.DataFrame,
    selected_sector: str | None = None,
) -> pd.DataFrame:
    """Return one live reference-basket point per actual Ignav collection batch.

    If selected_sector is supplied, return that route's reference-fare ratio;
    otherwise return the DGCA passenger-weighted composite of covered routes.
    The historical APIx series is deliberately not modified or interpolated.
    """
    if not {"sector", "weight"}.issubset(weights.columns):
        return pd.DataFrame()

    data = _prepare_latest_batches(quotes)
    if data.empty:
        return pd.DataFrame()

    snapshots = []
    for batch_id, batch in data.groupby("_batch", sort=True):
        result = _snapshot_for_batch(batch, weights, selected_sector)
        if result is None:
            continue
        snapshots.append(
            {
                "batch_id": str(batch_id),
                "snapshot_at": batch["scrape_timestamp"].max(),
                "snapshot_date": batch["scrape_timestamp"].max().normalize(),
                "snapshot_index": result["snapshot_index"],
                "observation_count": result["observation_count"],
                "route_count": result["route_count"],
                "lead_windows": result["lead_windows"],
            }
        )

    if not snapshots:
        return pd.DataFrame()

    return pd.DataFrame(snapshots).sort_values("snapshot_at").reset_index(drop=True)
