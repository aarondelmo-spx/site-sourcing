"""
Lamudi PH scraper — warehouse/industrial listings.

Target URL pattern:
  https://www.lamudi.com.ph/commercial/for-rent/?search[city_subdivision_code]={region}
  &search[category]=warehouse&page={n}

Lamudi renders listing cards server-side (initial HTML), which Playwright can
extract without JavaScript execution for basic fields. JS is only needed for
lazy-loaded images and maps — both ignored here.

Strategy:
  1. Load search results page for each region (with 'warehouse' filter)
  2. Extract listing cards from the search results HTML
  3. Follow each card link to the detail page for full spec fields
  4. Parse: sqft, dock_doors, clear_height_m, price, address, region

Selectors are best-effort based on observed Lamudi PH DOM structure (April 2026).
Wrapped in try/except per field — missing fields set to None, never crash.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urlencode, urljoin

from sourcing.models import ListingFields, RawListing
from sourcing.scrapers.base import ScraperBase

# Lamudi PH region codes (used in URL query params)
REGION_CODES = {
    "NCR": "metro-manila",
    "Cavite": "cavite",
    "Laguna": "laguna",
    "Bulacan": "bulacan",
    "Rizal": "rizal",
    "Pampanga": "pampanga",
}

BASE_SEARCH_URL = "https://www.lamudi.com.ph/commercial/for-rent/"
MAX_PAGES = 10  # Safety cap — real pagination rarely exceeds 5 for PH industrial


class LamudiScraper(ScraperBase):
    source_id = "lamudi-ph"

    def scrape_region(self, region: str) -> List[RawListing]:
        """Scrape all warehouse listings for a given region."""
        region_code = REGION_CODES.get(region)
        if region_code is None:
            print(f"[lamudi] Unknown region '{region}' — skipping")
            return []

        from playwright.sync_api import sync_playwright

        listings: List[RawListing] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()

            # Try playwright-stealth if installed
            try:
                from playwright_stealth import stealth_sync
                stealth_sync(page)
            except ImportError:
                pass

            for page_num in range(1, MAX_PAGES + 1):
                params = {
                    "search[city_subdivision_code]": region_code,
                    "search[category]": "warehouse",
                    "page": page_num,
                }
                url = BASE_SEARCH_URL + "?" + urlencode(params)

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                except Exception as e:
                    print(f"[lamudi] Failed to load page {page_num} for {region}: {e}")
                    break

                # Extract listing card links from search results
                card_links = self._extract_card_links(page)
                if not card_links:
                    break  # No more results

                for link in card_links:
                    listing = self._scrape_detail(page, link, region)
                    if listing:
                        listings.append(listing)
                    self._random_delay()

                # Check if there's a next page
                if not self._has_next_page(page):
                    break

            browser.close()

        return listings

    def _extract_card_links(self, page) -> List[str]:
        """Extract listing detail URLs from the search results page."""
        try:
            # Lamudi listing cards typically have class 'ListingCell-content' or similar
            # Use multiple selector fallbacks
            selectors = [
                "a.ListingCell-content",
                "a[class*='listing-card']",
                "div.ListingCell a[href*='/commercial/']",
                "article a[href*='/commercial/']",
            ]
            links = set()
            for selector in selectors:
                elements = page.query_selector_all(selector)
                for el in elements:
                    href = el.get_attribute("href")
                    if href and ("/for-rent/" in href or "/for-sale/" in href):
                        full_url = urljoin("https://www.lamudi.com.ph", href)
                        links.add(full_url)
                if links:
                    break

            # Fallback: find all links containing 'commercial' in path
            if not links:
                all_links = page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(e => e.href)"
                )
                for href in all_links:
                    if "/commercial/" in href and (
                        "lamudi.com.ph" in href
                    ):
                        links.add(href)

            return list(links)[:30]  # cap per page to avoid runaway
        except Exception as e:
            print(f"[lamudi] Failed to extract card links: {e}")
            return []

    def _scrape_detail(self, page, url: str, region: str) -> Optional[RawListing]:
        """Load a listing detail page and extract spec fields."""
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:
            print(f"[lamudi] Failed to load detail {url}: {e}")
            return None

        if response and response.status == 404:
            listing_id = self._url_to_id(url)
            return RawListing(
                id=self.make_id(self.source_id, listing_id),
                source=self.source_id,
                url=url,
                status="not_found",
            )

        try:
            title = self._safe_text(page, [
                "h1.Title",
                "h1[class*='title']",
                "h1",
            ])

            address = self._safe_text(page, [
                "span[class*='location']",
                "div[class*='address']",
                "p[class*='address']",
                "[itemprop='address']",
            ])

            # Price
            price_str = self._safe_text(page, [
                "span[class*='price']",
                "div[class*='price']",
                "[class*='PriceContainer']",
            ])
            price_php = self._parse_price_php(price_str)

            # Floor area / sqft
            sqft_raw = self._safe_attr_or_text(page, [
                ("span[data-attribute='floor_size']", None),
                ("[class*='FloorSize']", None),
                ("[class*='floor-area']", None),
            ])
            sqft = self.parse_sqft(sqft_raw)

            # Dock doors — often in "amenities" or "attributes" section
            dock_doors_raw = self._find_attribute_value(page, [
                "dock", "loading dock", "dock door", "loading bay"
            ])
            dock_doors = self.parse_int(dock_doors_raw)

            # Clear height
            height_raw = self._find_attribute_value(page, [
                "ceiling height", "clear height", "clearance", "height"
            ])
            clear_height_m = self._parse_height_m(height_raw)

            listing_id = self._url_to_id(url)

            return RawListing(
                id=self.make_id(self.source_id, listing_id),
                source=self.source_id,
                url=url,
                scraped_at=datetime.now(timezone.utc),
                status="active",
                listing=ListingFields(
                    title=title or "",
                    sqft=sqft,
                    dock_doors=dock_doors,
                    clear_height_m=clear_height_m,
                    address=address or "",
                    region=region,
                    price_php=price_php,
                    price_unit="per sqm/month",
                ),
            )
        except Exception as e:
            print(f"[lamudi] Parse error for {url}: {e}")
            return None

    def _has_next_page(self, page) -> bool:
        """Check if there's a next page button/link."""
        try:
            next_btn = page.query_selector("a[aria-label='Next page'], a.next, a[rel='next']")
            return next_btn is not None
        except Exception:
            return False

    def _safe_text(self, page, selectors: list) -> Optional[str]:
        """Try each selector, return first non-empty text found."""
        for sel in selectors:
            try:
                el = page.query_selector(sel)
                if el:
                    text = el.inner_text().strip()
                    if text:
                        return text
            except Exception:
                continue
        return None

    def _safe_attr_or_text(self, page, selector_attr_pairs: list) -> Optional[str]:
        for sel, attr in selector_attr_pairs:
            try:
                el = page.query_selector(sel)
                if el:
                    val = el.get_attribute(attr) if attr else el.inner_text().strip()
                    if val:
                        return val
            except Exception:
                continue
        return None

    def _find_attribute_value(self, page, keywords: list) -> Optional[str]:
        """
        Search through listing attribute rows for a value matching any keyword.
        Lamudi attributes are typically in <li> or <div> pairs: label | value.
        """
        try:
            # Try structured attribute blocks
            rows = page.query_selector_all(
                "ul.Details li, div.Attributes div, div[class*='attribute-row']"
            )
            for row in rows:
                text = row.inner_text().lower()
                for kw in keywords:
                    if kw in text:
                        # Value is usually after the colon or in a child span
                        parts = re.split(r"[:|\n]", row.inner_text(), maxsplit=1)
                        if len(parts) > 1:
                            return parts[1].strip()
                        return row.inner_text().strip()
        except Exception:
            pass
        return None

    def _parse_price_php(self, price_str: Optional[str]) -> Optional[float]:
        if not price_str:
            return None
        s = price_str.replace(",", "").replace("₱", "").replace("PHP", "").strip()
        match = re.search(r"[\d.]+", s)
        if match:
            try:
                return float(match.group())
            except ValueError:
                pass
        return None

    def _parse_height_m(self, raw: Optional[str]) -> Optional[float]:
        """Parse height from strings like '7m', '7.5 meters', '25 ft'."""
        if not raw:
            return None
        s = raw.lower()
        is_ft = "ft" in s or "feet" in s or "foot" in s
        match = re.search(r"[\d.]+", s.replace(",", ""))
        if not match:
            return None
        try:
            val = float(match.group())
        except ValueError:
            return None
        if is_ft:
            return round(val * 0.3048, 2)
        return round(val, 2)

    @staticmethod
    def _url_to_id(url: str) -> str:
        """
        Extract a stable ID from a Lamudi URL.
        Lamudi URLs typically end with a numeric ID: /property-name-12345.html
        Falls back to MD5 hash of URL if no ID found.
        """
        match = re.search(r"-(\d{5,})(?:\.html)?/?$", url)
        if match:
            return match.group(1)
        return hashlib.md5(url.encode()).hexdigest()[:12]
