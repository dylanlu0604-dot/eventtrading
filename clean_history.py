#!/usr/bin/env python3
"""
clean_history.py
────────────────
Removes corrupted history (wild swings from substring-matching bug) from data.json.
Markets with swings >50pp between consecutive hourly points had their prices
fetched from the wrong sub-market due to find_price_by_question substring matching.

Run ONCE after deploying the fetch_data.py fix.
Then let backfill_history.py rebuild clean history from CLOB API.

Usage:
    python3 clean_history.py            # preview only
    python3 clean_history.py --apply    # actually clean the data
"""
import json, os, sys

DATA_PATH = os.path.join(os.path.dirname(__file__), "docs", "data.json")
MAX_JUMP  = 50.0   # pp — swings larger than this are corrupted data

def has_wild_swing(history: list) -> bool:
    if len(history) < 2:
        return False
    vals = [h['v'] * 100 for h in history]
    return any(abs(vals[i] - vals[i-1]) > MAX_JUMP for i in range(1, len(vals)))

def main():
    apply = "--apply" in sys.argv

    with open(DATA_PATH) as f:
        data = json.load(f)

    markets = data.get("markets", {})
    to_clean = [mid for mid, m in markets.items() if has_wild_swing(m.get("history", []))]

    print(f"Total markets: {len(markets)}")
    print(f"Markets with >{MAX_JUMP}pp swings (corrupted): {len(to_clean)}")

    if not apply:
        print("\nDRY RUN — add --apply to actually clean\n")
        for mid in to_clean[:20]:
            m = markets[mid]
            print(f"  would clear: {mid} ({len(m['history'])} pts) — {m.get('label','')[:50]}")
        if len(to_clean) > 20:
            print(f"  ... and {len(to_clean)-20} more")
        return

    # Clear only the history (keep current price and metadata)
    for mid in to_clean:
        m = markets[mid]
        old_pts = len(m.get("history", []))
        m["history"] = []
        m.pop("resolved", None)   # reset resolved flag too
        print(f"  Cleared: {mid} ({old_pts} pts) → 0 pts")

    with open(DATA_PATH, "w") as f:
        json.dump(data, f, separators=(",", ":"))

    print(f"\n✅ Cleaned {len(to_clean)} markets. Run backfill_history.py --force to rebuild.")

if __name__ == "__main__":
    main()
