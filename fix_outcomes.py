#!/usr/bin/env python3
"""
fix_outcomes.py
───────────────
Fixes markets_config.json entries where outcome="No".
We ALWAYS want to track the Yes probability.
For each outcome=No market, find the corresponding Yes outcome token
from the event API and update both outcome and clob_token_id.

Run once after deploying the auto_discover.py fix.

Usage:
    python3 fix_outcomes.py            # preview
    python3 fix_outcomes.py --apply    # fix in place
"""
import json, os, sys, subprocess, time

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "markets_config.json")
GAMMA_API   = "https://gamma-api.polymarket.com"


def curl(url):
    r = subprocess.run(["curl","-s","--max-time","12","-H","User-Agent: Mozilla/5.0", url],
                       capture_output=True, text=True)
    try: return json.loads(r.stdout)
    except: return None


def main():
    apply = "--apply" in sys.argv

    with open(CONFIG_PATH) as f:
        cfg = json.load(f)

    markets = cfg.get("markets", [])
    no_markets = [m for m in markets if m.get("outcome") == "No"]
    print(f"Total markets: {len(markets)}")
    print(f"outcome=No (need fixing): {len(no_markets)}")

    if not apply:
        print("\nDRY RUN — add --apply to fix\n")

    # Group by event_id to minimize API calls
    by_event = {}
    for m in no_markets:
        eid = m.get("event_id")
        if eid:
            by_event.setdefault(eid, []).append(m)

    fixed = 0
    failed = 0

    for eid, mlist in by_event.items():
        event = curl(f"{GAMMA_API}/events?id={eid}")
        if not event or not isinstance(event, list):
            print(f"  FAILED to fetch event {eid}")
            failed += len(mlist)
            continue

        e = event[0]
        # Build market_id → tokens map
        token_map = {}
        for mk in e.get("markets", []):
            mid = str(mk.get("id",""))
            outcomes = mk.get("outcomes","[]")
            tokens   = mk.get("clobTokenIds","[]")
            if isinstance(outcomes, str): outcomes = json.loads(outcomes)
            if isinstance(tokens,   str): tokens   = json.loads(tokens)
            for o, t in zip(outcomes, tokens):
                token_map[(mid, o)] = t

        for m in mlist:
            mid = str(m.get("market_id",""))
            yes_token = token_map.get((mid, "Yes"))
            if yes_token:
                if apply:
                    m["outcome"] = "Yes"
                    m["clob_token_id"] = yes_token
                print(f"  {'FIXED' if apply else 'WOULD FIX'}: {m['id'][:55]}")
                fixed += 1
            else:
                print(f"  FAILED no Yes token: {m['id'][:55]}")
                failed += 1

        time.sleep(0.1)

    print(f"\n{'Fixed' if apply else 'Would fix'}: {fixed} | Failed: {failed}")

    if apply:
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, separators=(",",":"))
        print(f"✅ Saved {CONFIG_PATH}")
        print("Now run: python3 clean_history.py --apply && python3 backfill_history.py --force")


if __name__ == "__main__":
    main()
