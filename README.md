# AFIP — Airfare Price Index & Intelligence Prototype

AFIP is an independent academic/research prototype for examining domestic airfare patterns in India. It combines a Streamlit dashboard, a scheduled Ignav flight-prices API collector, route passenger weights, a modelled daily index, and a SARIMAX forecasting experiment.

> **Important:** This is not an official Government of India or Ministry of Statistics and Programme Implementation (MoSPI) service. In the current version, the historical APIx series and forecast are modelled outputs; they are not yet calculated from verified provider fare observations. The booking-window chart displays observations by provider and does not silently pool different sources.

## Run the dashboard locally

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run src/app.py
```

The dashboard uses files under `data/processed/`. It refreshes its in-memory data cache every 10 minutes. Use **Refresh dashboard data** in the sidebar to clear the cache immediately.

## Deploy the public dashboard

1. Open [Streamlit Community Cloud](https://share.streamlit.io/).
2. Sign in with GitHub and select this repository.
3. Select branch `main` for the current production release.
4. Set the main file path to `src/app.py`.
5. Deploy. Streamlit Cloud installs the dashboard dependencies from the root `requirements.txt`.

After validating the deployment, merge `deployment` into `main` and change the Streamlit app's selected branch to `main` if you want the production dashboard to follow the default branch.

## Automated airfare observations

The workflow at `.github/workflows/refresh-airfare-observations.yml` runs the five-route/five-lead-window scraper and commits successful additions to `data/processed/live_scraped_quotes_real.csv`.

- The scheduled run is configured for 00:07 India Standard Time (18:37 UTC).
- The workflow can also be started manually from **GitHub → Actions → Refresh airfare observations → Run workflow**.
- Add your Ignav API key under **GitHub → Settings → Secrets and variables → Actions → New repository secret**, using the name `IGNAV_API_KEY`. Do not put the key in a code file or commit it.
- Ignav currently includes 1,000 one-time free successful requests, then charges $2 per 1,000 successful requests. At 25 searches per daily run, one run per day uses about 750 searches per 30-day month; the free allowance lasts about 40 days at that cadence. Repeated manual runs also use the allowance. Set an account spending limit before unattended collection.
- The collector calls the Ignav one-way fares API for five domestic routes and five booking windows using India market (`IN`, INR). It stores only itineraries whose API price status is `verified` and whose returned currency is INR. The API key is read from the `IGNAV_API_KEY` GitHub Actions secret and must never be committed to this repository.
- GitHub scheduled workflows run only from the repository's default branch. The daily schedule therefore becomes active after this deployment branch is merged into `main`. Manual workflow dispatch can be used to test the workflow from `deployment`.
- The workflow needs repository Actions to be enabled and the workflow token to have permission to write contents. Its YAML requests `contents: write`.

The workflow commits a refreshed quote CSV only if validation finds at least one newly collected positive INR fare. Failed requests, empty results, unverified prices, and currency mismatches do not overwrite published observations. A JSON collection summary is uploaded as a short-lived Actions artifact. Ignav's `observed_at` is retained as source metadata; it records when the provider retrieved the result, may reflect a result reused for up to 30 minutes, and is not a guarantee that a fare remains available.

The observation workflow deliberately does **not** regenerate the historical APIx series or the SARIMAX forecast. Those calculations need a validated index methodology that incorporates real scraped fares; the current index engine still generates modelled values independently of the scraper.

## Dependencies

- `requirements.txt`: dashboard runtime.
- `requirements-pipeline.txt`: scraper, index/forecast research pipeline and their additional dependencies.

## Repository layout

```text
src/
  app.py                    Streamlit dashboard
  run_ignav_collection.py  Five-route, five-window Ignav API collector
  index_engine.py           Modelled APIx series and benchmark comparison
  forecaster.py             SARIMAX forecast experiment
  pipeline_scheduler.py     Local pipeline runner

data/
  raw/                      Input reference datasets
  processed/                Dashboard datasets and scraped observations
.github/workflows/          Scheduled data collection workflow
```

## Data interpretation

- **Fare observations:** successful, verified INR rows returned by the configured provider, stored in `live_scraped_quotes_real.csv`. Historical Ixigo rows remain in the file and are displayed separately from Ignav observations.
- **Historical APIx series:** currently generated by the model in `index_engine.py`; it is not yet derived from the scraped fare observations.
- **Forecast:** generated from the modelled APIx series by `forecaster.py`.
- **MoSPI benchmark:** reference CPI data included in the repository. Check the source release and period before making comparisons or public claims.

Use the project for demonstration and research only. API fare observations can change and may differ from final checkout prices. A provider quote is not a reservation or a guarantee of current availability.
