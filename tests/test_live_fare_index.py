import unittest

import pandas as pd

from src.live_fare_index import calculate_live_fare_history


class LiveFareIndexTests(unittest.TestCase):
    def setUp(self):
        self.weights = pd.DataFrame(
            [
                {"sector": "DELHI-MUMBAI", "weight": 0.60},
                {"sector": "DELHI-BANGALORE", "weight": 0.40},
            ]
        )

    def make_rows(self, batch, timestamp, del_bom_fares, del_blr_fares):
        rows = []
        for sector, fares in (
            ("DELHI-MUMBAI", del_bom_fares),
            ("DELHI-BANGALORE", del_blr_fares),
        ):
            for lead_days, fare in zip((1, 7, 15, 30, 45), fares):
                rows.append(
                    {
                        "source_platform": "ignav",
                        "status": "ok",
                        "sector": sector,
                        "scrape_timestamp": timestamp,
                        "parsed_fare_inr": fare,
                        "lead_time_days": lead_days,
                        "notes": f"provider=ignav; collection_batch={batch}",
                    }
                )
        return rows

    def test_creates_one_point_per_collection_batch(self):
        rows = []
        rows += self.make_rows("20261115T000700", "2026-11-15 00:07:10", [5100] * 5, [5600] * 5)
        rows += self.make_rows("20261116T000700", "2026-11-16 00:07:10", [10200] * 5, [11200] * 5)
        history = calculate_live_fare_history(pd.DataFrame(rows), self.weights)

        self.assertEqual(len(history), 2)
        self.assertEqual(history.iloc[0]["snapshot_date"], pd.Timestamp("2026-11-15"))
        self.assertEqual(history.iloc[1]["snapshot_date"], pd.Timestamp("2026-11-16"))
        self.assertAlmostEqual(history.iloc[1]["snapshot_index"], 200.0, places=6)
        self.assertEqual(int(history.iloc[1]["observation_count"]), 10)

    def test_legacy_rows_without_batch_id_use_latest_collection_window(self):
        rows = self.make_rows("ignored", "2026-11-16 10:39:30", [10200] * 5, [11200] * 5)
        for row in rows:
            row["notes"] = "provider=ignav; price_status=verified"
        history = calculate_live_fare_history(pd.DataFrame(rows), self.weights)
        self.assertEqual(len(history), 1)
        self.assertEqual(history.iloc[0]["snapshot_date"], pd.Timestamp("2026-11-16"))
        self.assertAlmostEqual(history.iloc[0]["snapshot_index"], 200.0, places=6)

    def test_route_filter_returns_only_selected_corridor(self):
        rows = self.make_rows("20261116T000700", "2026-11-16 00:07:10", [10200] * 5, [11200] * 5)
        history = calculate_live_fare_history(
            pd.DataFrame(rows), self.weights, selected_sector="DELHI-MUMBAI"
        )
        self.assertEqual(len(history), 1)
        self.assertAlmostEqual(history.iloc[0]["snapshot_index"], 200.0, places=6)
        self.assertEqual(int(history.iloc[0]["route_count"]), 1)


    def test_normalizes_api_route_codes_before_index_calculation(self):
        rows = self.make_rows(
            "20261116T000700",
            "2026-11-16 00:07:10",
            [5100] * 5,
            [5600] * 5,
        )
        route_codes = {
            "DELHI-MUMBAI": "DEL-BOM",
            "DELHI-BANGALORE": "DEL-BLR",
        }
        for row in rows:
            row["sector"] = route_codes[row["sector"]]

        history = calculate_live_fare_history(pd.DataFrame(rows), self.weights)

        self.assertEqual(len(history), 1)
        self.assertEqual(int(history.iloc[0]["route_count"]), 2)
        self.assertEqual(int(history.iloc[0]["observation_count"]), 10)
        self.assertAlmostEqual(history.iloc[0]["snapshot_index"], 100.0, places=6)

    def test_ignores_unverified_or_non_ignav_rows(self):
        rows = self.make_rows("20261116T000700", "2026-11-16 00:07:10", [10200] * 5, [11200] * 5)
        rows.append(
            {
                "source_platform": "ixigo",
                "status": "ok",
                "sector": "DELHI-MUMBAI",
                "scrape_timestamp": "2026-11-16 00:07:10",
                "parsed_fare_inr": 1,
                "lead_time_days": 1,
                "notes": "",
            }
        )
        rows.append(
            {
                "source_platform": "ignav",
                "status": "unverified",
                "sector": "DELHI-MUMBAI",
                "scrape_timestamp": "2026-11-16 00:07:10",
                "parsed_fare_inr": 1,
                "lead_time_days": 1,
                "notes": "",
            }
        )
        history = calculate_live_fare_history(pd.DataFrame(rows), self.weights)
        self.assertEqual(int(history.iloc[0]["observation_count"]), 10)
        self.assertAlmostEqual(history.iloc[0]["snapshot_index"], 200.0, places=6)


if __name__ == "__main__":
    unittest.main()
