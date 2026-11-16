import unittest

import pandas as pd

from src.live_fare_index import get_latest_ignav_quotes


class LatestIgnavBatchTests(unittest.TestCase):
    def test_returns_only_latest_explicit_batch(self):
        rows = []
        for batch, timestamp in (
            ("20261115T000700", "2026-11-15 00:07:10"),
            ("20261116T000700", "2026-11-16 00:07:10"),
        ):
            for lead in (1, 7, 15, 30, 45):
                rows.append(
                    {
                        "source_platform": "ignav",
                        "status": "ok",
                        "sector": "DELHI-MUMBAI",
                        "scrape_timestamp": timestamp,
                        "parsed_fare_inr": 5000 + lead,
                        "lead_time_days": lead,
                        "notes": f"provider=ignav; collection_batch={batch}",
                    }
                )
        latest = get_latest_ignav_quotes(pd.DataFrame(rows))
        self.assertEqual(len(latest), 5)
        self.assertEqual(latest["scrape_timestamp"].dt.date.nunique(), 1)
        self.assertEqual(latest["scrape_timestamp"].dt.date.iloc[0].isoformat(), "2026-11-16")


if __name__ == "__main__":
    unittest.main()
