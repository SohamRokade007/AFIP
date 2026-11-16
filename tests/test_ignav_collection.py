import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import run_ignav_collection as collector


class IgnavCollectorTests(unittest.TestCase):
    def test_only_verified_inr_fares_are_published(self):
        response = {
            "observed_at": "2026-11-16T04:00:00Z",
            "itineraries": [
                {
                    "price": {"amount": 6450, "currency": "INR", "status": "verified"},
                    "outbound": {"carrier": "IndiGo", "segments": []},
                    "ignav_id": "verified-id",
                },
                {
                    "price": {"amount": 6200, "currency": "INR", "status": "unverified"},
                    "outbound": {"carrier": "Air India", "segments": []},
                    "ignav_id": "hint-id",
                },
                {
                    "price": {"amount": 90, "currency": "USD", "status": "verified"},
                    "outbound": {"carrier": "Example Air", "segments": []},
                    "ignav_id": "usd-id",
                },
            ],
        }

        rows, details = collector.build_quote_rows(
            response=response,
            origin="DEL",
            destination="BOM",
            departure_date="2026-11-17",
            lead_days=1,
            market="IN",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_platform"], "ignav")
        self.assertEqual(rows[0]["carrier"], "IndiGo")
        self.assertEqual(rows[0]["parsed_fare_inr"], 6450)
        self.assertEqual(rows[0]["status"], "ok")
        self.assertIn("provider_observed_at=2026-11-16T04:00:00Z", rows[0]["notes"])
        self.assertEqual(details["skipped_unverified"], 1)
        self.assertEqual(details["skipped_currency"], 1)

    def test_empty_itineraries_are_not_fares(self):
        rows, details = collector.build_quote_rows(
            response={"observed_at": None, "itineraries": []},
            origin="DEL",
            destination="BOM",
            departure_date="2026-11-17",
            lead_days=1,
            market="IN",
        )
        self.assertEqual(rows, [])
        self.assertEqual(details["outcome"], "no_fares_returned")

    def test_india_market_maps_to_inr(self):
        self.assertEqual(collector.market_currency("IN"), "INR")

    def test_unknown_market_is_rejected(self):
        with self.assertRaises(collector.ProviderError):
            collector.market_currency("XX")

    def test_csv_header_mismatch_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "quotes.csv"
            target.write_text("old,header\nkeep,this\n", encoding="utf-8")
            with patch.object(collector, "OUTPUT_CSV", target):
                with self.assertRaises(collector.ProviderError):
                    collector.ensure_csv_header()
            self.assertEqual(target.read_text(encoding="utf-8"), "old,header\nkeep,this\n")


if __name__ == "__main__":
    unittest.main()
