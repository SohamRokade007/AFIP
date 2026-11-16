"""Persist live fare-basket history from verified Ignav observations."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

try:
    from src.live_fare_index import calculate_live_fare_history
except ModuleNotFoundError:
    from live_fare_index import calculate_live_fare_history

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "processed"
QUOTES_FILE = DATA_DIR / "live_scraped_quotes_real.csv"
WEIGHTS_FILE = DATA_DIR / "sector_weights.csv"
OUTPUT_FILE = DATA_DIR / "live_fare_basket_history.csv"


def build_history() -> pd.DataFrame:
    if not QUOTES_FILE.exists():
        raise FileNotFoundError(f"Fare observation file not found: {QUOTES_FILE}")
    if not WEIGHTS_FILE.exists():
        raise FileNotFoundError(f"Route-weight file not found: {WEIGHTS_FILE}")

    quotes = pd.read_csv(QUOTES_FILE)
    weights = pd.read_csv(WEIGHTS_FILE)
    history = calculate_live_fare_history(quotes, weights)

    if history.empty:
        raise RuntimeError(
            "No verified Ignav collection batches are available to build the live index."
        )

    history["snapshot_date"] = pd.to_datetime(history["snapshot_date"]).dt.strftime("%Y-%m-%d")
    history["snapshot_at"] = pd.to_datetime(history["snapshot_at"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    history = history[
        [
            "batch_id",
            "snapshot_at",
            "snapshot_date",
            "snapshot_index",
            "observation_count",
            "route_count",
            "lead_windows",
        ]
    ]
    history.to_csv(OUTPUT_FILE, index=False)
    latest = history.iloc[-1]
    print(
        f"[Live index] Saved {len(history)} actual collection snapshots. "
        f"Latest: {latest['snapshot_date']} = {float(latest['snapshot_index']):.2f} "
        f"({int(latest['observation_count'])} fares, {int(latest['route_count'])} routes)."
    )
    return history


if __name__ == "__main__":
    build_history()
