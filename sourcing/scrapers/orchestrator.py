"""
Scraper orchestrator — run all sources for all spec regions.

This is the entry point called as a subprocess by the Streamlit dashboard.
It writes progress to data/status.json as it runs so the dashboard can poll.

Usage (called by dashboard via subprocess):
    python -m sourcing.scrapers.orchestrator --spec spec.yaml

Geocoding defaults to Nominatim (free, no key needed).
To switch to Google Maps: set GEOCODING_BACKEND=google and GOOGLE_MAPS_API_KEY.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

# Allow running as script from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sourcing.geocoding.geocoder import Geocoder, MissingApiKeyError
from sourcing.models import ScraperStatus, SpecConfig, load_spec
from sourcing.scrapers.dotproperty import ALL_REGION_SLUGS as DP_ALL_REGIONS, DotPropertyScraper
from sourcing.scrapers.lamudi import ALL_REGION_CODES as LAMUDI_ALL_REGIONS, LamudiScraper
from sourcing.scorer.engine import ScoringEngine
from sourcing.storage import mark_stale, save_status


def run_scrape(spec: SpecConfig, data_dir: str = "data", all_regions: bool = False) -> None:
    """
    Full scrape + score pipeline.
    Writes status.json throughout for dashboard polling.

    all_regions=True: scrape all known PH provinces, not just spec.regions.
    Spec.regions is still used for scoring (region weight) but not for URL scope.
    """
    status = ScraperStatus(
        state="running",
        pid=os.getpid(),
        started_at=datetime.now(timezone.utc).isoformat(),
        message="Initializing...",
    )
    save_status(status)

    # ── 1. Init geocoder (fails fast only if Google backend is selected without key) ──
    try:
        geocoder = Geocoder()
    except MissingApiKeyError as e:
        status.state = "error"
        status.last_error = str(e)
        status.message = f"ERROR: {e}"
        save_status(status)
        print(f"\n{e}\n", file=sys.stderr)
        sys.exit(1)

    status.message = f"Geocoding backend: {geocoder.backend}"
    # Rough estimate: regions × 2 scrapers × ~70 listings each
    regions_count = len(spec.regions) if not all_regions else 30
    status.total = regions_count * 2 * 70
    save_status(status)

    # ── 2. Mark stale listings (re-check these first) ─────────────────────────
    stale_count = mark_stale(os.path.join(data_dir, "raw"))
    if stale_count:
        status.message = f"Marked {stale_count} listings as stale — re-checking..."
        save_status(status)

    # ── 3. Run scrapers ───────────────────────────────────────────────────────
    # When all_regions=True each scraper gets a regions_override covering all PH.
    # The spec.regions list is still used by the scorer for the region score weight.
    lamudi_regions  = list(LAMUDI_ALL_REGIONS.keys())  if all_regions else None
    dp_regions      = list(DP_ALL_REGIONS.keys())      if all_regions else None

    scraper_region_pairs = [
        (LamudiScraper(geocoder=geocoder,      spec=spec, data_dir=data_dir), lamudi_regions),
        (DotPropertyScraper(geocoder=geocoder, spec=spec, data_dir=data_dir), dp_regions),
    ]

    scope_label = "all PH regions" if all_regions else f"spec regions: {spec.regions}"
    status.message = f"Scraping {scope_label}..."
    save_status(status)

    all_results = []
    for scraper, regions_override in scraper_region_pairs:
        status.message = f"Starting {scraper.source_id}..."
        save_status(status)
        try:
            results = scraper.run(status, regions_override=regions_override)
            all_results.extend(results)
            status.message = (
                f"{scraper.source_id} complete — {len(results)} listings"
            )
            save_status(status)
        except Exception as e:
            status.last_error = str(e)
            status.message = f"{scraper.source_id} failed: {e}"
            save_status(status)
            print(f"[orchestrator] {scraper.source_id} error: {e}", file=sys.stderr)
            # Continue with next scraper — partial results are better than nothing

    # ── 4. Score results ──────────────────────────────────────────────────────
    status.message = "Scoring listings..."
    save_status(status)

    try:
        engine = ScoringEngine(spec=spec, data_dir=data_dir)
        scored = engine.score_all(os.path.join(data_dir, "raw"))
        complete_count = len(engine.complete)
        incomplete_count = len(engine.incomplete)
        status.message = (
            f"Done — {complete_count} ranked, {incomplete_count} incomplete"
        )
    except FileNotFoundError as e:
        status.last_error = str(e)
        status.message = f"Scoring skipped: {e}"

    # ── 5. Finalize status ────────────────────────────────────────────────────
    status.state = "done"
    status.finished_at = datetime.now(timezone.utc).isoformat()
    status.total = len(all_results)
    save_status(status)

    print(f"\n[orchestrator] Complete. {len(all_results)} listings scraped.")


def main():
    parser = argparse.ArgumentParser(description="SPX Site Sourcing Scraper")
    parser.add_argument(
        "--spec", default="spec.yaml", help="Path to spec.yaml (default: spec.yaml)"
    )
    parser.add_argument(
        "--data-dir", default="data", help="Base data directory (default: data)"
    )
    parser.add_argument(
        "--all-regions", action="store_true",
        help="Scrape all known PH provinces, not just spec.regions. "
             "Use this for a full nationwide sweep. Scoring still uses spec.regions."
    )
    args = parser.parse_args()

    try:
        spec = load_spec(args.spec)
    except Exception as e:
        print(f"ERROR: Failed to load spec.yaml: {e}", file=sys.stderr)
        sys.exit(1)

    run_scrape(spec, data_dir=args.data_dir, all_regions=args.all_regions)


if __name__ == "__main__":
    main()
