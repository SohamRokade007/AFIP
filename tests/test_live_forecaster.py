import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src import live_forecaster


class LiveForecasterTests(unittest.TestCase):
    def test_forecast_waits_for_real_daily_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_path = root / "history.csv"
            forecast_path = root / "forecast.csv"
            status_path = root / "status.json"

            pd.DataFrame(
                {
                    "snapshot_date": ["2026-11-15", "2026-11-16"],
                    "snapshot_index": [210.0, 215.0],
                }
            ).to_csv(history_path, index=False)

            with (
                patch.object(live_forecaster, "HISTORY_FILE", history_path),
                patch.object(live_forecaster, "FORECAST_FILE", forecast_path),
                patch.object(live_forecaster, "STATUS_FILE", status_path),
                patch.object(live_forecaster, "DATA_DIR", root),
            ):
                status = live_forecaster.generate_live_forecast()

            self.assertEqual(status["status"], "insufficient_history")
            self.assertEqual(status["available_daily_observations"], 2)
            self.assertFalse(forecast_path.exists())
            saved = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["required_daily_observations"], 60)

    def test_forecast_removes_old_output_when_history_is_insufficient(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_path = root / "history.csv"
            forecast_path = root / "forecast.csv"
            status_path = root / "status.json"
            pd.DataFrame(
                {"snapshot_date": ["2026-11-16"], "snapshot_index": [215.0]}
            ).to_csv(history_path, index=False)
            forecast_path.write_text("stale forecast", encoding="utf-8")

            with (
                patch.object(live_forecaster, "HISTORY_FILE", history_path),
                patch.object(live_forecaster, "FORECAST_FILE", forecast_path),
                patch.object(live_forecaster, "STATUS_FILE", status_path),
                patch.object(live_forecaster, "DATA_DIR", root),
            ):
                status = live_forecaster.generate_live_forecast()

            self.assertEqual(status["status"], "insufficient_history")
            self.assertFalse(forecast_path.exists())


if __name__ == "__main__":
    unittest.main()
