#!/usr/bin/env python3
"""Collect verified India-market airfare quotes from the Ignav Flight Prices API.

The API key must be supplied in IGNAV_API_KEY. Never commit credentials.
Only fares marked price.status="verified" and denominated in INR are written
as status="ok" to the dashboard's shared observations CSV.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_URL = "https://ignav.com/api/fares/one-way"
OUTPUT_CSV = Path("data/processed/live_scraped_quotes_real.csv")
DEBUG_DIR = Path("api_debug")
SUMMARY_PATH = DEBUG_DIR / "collection_summary.json"

ROUTES = [
    ("DEL", "BOM", "DELHI-MUMBAI"),
    ("DEL", "BLR", "DELHI-BANGALORE"),
    ("BOM", "BLR", "MUMBAI-BANGALORE"),
    ("DEL", "HYD", "DELHI-HYDERABAD"),
    ("DEL", "CCU", "DELHI-KOLKATA"),
]
LEAD_WINDOWS_DAYS = [1, 7, 15, 30, 45]
CSV_COLUMNS = [
    "scrape_timestamp", "source_platform", "origin", "destination",
    "sector", "departure_date", "carrier", "raw_price_text",
    "parsed_fare_inr", "status", "notes", "lead_time_days",
]


class ProviderError(RuntimeError):
    """An API error that should stop collection without publishing bad data."""


class UnsupportedSearchError(ProviderError):
    """A date/route combination the provider cannot search; continue other windows."""


def write_summary(summary: dict[str, Any]) -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def market_currency(market: str) -> str:
    currencies = {"IN": "INR", "US": "USD", "GB": "GBP"}
    if market not in currencies:
        raise ProviderError(f"Unsupported configured market: {market}")
    return currencies[market]


def api_post(api_key: str, payload: dict[str, Any], timeout: int = 45) -> dict[str, Any]:
    request = Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "X-Api-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "AFIP-Research-Collector/1.0",
        },
        method="POST",
    )
    for attempt in range(3):
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(body)
            if not isinstance(parsed, dict):
                raise ProviderError("Ignav returned an unexpected response shape.")
            return parsed
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                error_data = json.loads(body)
            except json.JSONDecodeError:
                error_data = {"message": body[:500]}
            error_obj = error_data.get("error", {})
            if not isinstance(error_obj, dict):
                error_obj = {}
            code = str(error_obj.get("code") or error_data.get("code") or "")
            message = str(error_obj.get("message") or error_data.get("message") or body[:300])

            if exc.code == 424 and code == "unable_to_complete_request" and attempt < 2:
                delay = 2 ** (attempt + 1)
                print(f"[Ignav] Temporary upstream 424; retrying after {delay}s.")
                time.sleep(delay)
                continue
            if exc.code == 503 and attempt < 2:
                try:
                    delay = max(1, min(int(exc.headers.get("Retry-After", "3")), 30))
                except (TypeError, ValueError):
                    delay = 3
                print(f"[Ignav] Temporary 503; retrying after {delay}s.")
                time.sleep(delay)
                continue
            if code == "unsupported_search":
                raise UnsupportedSearchError(
                    f"Ignav does not support this route/date combination: {message}"
                ) from exc
            if exc.code == 401:
                raise ProviderError("Ignav rejected the API key (HTTP 401). Check IGNAV_API_KEY.") from exc
            if exc.code == 403:
                raise ProviderError("Ignav returned HTTP 403. Verify the account email address.") from exc
            if exc.code == 402:
                raise ProviderError("Ignav requires billing setup (HTTP 402). Check the Ignav dashboard.") from exc
            if exc.code == 429:
                raise ProviderError("Ignav monthly spending limit reached (HTTP 429). No retry was attempted.") from exc
            raise ProviderError(f"Ignav API HTTP {exc.code} ({code or 'provider_error'}): {message}") from exc
        except (URLError, TimeoutError) as exc:
            if attempt < 2:
                delay = 2 ** (attempt + 1)
                print(f"[Ignav] Network error; retrying after {delay}s: {exc}")
                time.sleep(delay)
                continue
            raise ProviderError(f"Could not reach Ignav API after retries: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ProviderError("Ignav returned a non-JSON response.") from exc
    raise ProviderError("Ignav request failed after retries.")


def ensure_csv_header() -> bool:
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    if not OUTPUT_CSV.exists() or OUTPUT_CSV.stat().st_size == 0:
        return False
    with OUTPUT_CSV.open("r", newline="", encoding="utf-8-sig") as handle:
        existing = next(csv.reader(handle), [])
    if existing != CSV_COLUMNS:
        raise ProviderError(
            "Existing quote CSV has an unexpected header. It was left unchanged; "
            "migrate the CSV before appending Ignav observations."
        )
    return True


def append_rows(rows: list[dict[str, Any]], has_header: bool) -> bool:
    if not rows:
        return has_header
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        if not has_header:
            writer.writeheader()
        writer.writerows(rows)
    return True


def get_carrier(itinerary: dict[str, Any]) -> str:
    outbound = itinerary.get("outbound") or {}
    carrier = outbound.get("carrier")
    if carrier:
        return str(carrier)
    segments = outbound.get("segments") or []
    if segments:
        first = segments[0]
        return str(first.get("operating_carrier_name") or first.get("marketing_carrier_code") or "")
    return ""


def build_quote_rows(
    response: dict[str, Any],
    origin: str,
    destination: str,
    departure_date: str,
    lead_days: int,
    market: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    itineraries = response.get("itineraries")
    if not isinstance(itineraries, list):
        raise ProviderError("Ignav response did not contain an itineraries array.")

    observed_at = response.get("observed_at")
    collected_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows: list[dict[str, Any]] = []
    skipped_unverified = 0
    skipped_currency = 0

    for itinerary in itineraries:
        if not isinstance(itinerary, dict):
            continue
        price = itinerary.get("price") or {}
        if str(price.get("status", "")).lower() != "verified":
            skipped_unverified += 1
            continue
        currency = str(price.get("currency", "")).upper()
        if currency != market_currency(market):
            skipped_currency += 1
            continue
        try:
            amount = float(price.get("amount"))
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue

        formatted = f"₹{amount:,.2f}" if currency == "INR" else f"{currency} {amount:,.2f}"
        notes = ["provider=ignav", "price_status=verified"]
        if observed_at:
            notes.append(f"provider_observed_at={observed_at}")
        if itinerary.get("ignav_id"):
            notes.append(f"ignav_id={itinerary['ignav_id']}")

        rows.append({
            "scrape_timestamp": collected_at,
            "source_platform": "ignav",
            "origin": origin,
            "destination": destination,
            "sector": f"{origin}-{destination}",
            "departure_date": departure_date,
            "carrier": get_carrier(itinerary) or None,
            "raw_price_text": formatted,
            "parsed_fare_inr": amount if currency == "INR" else "",
            "status": "ok",
            "notes": "; ".join(notes),
            "lead_time_days": lead_days,
        })

    if rows:
        outcome = "verified_fares"
    elif not itineraries:
        outcome = "no_fares_returned"
    elif skipped_currency:
        outcome = "currency_mismatch"
    elif skipped_unverified:
        outcome = "no_verified_fares"
    else:
        outcome = "no_usable_fares"

    return rows, {
        "outcome": outcome,
        "itineraries_returned": len(itineraries),
        "verified_inr_fares": len(rows),
        "skipped_unverified": skipped_unverified,
        "skipped_currency": skipped_currency,
        "provider_observed_at": observed_at,
    }


def run_collection(market: str = "IN") -> dict[str, Any]:
    api_key = os.environ.get("IGNAV_API_KEY", "").strip()
    if not api_key:
        raise ProviderError(
            "IGNAV_API_KEY is missing. Add it as a GitHub Actions repository secret before running this workflow."
        )

    has_header = ensure_csv_header()
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    summary: dict[str, Any] = {
        "provider": "ignav",
        "market": market,
        "currency": market_currency(market),
        "started_at": started_at,
        "requests_attempted": 0,
        "verified_fares_written": 0,
        "searches": [],
        "errors": [],
    }

    try:
        for origin, destination, _sector in ROUTES:
            for lead_days in LEAD_WINDOWS_DAYS:
                departure_date = (datetime.now().date() + timedelta(days=lead_days)).isoformat()
                payload = {
                    "origin": origin,
                    "destination": destination,
                    "departure_date": departure_date,
                    "adults": 1,
                    "cabin_class": "economy",
                    "market": market,
                }
                print(f"[Ignav] {origin}-{destination}, {departure_date} (T+{lead_days})")
                summary["requests_attempted"] += 1
                try:
                    response = api_post(api_key, payload)
                except UnsupportedSearchError as exc:
                    details = {
                        "outcome": "unsupported_search",
                        "itineraries_returned": 0,
                        "verified_inr_fares": 0,
                        "skipped_unverified": 0,
                        "skipped_currency": 0,
                        "provider_observed_at": None,
                        "error": str(exc),
                        "origin": origin,
                        "destination": destination,
                        "departure_date": departure_date,
                        "lead_time_days": lead_days,
                    }
                    summary["searches"].append(details)
                    diagnostic_row = {
                        "scrape_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "source_platform": "ignav",
                        "origin": origin,
                        "destination": destination,
                        "sector": f"{origin}-{destination}",
                        "departure_date": departure_date,
                        "carrier": "",
                        "raw_price_text": "",
                        "parsed_fare_inr": "",
                        "status": "unsupported_search",
                        "notes": f"provider=ignav; {exc}",
                        "lead_time_days": lead_days,
                    }
                    has_header = append_rows([diagnostic_row], has_header)
                    time.sleep(0.25)
                    continue

                rows, details = build_quote_rows(
                    response, origin, destination, departure_date, lead_days, market
                )
                details.update({
                    "origin": origin,
                    "destination": destination,
                    "departure_date": departure_date,
                    "lead_time_days": lead_days,
                })
                summary["searches"].append(details)

                if rows:
                    has_header = append_rows(rows, has_header)
                    summary["verified_fares_written"] += len(rows)
                else:
                    diagnostic_row = {
                        "scrape_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "source_platform": "ignav",
                        "origin": origin,
                        "destination": destination,
                        "sector": f"{origin}-{destination}",
                        "departure_date": departure_date,
                        "carrier": "",
                        "raw_price_text": "",
                        "parsed_fare_inr": "",
                        "status": details["outcome"],
                        "notes": (
                            f"provider=ignav; returned={details['itineraries_returned']}; "
                            f"unverified={details['skipped_unverified']}; "
                            f"currency_mismatch={details['skipped_currency']}"
                        ),
                        "lead_time_days": lead_days,
                    }
                    has_header = append_rows([diagnostic_row], has_header)
                time.sleep(0.25)

        summary["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        write_summary(summary)
        print(
            f"[Ignav] Finished: {summary['verified_fares_written']} verified INR fares "
            f"from {summary['requests_attempted']} requests."
        )
        return summary
    except Exception as exc:
        summary["errors"].append(str(exc))
        summary["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        write_summary(summary)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default=os.environ.get("IGNAV_MARKET", "IN"))
    args = parser.parse_args()
    try:
        summary = run_collection(args.market.upper())
    except ProviderError as exc:
        print(f"[Ignav] ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[Ignav] Unexpected collection error: {exc}", file=sys.stderr)
        return 1

    if summary["verified_fares_written"] == 0:
        print(
            "[Ignav] No verified INR fares were returned. "
            "The workflow validation step will leave published observations unchanged.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
