"""Run the complete local AFIP live-data pipeline.

Requires IGNAV_API_KEY in the environment. This pipeline intentionally does not
rerun the synthetic historical index generator.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_step(step_name: str, command: list[str]) -> None:
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting: {step_name}...")
    subprocess.run(command, cwd=ROOT, check=True)
    print(f"Completed: {step_name} successfully.")


def execute_daily_pipeline() -> None:
    if not os.environ.get("IGNAV_API_KEY", "").strip():
        raise RuntimeError("IGNAV_API_KEY is missing from the environment.")

    print("==================================================")
    print(" AFIP VERIFIED FARE → LIVE INDEX → FORECAST PIPELINE")
    print("==================================================")

    run_step(
        "Ignav verified airfare collection",
        [sys.executable, "src/run_ignav_collection.py", "--market", "IN"],
    )

    summary_path = ROOT / "api_debug" / "collection_summary.json"
    if not summary_path.exists():
        raise RuntimeError("Collector summary was not created.")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if int(summary.get("verified_fares_written", 0)) <= 0:
        raise RuntimeError("No new verified INR fare observations were collected.")

    run_step("Build daily live fare-basket history", [sys.executable, "src/build_live_index.py"])
    run_step("Generate live-data 30-day forecast", [sys.executable, "src/forecaster.py"])

    print("\nAFIP live-data pipeline completed successfully.")


if __name__ == "__main__":
    execute_daily_pipeline()
