#!/usr/bin/env python3
"""
fetch_data.py
─────────────
Reads market definitions from markets_config.json (produced by auto_discover.py),
fetches current prices from the Polymarket Gamma API, and appends to docs/data.json.

Run every 30 minutes via GitHub Actions.
"""

import json
import subprocess
import os
from datetime import datetime, timezone

GAMMA_API   = "https://gamma-api.polymarket.com"
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "markets_config.json")
DATA_PATH   = os.path.join(os.path.dirname(__file__), "docs", "data.json")
MAX_HISTORY = 1440   # ~30 days at 30-min intervals


def curl(url, timeout=15):
    result = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-H", "User-Agent: Mozilla/5.0", url],
        capture_output=True, text=True
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def fetch_event(event_id):
    data = curl(f"{GAMMA_API}/events?id={event_id}", timeout=12)
    if isinstance(data, list) and data:
        return data[0]
    return None


def find_price_by_market_id(event, market_id, outcome):
    for market in event.get("markets", []):
        if str(market.get("id")) != str(market_id):
            continue
        outcomes = market.get("outcomes", "[]")
        prices   = market.get("outcomePrices", "[]")
        if isinstance(outcomes, str): outcomes = json.loads(outcomes)
        if isinstance(prices,   str): prices   = json.loads(prices)
        for o, p in zip(outcomes, prices):
            if o.lower() == outcome.lower():
                try:
                    return round(float(p), 4)
                except (ValueError, TypeError):
                    return None
    return None


def find_price_by_question(event, question_sub, outcome):
    q_lower = question_sub.lower()
    for market in event.get("markets", []):
        if q_lower not in (market.get("question") or "").lower():
            continue
        outcomes = market.get("outcomes", "[]")
        prices   = market.get("outcomePrices", "[]")
        if isinstance(outcomes, str): outcomes = json.loads(outcomes)
        if isinstance(prices,   str): prices   = json.loads(prices)
        for o, p in zip(outcomes, prices):
            if o.lower() == outcome.lower():
                try:
                    return round(float(p), 4)
                except (ValueError, TypeError):
                    return None
    return None


def load_config():
    """Load markets_config.json. Falls back gracefully if missing."""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)

    print("WARNING: markets_config.json not found. Run auto_discover.py first.")
    # Return minimal structure so the script doesn't crash
    return {"categories": {}, "markets": []}


def load_data():
    if os.path.exists(DATA_PATH):
        try:
            with open(DATA_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_updated": None, "categories": {}, "markets": {}}


def main():
    config      = load_config()
    data        = load_data()
    now_ts      = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    markets_cfg = config.get("markets", [])

    print(f"[{now_ts}] Fetching {len(markets_cfg)} markets...")

    # Keep category metadata up to date
    data["categories"] = config.get("categories", data.get("categories", {}))

    event_cache   = {}
    success_count = 0
    errors        = []

    for cfg in markets_cfg:
        mid      = cfg.get("id")
        event_id = cfg.get("event_id")
        if not mid or not event_id:
            continue

        # Fetch event (cached)
        if event_id not in event_cache:
            print(f"  Fetching event {event_id}...", end=" ", flush=True)
            event = fetch_event(event_id)
            event_cache[event_id] = event
            print("OK" if event else "FAILED")
        event = event_cache[event_id]

        if event is None:
            errors.append(mid)
            continue

        # Find price: market_id preferred, fall back to question substring
        outcome   = cfg.get("outcome", "Yes")
        market_id = cfg.get("market_id")
        question  = cfg.get("question", "")

        price = None
        if market_id:
            price = find_price_by_market_id(event, str(market_id), outcome)
        if price is None and question:
            price = find_price_by_question(event, question, outcome)

        if price is None:
            print(f"  WARNING: No price for {mid}")
            errors.append(mid)
            if mid not in data["markets"]:
                data["markets"][mid] = {
                    "id": mid, "category": cfg.get("category",""),
                    "label": cfg.get("label",""), "question": question,
                    "polymarket_url": cfg.get("polymarket_url",""),
                    "current": None, "vol_24h": 0, "vol_total": 0,
                    "liquidity": 0, "history": [],
                }
            continue

        vol_24h   = round(float(event.get("volume24hr") or 0))
        vol_total = round(float(event.get("volume")     or 0))
        liquidity = round(float(event.get("liquidity")  or 0))

        if mid not in data["markets"]:
            data["markets"][mid] = {"history": []}

        m = data["markets"][mid]
        m.update({
            "id":            mid,
            "current":       price,
            "label":         cfg.get("label", m.get("label", "")),
            "sub_label":     cfg.get("sub_label"),
            "group_id":      cfg.get("group_id"),
            "category":      cfg.get("category", m.get("category", "")),
            "question":      question,
            "polymarket_url":cfg.get("polymarket_url", m.get("polymarket_url", "")),
            "vol_24h":       vol_24h,
            "vol_total":     vol_total,
            "liquidity":     liquidity,
        })

        m["history"].append({"t": now_ts, "v": price})
        if len(m["history"]) > MAX_HISTORY:
            m["history"] = m["history"][-MAX_HISTORY:]

        success_count += 1
        print(f"  ✓ {cfg.get('label','')[:52]:52s}: {price*100:.1f}%")

    data["last_updated"] = now_ts

    # Remove markets no longer in config (cleanup stale entries)
    active_ids = {cfg.get("id") for cfg in markets_cfg if cfg.get("id")}
    stale = [mid for mid in list(data["markets"]) if mid not in active_ids]
    for mid in stale:
        del data["markets"][mid]
    if stale:
        print(f"  Removed {len(stale)} stale markets: {stale[:5]}{'...' if len(stale)>5 else ''}")

    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w") as f:
        json.dump(data, f, separators=(",", ":"))

    print(f"\n✅ Done: {success_count}/{len(markets_cfg)} updated, {len(errors)} errors")
    if errors:
        print(f"   Errors: {errors}")


if __name__ == "__main__":
    main()
