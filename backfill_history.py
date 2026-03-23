#!/usr/bin/env python3
"""
backfill_history.py
-------------------
One-time script to backfill historical probability data from Polymarket's
CLOB price-history API into docs/data.json.

The CLOB API provides ~30-second granularity price history going back
6+ months for active markets. We downsample to ~30-min intervals to
keep data.json lean and consistent with the live fetch cadence.

Usage:
    python3 backfill_history.py            # default: 365 days back
    python3 backfill_history.py --days 90  # 90 days back
"""

import json
import subprocess
import os
import sys
import time
from datetime import datetime, timezone

# ── Token IDs (clobTokenId of "Yes" outcome for each market) ──────────────────
# These are the Polymarket CLOB token IDs for the "Yes" outcome of each market.
# To find new token IDs: gamma-api.polymarket.com/events?id={event_id}
#   → markets[].clobTokenIds[0]  (index 0 = Yes outcome)

CLOB_TOKENS = {
    # Iran / Geopolitical
    "us_invades_iran":       "55115078421062885512539156303747803058407616201213034911037320915726138659123",
    "iranian_regime_falls":  "10991849228756847439673778874175365458450913336396982752046655649803657501964",
    "iran_nuke":             "55302250828823180276187438149698246226978412434312478767847394628702439947936",
    "iran_nuclear_test":     "101371628741119370156433108829627306392734152017027640384155257750266453353351",
    "iran_npt_withdrawal":   "16256817581852682535056260402161151761226188836712828144692776104914940304066",

    # Fed / Rates
    "fed_april_hold":        "63586620628756015058616403521099137018911742768824051367331188904593189743777",
    "fed_april_hike":        "9556122149160720922715284597610520228366807023831966638741974320131898296289",
    "fed_june_hold":         "30767812841387255642892182147223249245545002662653079696958384408588201824258",
    "fed_june_cut25":        "65193234666628291664907888364936366210889305490897648116746073820519263548476",
    "fed_2026_zero_cuts":    "12403602920039269077597917340921667997547115084613238528792639013246536343316",
    "fed_rate_hike_2026":    "75028752776148090296091099469912621384650554615761384992997579209329182670110",
    "fed_emergency_cut":     "8618184031231342643840589970076443003283607991865226846156174312081261691762",

    # Economy / Macro
    "us_recession_2026":     "100379208559626151022751801118534484742123694725746262280150222742563282755057",
    "inflation_above_4pct":  "77809380206760496358055332023206786694120788116551923604170177964424180121482",
    "inflation_above_5pct":  "72304052873611261335260464672644781804557218671425470569977240802446584805659",
    "negative_gdp_2026":     "45429668890056162364307617807576387840293305143387096943585488315586563658159",
    "debt_downgrade":        "66284977163262204624598450319968559820692964588074113638404834566884517814525",
    "nyse_circuit_breaker":  "10776578084439550181526173165818979034936800182797800156144227188229495479910",

    # Markets / Assets
    "gold_best_2026":        "4655209727295513535859117642120184432334179761721646415109648555849060899062",
    "sp500_q1_negative":     "113344914461305174938808506971245553469172066809351271365115504384505953576193",
    "sp500_close_below_6000":"109916013757824662007601798440979979574910555306434264115363209395319896397047",
    "crude_above_90_june":   "92199334246706617385520963092185406980809392398010215995995163419083393097176",
    "gold_hit_6000_june":    "99624346890619002372699754069143706837879948305658347182463693771839092913168",
}

CLOB_API = "https://clob.polymarket.com/prices-history"
DATA_PATH = os.path.join(os.path.dirname(__file__), "docs", "data.json")

# Downsample: keep one point every N minutes from the raw CLOB data
DOWNSAMPLE_MINUTES = 30


def fetch_price_history(token_id: str, start_ts: int) -> list[dict]:
    """Fetch price history from CLOB API. Returns list of {t, p}."""
    url = f"{CLOB_API}?market={token_id}&startTs={start_ts}&fidelity=60"
    cmd = ["curl", "-s", "--max-time", "20",
           "-H", "User-Agent: Mozilla/5.0",
           url]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
        return data.get("history", [])
    except json.JSONDecodeError:
        return []


def downsample(points: list[dict], interval_minutes: int = 30) -> list[dict]:
    """
    Downsample raw CLOB history (every ~30s) to ~30-min intervals.
    Keeps the last price within each bucket.
    """
    if not points:
        return []
    interval_sec = interval_minutes * 60
    buckets = {}
    for p in points:
        bucket = (p["t"] // interval_sec) * interval_sec
        buckets[bucket] = p["p"]   # last-write-wins within bucket

    return [
        {"t": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "v": round(price, 4)}
        for ts, price in sorted(buckets.items())
    ]


def merge_history(existing: list[dict], new: list[dict]) -> list[dict]:
    """
    Merge existing history (from data.json) with backfilled history.
    - new historical points go first
    - existing points are preserved (they may have higher precision)
    - deduplicate by timestamp, preferring existing data
    """
    seen = {h["t"] for h in existing}
    merged = [h for h in new if h["t"] not in seen]
    merged.extend(existing)
    merged.sort(key=lambda h: h["t"])
    return merged


def main():
    # Parse --days argument
    days = 365
    if "--days" in sys.argv:
        idx = sys.argv.index("--days")
        try:
            days = int(sys.argv[idx + 1])
        except (IndexError, ValueError):
            pass

    start_ts = int(time.time()) - days * 86400
    print(f"Backfilling {days} days of history (since {datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d')})")
    print(f"Downsampling to {DOWNSAMPLE_MINUTES}-min intervals\n")

    # Load existing data.json
    if not os.path.exists(DATA_PATH):
        print(f"ERROR: {DATA_PATH} not found. Run fetch_data.py first.")
        sys.exit(1)

    with open(DATA_PATH, "r") as f:
        data = json.load(f)

    markets = data.get("markets", {})
    updated = 0

    for market_id, token_id in CLOB_TOKENS.items():
        if market_id not in markets:
            print(f"  SKIP {market_id} — not in data.json (run fetch_data.py first)")
            continue

        print(f"  [{market_id}]", end=" ", flush=True)

        raw = fetch_price_history(token_id, start_ts)
        if not raw:
            print("⚠ no data")
            continue

        downsampled = downsample(raw, DOWNSAMPLE_MINUTES)
        existing = markets[market_id].get("history", [])
        merged = merge_history(existing, downsampled)

        markets[market_id]["history"] = merged
        updated += 1

        first_date = downsampled[0]["t"][:10] if downsampled else "?"
        print(f"✓  {len(raw)} raw → {len(downsampled)} pts (from {first_date}), merged={len(merged)}")

    data["markets"] = markets

    with open(DATA_PATH, "w") as f:
        json.dump(data, f, separators=(",", ":"))

    print(f"\n✅ Backfill complete: {updated}/{len(CLOB_TOKENS)} markets updated → {DATA_PATH}")


if __name__ == "__main__":
    main()
