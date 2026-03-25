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

OPENAI_API_KEY       = os.environ.get("OPENAI_API_KEY", "")
SUMMARY_INTERVAL_MIN = 60   # regenerate summary at most every 60 minutes


def generate_summary(label: str, question: str, current_pct: float,
                     d24h_pp: float, vol_24h: int, scenarios: list[dict] = None) -> str:
    """Call OpenAI gpt-4o-mini to generate a market context summary."""
    if not OPENAI_API_KEY:
        return ""

    if scenarios:
        sc_lines = "\n".join(f"  {s['sub_label']}: {s['current_pct']:.1f}%" for s in scenarios)
        prompt = (f"Market: \"{label}\"\nScenarios:\n{sc_lines}\n"
                  f"Vol 24h: ${vol_24h:,}\nWrite market context analysis.")
    else:
        prompt = (f"Market: \"{label}\"\nQuestion: {question}\n"
                  f"Current probability: {current_pct:.1f}%\n"
                  f"24h change: {d24h_pp:+.1f}pp\nVol 24h: ${vol_24h:,}\n"
                  f"Write market context analysis.")

    payload = json.dumps({
        "model": "gpt-4o-mini",
        "max_tokens": 200,
        "temperature": 0.5,
        "messages": [
            {"role": "system", "content": (
                "You are a concise macro market analyst. Write a 2-3 sentence Market Context "
                "paragraph for a prediction market, like Polymarket's own website style. "
                "Focus on what drives the probability, key catalysts, and what the odds imply. "
                "Be specific with numbers. Third person. No bullets or headers."
            )},
            {"role": "user", "content": prompt}
        ]
    })

    result = subprocess.run(
        ["curl", "-s", "--max-time", "20",
         "https://api.openai.com/v1/chat/completions",
         "-H", "Content-Type: application/json",
         "-H", f"Authorization: Bearer {OPENAI_API_KEY}",
         "--data", payload],
        capture_output=True, text=True, timeout=25
    )
    try:
        return json.loads(result.stdout)["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""


def should_refresh_summary(existing_summary: dict, now_ts: str) -> bool:
    """Return True if the summary should be regenerated."""
    if not existing_summary or not existing_summary.get("text"):
        return True
    last = existing_summary.get("updated_at", "")
    if not last:
        return True
    try:
        from datetime import datetime, timezone
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        now_dt  = datetime.fromisoformat(now_ts.replace("Z", "+00:00"))
        age_min = (now_dt - last_dt).total_seconds() / 60
        return age_min >= SUMMARY_INTERVAL_MIN
    except Exception:
        return True


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

        # Find price: ONLY use market_id for exact lookup.
        # find_price_by_question (substring) is REMOVED — it causes random wrong-market
        # matching when multiple sub-markets in one event share similar question text.
        outcome   = cfg.get("outcome", "Yes")
        market_id = cfg.get("market_id")

        price = None
        if market_id:
            price = find_price_by_market_id(event, str(market_id), outcome)

        if price is None:
            if mid not in data["markets"]:
                data["markets"][mid] = {
                    "id": mid, "category": cfg.get("category",""),
                    "label": cfg.get("label",""),
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

        # Detect if market is effectively resolved (≥97% or ≤3%)
        # Still update current price, but mark as resolved and stop appending to history
        is_resolved = (price >= 0.97 or price <= 0.03)
        was_resolved = m.get("resolved", False)

        m.update({
            "id":            mid,
            "current":       price,
            "label":         cfg.get("label", m.get("label", "")),
            "sub_label":     cfg.get("sub_label"),
            "group_id":      cfg.get("group_id"),
            "category":      cfg.get("category", m.get("category", "")),
            "polymarket_url":cfg.get("polymarket_url", m.get("polymarket_url", "")),
            "vol_24h":       vol_24h,
            "vol_total":     vol_total,
            "liquidity":     liquidity,
            "resolved":      is_resolved,
        })

        if is_resolved and not was_resolved:
            # First time hitting resolution threshold — append one final point then stop
            m["history"].append({"t": now_ts, "v": price})
            print(f"  ⚑ RESOLVED {cfg.get('label','')[:45]:45s}: {price*100:.1f}%")
        elif not is_resolved:
            # Normal active market — keep appending
            m["history"].append({"t": now_ts, "v": price})
        # else: already resolved → don't append, price is stable at extreme

        if len(m["history"]) > MAX_HISTORY:
            m["history"] = m["history"][-MAX_HISTORY:]

        success_count += 1
        if not is_resolved:
            print(f"  ✓ {cfg.get('label','')[:52]:52s}: {price*100:.1f}%")

    data["last_updated"] = now_ts

    # Remove markets no longer in config (cleanup stale entries)
    active_ids = {cfg.get("id") for cfg in markets_cfg if cfg.get("id")}
    stale = [mid for mid in list(data["markets"]) if mid not in active_ids]
    for mid in stale:
        del data["markets"][mid]
    if stale:
        print(f"  Removed {len(stale)} stale markets: {stale[:5]}{'...' if len(stale)>5 else ''}")

    # Generate AI summaries (once per hour per group/single)
    if OPENAI_API_KEY:
        print("\nGenerating AI summaries...")
        # Find groups and singles
        groups = {}
        for mid, m in data["markets"].items():
            gid = m.get("group_id")
            if gid:
                groups.setdefault(gid, []).append(m)

        summarized = 0
        # Group summaries
        for gid, members in groups.items():
            existing = data["markets"][members[0]["id"]].get("summary", {})
            if not should_refresh_summary(existing, now_ts):
                continue
            scenarios = [{"sub_label": m.get("sub_label", ""), "current_pct": (m.get("current") or 0)*100}
                         for m in members if not m.get("resolved")]
            if not scenarios:
                continue
            text = generate_summary(
                label=members[0].get("label",""),
                question="", current_pct=0, d24h_pp=0,
                vol_24h=members[0].get("vol_24h",0),
                scenarios=scenarios
            )
            if text:
                summary = {"text": text, "updated_at": now_ts}
                for m in members:
                    data["markets"][m["id"]]["summary"] = summary
                summarized += 1
                print(f"  ✓ summary: {members[0].get('label','')[:50]}")

        # Single market summaries
        singles = {mid: m for mid, m in data["markets"].items() if not m.get("group_id")}
        for mid, m in singles.items():
            if m.get("resolved"):
                continue
            existing = m.get("summary", {})
            if not should_refresh_summary(existing, now_ts):
                continue
            h = m.get("history", [])
            cur = (m.get("current") or 0)
            prev = h[-48]["v"] if len(h) >= 48 else (h[0]["v"] if h else cur)
            d24 = (cur - prev) * 100
            text = generate_summary(
                label=m.get("label",""),
                question=m.get("question", m.get("label","")),
                current_pct=cur*100, d24h_pp=d24,
                vol_24h=m.get("vol_24h",0)
            )
            if text:
                m["summary"] = {"text": text, "updated_at": now_ts}
                summarized += 1
                print(f"  ✓ summary: {m.get('label','')[:50]}")

        print(f"  Summaries generated: {summarized}")

    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w") as f:
        json.dump(data, f, separators=(",", ":"))

    print(f"\n✅ Done: {success_count}/{len(markets_cfg)} updated, {len(errors)} errors")
    if errors:
        print(f"   Errors: {errors}")


if __name__ == "__main__":
    main()
