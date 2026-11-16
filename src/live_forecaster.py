"""Forecast the live fare-basket index after enough real daily observations exist."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "processed"
HISTORY_FILE = DATA_DIR / "live_fare_basket_history.csv"
FORECAST_FILE = DATA_DIR / "live_fare_forecast_30d.csv"
STATUS_FILE = DATA_DIR / "live_fare_forecast_status.json"

MIN_DAILY_OBSERVATIONS = 60
FORECAST_HORIZON_DAYS = 30


def _write_status(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _not_ready(count: int, last_date: str | None, reason: str) -> dict[str, Any]:
    if FORECAST_FILE.exists():
        FORECAST_FILE.unlink()
    status = {
        "status": "insufficient_history",
        "model": "SARIMAX(1,1,1)x(1,0,0,7)",
        "available_daily_observations": int(count),
        "required_daily_observations": MIN_DAILY_OBSERVATIONS,
        "latest_observation_date": last_date,
        "reason": reason,
    }
    _write_status(status)
    print(
        f"[Live forecast] Waiting for daily observations: "
        f"{count}/{MIN_DAILY_OBSERVATIONS}. {reason}"
    )
    return status


def generate_live_forecast() -> dict[str, Any]:
    if not HISTORY_FILE.exists():
        return _not_ready(0, None, "Live fare-basket history has not been built yet.")

    history = pd.read_csv(HISTORY_FILE)
    if history.empty or not {"snapshot_date", "snapshot_index"}.issubset(history.columns):
        return _not_ready(0, None, "No valid live index snapshots are available.")

    history["snapshot_date"] = pd.to_datetime(history["snapshot_date"], errors="coerce")
    history["snapshot_index"] = pd.to_numeric(history["snapshot_index"], errors="coerce")
    history = history.dropna(subset=["snapshot_date", "snapshot_index"])
    history = history[history["snapshot_index"] > 0]

    if history.empty:
        return _not_ready(0, None, "No positive live index observations are available.")

    daily = (
        history.groupby(history["snapshot_date"].dt.normalize())["snapshot_index"]
        .mean()
        .sort_index()
    )
    last_date = daily.index.max()
    first_date = daily.index.min()
    observed_count = int(daily.notna().sum())
    calendar_span = int((last_date - first_date).days) + 1
    latest_date_text = last_date.strftime("%Y-%m-%d")

    if observed_count < MIN_DAILY_OBSERVATIONS:
        return _not_ready(
            observed_count,
            latest_date_text,
            "A weekly seasonal model is held back until at least 60 actual daily snapshots exist.",
        )
    if calendar_span < MIN_DAILY_OBSERVATIONS:
        return _not_ready(
            observed_count,
            latest_date_text,
            "There are not yet 60 calendar days between the first and latest snapshot.",
        )

    # Reindex to calendar days. Missing collection dates stay NaN; no interpolation
    # or synthetic daily values are inserted. SARIMAX can accommodate missing endog.
    daily = daily.reindex(pd.date_range(first_date, last_date, freq="D"))
    try:
        from statsmodels.tsa.statespace.sarimax import SARIMAX

        model = SARIMAX(
            daily.astype(float),
            order=(1, 1, 1),
            seasonal_order=(1, 0, 0, 7),
            enforce_stationarity=False,
            enforce_invertibility=False,
        )
        fitted = model.fit(disp=False, maxiter=200)
        forecast_result = fitted.get_forecast(steps=FORECAST_HORIZON_DAYS)
        forecast_values = np.asarray(forecast_result.predicted_mean, dtype=float)
        confidence = np.asarray(forecast_result.conf_int(alpha=0.05), dtype=float)

        if not np.isfinite(forecast_values).all() or not np.isfinite(confidence).all():
            raise ValueError("SARIMAX returned non-finite forecasts.")

        forecast_dates = pd.date_range(
            last_date + pd.Timedelta(days=1),
            periods=FORECAST_HORIZON_DAYS,
            freq="D",
        )
        forecast = pd.DataFrame(
            {
                "date": forecast_dates,
                "predicted_live_fare_basket": np.maximum(forecast_values, 0),
                "lower_ci": np.maximum(confidence[:, 0], 0),
                "upper_ci": np.maximum(confidence[:, 1], 0),
                "model": "SARIMAX(1,1,1)x(1,0,0,7)",
                "training_observations": observed_count,
                "last_observed_date": latest_date_text,
            }
        )
        forecast.to_csv(FORECAST_FILE, index=False)
        status = {
            "status": "ready",
            "model": "SARIMAX(1,1,1)x(1,0,0,7)",
            "available_daily_observations": observed_count,
            "required_daily_observations": MIN_DAILY_OBSERVATIONS,
            "latest_observation_date": latest_date_text,
            "forecast_horizon_days": FORECAST_HORIZON_DAYS,
            "converged": bool(getattr(fitted, "mle_retvals", {}).get("converged", True)),
        }
        _write_status(status)
        print(
            f"[Live forecast] Generated {FORECAST_HORIZON_DAYS} days from "
            f"{observed_count} actual daily snapshots (latest {latest_date_text})."
        )
        return status
    except Exception as exc:
        # Never leave a stale forecast visible after a failed model fit.
        if FORECAST_FILE.exists():
            FORECAST_FILE.unlink()
        status = {
            "status": "model_error",
            "model": "SARIMAX(1,1,1)x(1,0,0,7)",
            "available_daily_observations": observed_count,
            "required_daily_observations": MIN_DAILY_OBSERVATIONS,
            "latest_observation_date": latest_date_text,
            "reason": f"Model could not be fitted: {type(exc).__name__}: {exc}",
        }
        _write_status(status)
        print(f"[Live forecast] {status['reason']}")
        return status


if __name__ == "__main__":
    generate_live_forecast()
