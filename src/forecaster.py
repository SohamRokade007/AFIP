"""Backward-compatible entry point for the live fare-basket forecaster.

The old model forecast was trained on a simulated APIx history. The production
forecast now uses only actual daily fare-basket snapshots.
"""
try:
    from src.live_forecaster import generate_live_forecast
except ModuleNotFoundError:
    from live_forecaster import generate_live_forecast


def generate_apix_forecast(forecast_horizon_days: int = 30):
    if forecast_horizon_days != 30:
        raise ValueError("The production live fare-basket forecast is configured for 30 days.")
    return generate_live_forecast()


if __name__ == "__main__":
    generate_apix_forecast(30)
