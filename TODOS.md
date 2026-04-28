# SPX Site Sourcing — TODOS

## Before first demo run
- [ ] Set `GOOGLE_MAPS_API_KEY` env var (see setup below)
- [ ] Run feasibility spike: manually search Lamudi PH + Dot Property PH for
      warehouse/industrial in NCR/Cavite/Laguna
      — if <20 qualifying listings found, pivot Phase 1 to broker email intake
- [ ] Verify Playwright selectors against live Lamudi PH and Dot Property PH
      (DOM may differ from April 2026 snapshot — update `_extract_card_links`,
      `_extract_cards` selectors as needed)
- [ ] Install dependencies: `pip install -r requirements.txt && playwright install chromium`
- [ ] Run once: `python -m sourcing.scrapers.orchestrator` to validate end-to-end

## Phase 1.5 (after VP greenlight)
- [ ] Add HTTP basic auth to Streamlit (if dashboard exposed beyond VPN)
- [ ] Set up cron job for daily scrape: `0 6 * * * python -m sourcing.scrapers.orchestrator`
- [ ] Review ToS for Lamudi PH and Dot Property PH before production use

## Score calibration (after 10+ outcomes)
- [ ] Record Phase 1 score for every site that enters the pipeline
- [ ] After 10+ sites reach "Signed" or "Rejected" status, compare scores to outcomes
- [ ] Calibrate weights in spec.yaml based on which fields actually predicted performance
- [ ] Key hypothesis to test: is dock_doors (weight=20) or region (weight=20) more
      predictive of operational success?

## Phase 2 (after VP greenlight)
- [ ] Migrate JSON flat files → Postgres (same schema)
- [ ] Replace Streamlit → Next.js web app
- [ ] Add pipeline stages: Prospect → Contacted → Site Visit → LOI → Negotiating
      → Contract Review → Signed → Pre-Open → Live → Tracked
- [ ] Comms log: structured form (date | channel | contact | 1-line summary)
      per site record — replaces Viber/SMS chaos
- [ ] Contract negotiation playbook: clause list + RED/YELLOW/GREEN + lessor
      proposal field + RED flag alert on dashboard
- [ ] Document storage: S3/GCS links per site record
- [ ] Add Santos Knight Frank PH manual CSV import flow

## Phase 3 (stretch)
- [ ] Viber Business API integration for automated comms capture
- [ ] AI clause comparison: upload redlined PDF → auto-detect changed clauses
      → match against playbook
- [ ] Performance tracking: link post-opening TMS/WMS ops metrics back to
      site record (gated on TMS/WMS data source confirmation)

## Setup instructions

### 1. Install dependencies
```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Geocoding — no key needed (Nominatim default)

Nominatim (OpenStreetMap) is the default — free, no API key, 1 req/sec rate limit.
The persistent cache at `data/geocode_cache.json` means each unique address is
only ever looked up once, so the 1 req/sec limit is not a practical constraint.

**To switch to Google Maps later** (more accurate for barangay-level PH addresses):
```bash
# Windows (PowerShell)
$env:GEOCODING_BACKEND = "google"
$env:GOOGLE_MAPS_API_KEY = "your_key_here"

# Mac/Linux
export GEOCODING_BACKEND=google
export GOOGLE_MAPS_API_KEY="your_key_here"
```

Get a Google Maps key at: https://console.cloud.google.com/
Enable: "Geocoding API" (~$0.005/request; demo with caching ≈ $0.50–2.00 total)

### 3. Run the scraper
```bash
python -m sourcing.scrapers.orchestrator
```

### 4. Launch dashboard
```bash
streamlit run streamlit_app.py
```

### 5. Run tests
```bash
pytest tests/ -v
```
