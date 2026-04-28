"""
Dot Property PH scraper — warehouse/industrial listings.

Target URL pattern:
  https://www.dotproperty.com.ph/warehouses-for-rent/{region-slug}?page={n}
  https://www.dotproperty.com.ph/warehouses-for-sale/{region-slug}?page={n}

Dot Property PH has dedicated warehouse/industrial URL paths and structured
region-based paths that map cleanly to our spec regions. PEZA status and
loading dock data are often visible in listing cards.

Strategy:
  1. Iterate rent + sale URLs per region
  2. Extract listing cards from search results (HTML-rendered on Dot Property PH)
  3. For each card, optionally visit detail page for full spec fields
  4. Parse: sqft, dock_doors, clear_height_m, price, address, region, PEZA status
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urljoin

from sourcing.models import ListingFields, RawListing
from sourcing.scrapers.base import ScraperBase

# Region slug mapping for Dot Property PH URL structure
REGION_SLUGS = {
    "NCR": ["metro-manila"],
    "Cavite": ["cavite"],
    "Laguna": [
        "laguna",
        "laguna/calamba",
        "laguna/cabuyao",
        "laguna/sta-rosa",
        "laguna/binan",
    ],
    "Bulacan": ["bulacan"],
    "Rizal": ["rizal"],
    "Pampanga": ["pampanga"],
}

BASE_URL = "https://www.dotproperty.com.ph"
LISTING_TYPES = ["warehouses-for-rent", "warehouses-for-sale"]
MAX_PAGES = 8


class DotPropertyScraper(ScraperBase):
    source_id = "dotproperty-ph"

    def scrape_region(self, region: str) -> List[RawListing]:
        """Scrape all warehouse listings for a given region (rent + sale)."""
        slugs = REGION_SLUGS.get(region)
        if not slugs:
            print(f"[dotproperty] Unknown region '{region}' — skipping")
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
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()

            try:
                from playwright_stealth import stealth_sync
                stealth_sync(page)
            except ImportError:
                pass

            for listing_type in LISTING_TYPES:
                for slug in slugs:
                    for page_num in range(1, MAX_PAGES + 1):
                        url = f"{BASE_URL}/{listing_type}/{slug}"
                        if page_num > 1:
                            url += f"?page={page_num}"

                        try:
                            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                        except Exception as e:
                            print(f"[dotproperty] Failed to load {url}: {e}")
                            break

                        card_data = self._extract_cards(page, region)
                        if not card_data:
                            break

                        for card in card_data:
                            listing = self._build_listing_from_card(
                                page, card, region, listing_type
                            )
                            if listing:
                                listings.append(listing)
                            self._random_delay()

                        if not self._has_next_page(page):
                            break

            browser.close()

        return listings

    def _extract_cards(self, page, region: str) -> List[dict]:
        """
        Extract listing data from search result cards.
        Dot Property PH shows key fields in the card (sqft, price, location).
        Returns list of dicts with raw field values + detail URL.
        """
        cards = []
        try:
            # Dot Property listing cards selector patterns
            card_selectors = [
                "div[class*='listing-card']",
                "article[class*='listing']",
                "div[class*='property-listing']",
                "li[class*='listing']",
            ]
            elements = []
            for sel in card_selectors:
                elements = page.query_selector_all(sel)
                if elements:
                    break

            for el in elements:
                try:
                    card = {}

                    # Detail URL
                    link_el = el.query_selector("a[href*='/property/'], a[href*='/warehouse/']")
                    if not link_el:
                        link_el = el.query_selector("a[href]")
                    if link_el:
                        href = link_el.get_attribute("href")
                        if href:
                            card["url"] = urljoin(BASE_URL, href)

                    # Title
                    title_el = el.query_selector(
                        "h2, h3, [class*='title'], [class*='name']"
                    )
                    card["title"] = title_el.inner_text().strip() if title_el else ""

                    # Price
                    price_el = el.query_selector("[class*='price']")
                    card["price_raw"] = price_el.inner_text().strip() if price_el else ""

                    # Location / address
                    loc_el = el.query_selector(
                        "[class*='location'], [class*='address'], [class*='area']"
                    )
                    card["address"] = loc_el.inner_text().strip() if loc_el else ""

                    # Floor area
                    area_el = el.query_selector(
                        "[class*='floor-area'], [class*='size'], [data-type='area']"
                    )
                    card["sqft_raw"] = area_el.inner_text().strip() if area_el else ""

                    if card.get("url"):
                        cards.append(card)
                except Exception:
                    continue

        except Exception as e:
            print(f"[dotproperty] Card extraction error: {e}")

        return cards

    def _build_listing_from_card(
        self, page, card: dict, region: str, listing_type: str
    ) -> Optional[RawListing]:
        """
        Build a RawListing from card data.
        For dock_doors and clear_height_m (rarely in cards), visit detail page.
        """
        url = card.get("url")
        if not url:
            return None

        # Try to get dock_doors + clear_height from detail page
        dock_doors = None
        clear_height_m = None

        try:
            detail_resp = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:
            print(f"[dotproperty] Failed to load detail {url}: {e}")
            detail_resp = None

        if detail_resp and detail_resp.status == 404:
            listing_id = self._url_to_id(url)
            return RawListing(
                id=self.make_id(self.source_id, listing_id),
                source=self.source_id,
                url=url,
                status="not_found",
            )

        if detail_resp:
            dock_doors_raw = self._find_attribute_value(page, [
                "dock", "loading dock", "dock door", "loading bay", "bay door"
            ])
            dock_doors = self.parse_int(dock_doors_raw)

            height_raw = self._find_attribute_value(page, [
                "ceiling height", "clear height", "clearance", "height"
            ])
            clear_height_m = self._parse_height_m(height_raw)

            # Override sqft from detail if card value was empty
            if not card.get("sqft_raw"):
                sqft_raw = self._find_attribute_value(page, [
                    "floor area", "lot area", "total area", "building area"
                ])
                if sqft_raw:
                    card["sqft_raw"] = sqft_raw

            # Override address from detail if card was empty
            if not card.get("address"):
                addr_el = page.query_selector(
                    "[class*='address'], [itemprop='address'], "
                    "[class*='location']"
                )
                if addr_el:
                    card["address"] = addr_el.inner_text().strip()

        listing_id = self._url_to_id(url)
        sqft = self.parse_sqft(card.get("sqft_raw"))
        price_php = self._parse_price_php(card.get("price_raw"))

        return RawListing(
            id=self.make_id(self.source_id, listing_id),
            source=self.source_id,
            url=url,
            scraped_at=datetime.now(timezone.utc),
            status="active",
            listing=ListingFields(
                title=card.get("title", ""),
                sqft=sqft,
                dock_doors=dock_doors,
                clear_height_m=clear_height_m,
                address=card.get("address", ""),
                region=region,
                price_php=price_php,
                price_unit="per sqm/month" if "rent" in listing_type else "total",
                raw_extras={"listing_type": listing_type},
            ),
        )

    def _has_next_page(self, page) -> bool:
        try:
            next_el = page.query_selector(
                "a[aria-label='Next'], a.next-page, "
                "a[rel='next'], [class*='pagination'] a:last-child"
            )
            return next_el is not None
        except Exception:
            return False

    def _find_attribute_value(self, page, keywords: list) -> Optional[str]:
        """Search detail page attribute rows for a keyword match."""
        try:
            rows = page.query_selector_all(
                "li[class*='feature'], div[class*='feature'], "
                "tr, dl dt, div[class*='detail-row'], "
                "div[class*='attribute']"
            )
            for row in rows:
                text = row.inner_text().lower()
                for kw in keywords:
                    if kw in text:
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
        """Extract stable ID from Dot Property URL or hash it."""
        # Dot Property URLs end with slug or numeric ID
        match = re.search(r"/(\d{4,})/?$", url)
        if match:
            return match.group(1)
        # Try slug as ID
        slug_match = re.search(r"/property/([^/?]+)", url)
        if slug_match:
            return slug_match.group(1)[:40]
        return hashlib.md5(url.encode()).hexdigest()[:12]
