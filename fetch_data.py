#!/usr/bin/env python3
"""
Polymarket Data Fetcher
Fetches probability data from Polymarket and updates docs/data.json
"""

import json
import subprocess
import os
from datetime import datetime, timezone

# ─────────────────────────────────────────────────────────────────────────────
# MARKET CONFIGURATION
# Each entry: event_id, question substring to match, outcome to track, metadata
# ─────────────────────────────────────────────────────────────────────────────
TRACKED = [
    # ── Iran / Geopolitical ──────────────────────────────────────────────────
    {
        "id": "us_invades_iran",
        "category": "iran_geopolitical",
        "label": "US invades Iran before 2027",
        "event_id": 73130,
        "q_contains": "U.S. invade Iran",
        "outcome": "Yes",
        "slug": "will-the-us-invade-iran-before-2027",
    },
    {
        "id": "iranian_regime_falls",
        "category": "iran_geopolitical",
        "label": "Iranian regime falls before 2027",
        "event_id": 72347,
        "q_contains": "Iranian regime fall",
        "outcome": "Yes",
        "slug": "will-the-iranian-regime-fall-before-2027",
    },
    {
        "id": "iran_nuke",
        "category": "iran_geopolitical",
        "label": "Iran acquires nuclear weapon before 2027",
        "event_id": 79222,
        "q_contains": "Iran Nuke",
        "outcome": "Yes",
        "slug": "iran-nuke-before-2027",
    },
    {
        "id": "iran_nuclear_test",
        "category": "iran_geopolitical",
        "label": "Iran conducts nuclear test before 2027",
        "event_id": 73227,
        "q_contains": "Iran nuclear test",
        "outcome": "Yes",
        "slug": "iran-nuclear-test-before-2027",
    },
    {
        "id": "iran_npt_withdrawal",
        "category": "iran_geopolitical",
        "label": "Iran withdraws from NPT before 2027",
        "event_id": 73330,
        "q_contains": "Iran withdraw from the NPT",
        "outcome": "Yes",
        "slug": "will-iran-withdraw-from-the-npt-before-2027",
    },
    # ── Fed / Interest Rates ─────────────────────────────────────────────────
    {
        "id": "fed_april_hold",
        "category": "fed_rates",
        "label": "Fed holds rates in April 2026",
        "event_id": 75478,
        "q_contains": "no change in Fed interest rates after the April",
        "outcome": "Yes",
        "slug": "fed-decision-in-april",
    },
    {
        "id": "fed_april_hike",
        "category": "fed_rates",
        "label": "Fed hikes rates in April 2026",
        "event_id": 75478,
        "q_contains": "increase interest rates by 25+ bps after the April",
        "outcome": "Yes",
        "slug": "fed-decision-in-april",
    },
    {
        "id": "fed_june_hold",
        "category": "fed_rates",
        "label": "Fed holds rates in June 2026",
        "event_id": 101772,
        "q_contains": "no change in Fed interest rates after the June",
        "outcome": "Yes",
        "slug": "fed-decision-in-june",
    },
    {
        "id": "fed_june_cut25",
        "category": "fed_rates",
        "label": "Fed cuts 25bps in June 2026",
        "event_id": 101772,
        "q_contains": "decrease interest rates by 25 bps after the June",
        "outcome": "Yes",
        "slug": "fed-decision-in-june",
    },
    {
        "id": "fed_2026_zero_cuts",
        "category": "fed_rates",
        "label": "No Fed rate cuts in 2026",
        "event_id": 51456,
        "q_contains": "no Fed rate cuts happen in 2026",
        "outcome": "Yes",
        "slug": "how-many-fed-rate-cuts-in-2026",
    },
    {
        "id": "fed_rate_hike_2026",
        "category": "fed_rates",
        "label": "Fed rate hike in 2026",
        "event_id": 101936,
        "q_contains": "Fed rate hike in 2026",
        "outcome": "Yes",
        "slug": "fed-rate-hike-in-2026",
    },
    {
        "id": "fed_emergency_cut",
        "category": "fed_rates",
        "label": "Fed emergency rate cut before 2027",
        "event_id": 79124,
        "q_contains": "Fed emergency rate cut",
        "outcome": "Yes",
        "slug": "fed-emergency-rate-cut-before-2027",
    },
    # ── Economy / Macro ──────────────────────────────────────────────────────
    {
        "id": "us_recession_2026",
        "category": "economy_macro",
        "label": "US recession by end of 2026",
        "event_id": 48802,
        "q_contains": "US recession by end of 2026",
        "outcome": "Yes",
        "slug": "us-recession-by-end-of-2026",
    },
    {
        "id": "inflation_above_4pct",
        "category": "economy_macro",
        "label": "US inflation exceeds 4% in 2026",
        "event_id": 80773,
        "q_contains": "reach more than 4%",
        "outcome": "Yes",
        "slug": "how-high-will-inflation-get-in-2026",
    },
    {
        "id": "inflation_above_5pct",
        "category": "economy_macro",
        "label": "US inflation exceeds 5% in 2026",
        "event_id": 80773,
        "q_contains": "reach more than 5%",
        "outcome": "Yes",
        "slug": "how-high-will-inflation-get-in-2026",
    },
    {
        "id": "negative_gdp_2026",
        "category": "economy_macro",
        "label": "US negative GDP growth in 2026",
        "event_id": 80660,
        "q_contains": "Negative GDP growth in 2026",
        "outcome": "Yes",
        "slug": "negative-gdp-growth-in-2026",
    },
    {
        "id": "debt_downgrade",
        "category": "economy_macro",
        "label": "Another US credit downgrade before 2027",
        "event_id": 73338,
        "q_contains": "debt downgrade before 2027",
        "outcome": "Yes",
        "slug": "another-us-debt-downgrade-before-2027",
    },
    {
        "id": "nyse_circuit_breaker",
        "category": "economy_macro",
        "label": "NYSE market-wide circuit breaker triggered before 2027",
        "event_id": 75598,
        "q_contains": "NYSE marketwide circuit breaker",
        "outcome": "Yes",
        "slug": "nyse-marketwide-circuit-breaker-before-2027",
    },
    # ── Markets / Assets ─────────────────────────────────────────────────────
    {
        "id": "gold_best_2026",
        "category": "markets_assets",
        "label": "Gold outperforms BTC & S&P 500 in 2026",
        "event_id": 106981,
        "q_contains": "Gold have the best performance in 2026",
        "outcome": "Yes",
        "slug": "bitcoin-vs-gold-vs-s-p-500-in-2026",
    },
    {
        "id": "sp500_q1_negative",
        "category": "markets_assets",
        "label": "S&P 500 Q1 2026 change < 3%",
        "event_id": 162001,
        "q_contains": "percentage change in the S&P 500 in Q1 2026 be less than",
        "outcome": "Yes",
        "slug": "q1-s-p-500-performance",
    },
    {
        "id": "sp500_close_below_6000",
        "category": "markets_assets",
        "label": "S&P 500 closes below $6,000 in Dec 2026",
        "event_id": 148015,
        "q_contains": "S&P 500 (SPX) close at <$6,000 in December",
        "outcome": "Yes",
        "slug": "what-will-s-p-500-spx-close-at-end-of-2026",
    },
    {
        "id": "crude_above_90_june",
        "category": "markets_assets",
        "label": "WTI Crude Oil > $90 at end of June 2026",
        "event_id": 125878,
        "q_contains": "over $90 on the final trading day of J",
        "outcome": "Yes",
        "slug": "crude-oil-cl-above-end-of-june",
    },
    {
        "id": "gold_hit_6000_june",
        "category": "markets_assets",
        "label": "Gold futures hit $6,000 by end of June 2026",
        "event_id": 125865,
        "q_contains": "hit (HIGH) $6,000 by end of June",
        "outcome": "Yes",
        "slug": "what-will-gold-gc-hit-by-end-of-june",
    },
]

CATEGORIES = {
    "iran_geopolitical": {
        "label": "Iran & Geopolitical Risk",
        "icon": "🚨",
        "color": "red",
    },
    "fed_rates": {
        "label": "Fed & Interest Rates",
        "icon": "🏦",
        "color": "blue",
    },
    "economy_macro": {
        "label": "Economy & Inflation",
        "icon": "📊",
        "color": "amber",
    },
    "markets_assets": {
        "label": "Markets & Assets",
        "icon": "📈",
        "color": "green",
    },
}

MAX_HISTORY = 1440  # keep last 30 days of 30-min snapshots


def fetch_event(event_id):
    """Fetch event data from Polymarket Gamma API."""
    url = f"https://gamma-api.polymarket.com/events?id={event_id}"
    result = subprocess.run(
        ["curl", "-s", "--max-time", "15", "-H", "User-Agent: Mozilla/5.0", url],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        return data[0] if data else None
    except (json.JSONDecodeError, IndexError):
        return None


def find_market_price(event, q_contains, outcome):
    """Find price for a specific outcome in an event's markets."""
    markets = event.get("markets", [])
    q_lower = q_contains.lower()

    for market in markets:
        question = (market.get("question") or "").lower()
        if q_lower.lower() not in question:
            continue

        outcomes_raw = market.get("outcomes", "[]")
        prices_raw = market.get("outcomePrices", "[]")

        if isinstance(outcomes_raw, str):
            outcomes_raw = json.loads(outcomes_raw)
        if isinstance(prices_raw, str):
            prices_raw = json.loads(prices_raw)

        for o, p in zip(outcomes_raw, prices_raw):
            if o.lower() == outcome.lower():
                try:
                    return round(float(p), 4)
                except (ValueError, TypeError):
                    return None
    return None


def load_existing_data(path):
    """Load existing data.json or create empty structure."""
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_updated": None, "categories": CATEGORIES, "markets": {}, "version": 1}


def main():
    data_path = os.path.join(os.path.dirname(__file__), "docs", "data.json")
    data = load_existing_data(data_path)
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"[{now_ts}] Fetching {len(TRACKED)} markets...")

    # Cache fetched events to avoid duplicate API calls
    event_cache = {}
    success_count = 0
    errors = []

    for cfg in TRACKED:
        event_id = cfg["event_id"]

        # Fetch event (cached)
        if event_id not in event_cache:
            print(f"  Fetching event {event_id}...", end=" ", flush=True)
            event = fetch_event(event_id)
            event_cache[event_id] = event
            if event:
                print("OK")
            else:
                print("FAILED")
        event = event_cache[event_id]

        market_id = cfg["id"]

        if event is None:
            errors.append(market_id)
            continue

        price = find_market_price(event, cfg["q_contains"], cfg["outcome"])

        if price is None:
            print(f"  WARNING: Could not find market '{cfg['q_contains']}' in event {event_id}")
            errors.append(market_id)
            # Keep existing data without update
            if market_id not in data["markets"]:
                data["markets"][market_id] = {
                    "id": market_id,
                    "category": cfg["category"],
                    "label": cfg["label"],
                    "polymarket_url": f"https://polymarket.com/event/{cfg['slug']}",
                    "current": None,
                    "history": [],
                }
            continue

        # Initialize market entry if new
        if market_id not in data["markets"]:
            data["markets"][market_id] = {
                "id": market_id,
                "category": cfg["category"],
                "label": cfg["label"],
                "polymarket_url": f"https://polymarket.com/event/{cfg['slug']}",
                "current": price,
                "history": [],
            }

        m = data["markets"][market_id]
        m["current"] = price
        m["label"] = cfg["label"]
        m["category"] = cfg["category"]
        m["polymarket_url"] = f"https://polymarket.com/event/{cfg['slug']}"

        # Append to history
        m["history"].append({"t": now_ts, "v": price})

        # Trim history
        if len(m["history"]) > MAX_HISTORY:
            m["history"] = m["history"][-MAX_HISTORY:]

        success_count += 1
        print(f"  ✓ {cfg['label'][:50]}: {price*100:.1f}%")

    # Update metadata
    data["last_updated"] = now_ts
    data["categories"] = CATEGORIES

    # Save
    os.makedirs(os.path.dirname(data_path), exist_ok=True)
    with open(data_path, "w") as f:
        json.dump(data, f, separators=(",", ":"))

    print(f"\n✅ Done: {success_count}/{len(TRACKED)} markets updated, {len(errors)} errors")
    if errors:
        print(f"   Errors: {errors}")


if __name__ == "__main__":
    main()
