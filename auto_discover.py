#!/usr/bin/env python3
"""
auto_discover.py
────────────────
Automatically discovers and classifies Polymarket events into 4 categories
using keyword pre-filtering + Claude API for smart selection.

Outputs:  markets_config.json   (read by fetch_data.py)

Categories:
  iran_war           – Iran conflict, military, nuclear, regime change
  interest_rates     – Fed decisions, rate cuts/hikes, monetary policy
  economy_inflation  – Recession, inflation, CPI, GDP, unemployment, debt
  markets_assets     – S&P 500, Gold, Crude Oil, Nasdaq, futures prices

Usage:
  python3 auto_discover.py                     # discover + classify
  python3 auto_discover.py --dry-run           # print without saving
  python3 auto_discover.py --min-volume 5000   # set min total volume ($)
"""

import json
import os
import sys
import re
import subprocess
import time
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
GAMMA_API       = "https://gamma-api.polymarket.com"
CLOB_API        = "https://clob.polymarket.com"
CONFIG_PATH     = os.path.join(os.path.dirname(__file__), "markets_config.json")
MAX_SCAN_EVENTS = 3000      # scan up to this many events
SCAN_PAGE_SIZE  = 100
MIN_VOLUME      = 1_000      # skip events with less than $1K total volume
MAX_MARKETS_PER_CAT = 40    # max markets to track per category

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL   = "gpt-4o-mini"   # cheapest OpenAI model with good JSON output

# ── Keyword pre-filter ────────────────────────────────────────────────────────
# Quick client-side filter before sending to Claude. Broad intentionally.
KEYWORD_MAP = {
    "iran_war": [
        "iran", "iranian", "hormuz", "tehran", "irgc", "khamenei",
        "nuclear deal", "npt", "pahlavi", "persian gulf", "isfahan",
        "enrichment of uranium", "war on iran", "strike iran",
        "ceasefire.*iran", "iran.*ceasefire", "us.*iran", "iran.*us",
        "israel.*iran", "iran.*israel", "reza pahlavi", "iran coup",
        "iran regime", "iran nuke", "iran nuclear", "iran sanction",
        "iran oil", "iran election", "iran internet", "iran kurds",
    ],
    "interest_rates": [
        "fed ", "fomc", "rate cut", "rate hike", "interest rate",
        "federal reserve", "fed decision", "fed rate", "emergency cut",
        "basis point", "fed chair", "monetary policy", "ecb interest",
        "bank of england rate", "pboc rate", "people's bank of china rate",
        "powell", "warsh", "fed abolish", "credit card interest",
        "ecb rate", "boe rate", "bank of japan", "boj rate",
        "quantitative easing", "quantitative tightening",
    ],
    "economy_inflation": [
        "recession", "inflation", " cpi", "gdp growth", "gdp ",
        "unemployment", "debt downgrade", "circuit breaker",
        "national debt", "stagflation", "annual inflation",
        "annual gdp", "world gdp", "tariff", "trade war",
        "deficit", "debt ceiling", "credit rating", "downgrade",
        "nfp", "payroll", "job", "consumer price", "pce",
        "core inflation", "hyperinflation",
    ],
    "markets_assets": [
        "s&p 500", "(spx)", "gold (gc)", "crude oil (cl)", "wti",
        "gold vs", "bitcoin vs. gold", "nasdaq 100", "(ndx)",
        "gold futures", "oil futures", "gold hit", "crude oil hit",
        "gold above", "oil above", "s&p.*hit", "spx.*hit",
        "dow jones", "vix ", "russell 2000", "brent crude",
        "natural gas", "copper price", "silver price",
        "stock market", "bear market", "bull market",
        "market crash", "market high", "all time high",
        "settle at", "close at in", "close at end",
        "gold.*settle", "crude.*settle", "spx.*close",
    ],
}

CATEGORY_LABELS = {
    "iran_war":          "Iran & Geopolitical Risk",
    "interest_rates":    "Fed & Interest Rates",
    "economy_inflation": "Economy & Inflation",
    "markets_assets":    "Markets & Assets",
}

CATEGORY_ICONS = {
    "iran_war":          "🚨",
    "interest_rates":    "🏦",
    "economy_inflation": "📊",
    "markets_assets":    "📈",
}

CATEGORY_COLORS = {
    "iran_war":          "red",
    "interest_rates":    "blue",
    "economy_inflation": "amber",
    "markets_assets":    "green",
}


# ── HTTP helper ────────────────────────────────────────────────────────────────
def curl(url: str, timeout: int = 15) -> dict | list | None:
    result = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout),
         "-H", "User-Agent: Mozilla/5.0", url],
        capture_output=True, text=True
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


# ── Step 1: Scan all active events ────────────────────────────────────────────
def scan_events(max_events: int = MAX_SCAN_EVENTS) -> list[dict]:
    print(f"[1/4] Scanning Polymarket events (up to {max_events})...")
    all_events = []
    for offset in range(0, max_events, SCAN_PAGE_SIZE):
        url = f"{GAMMA_API}/events?active=true&closed=false&limit={SCAN_PAGE_SIZE}&offset={offset}"
        data = curl(url, timeout=12)
        if not data:
            print(f"  offset={offset}: empty response, stopping")
            break
        all_events.extend(data)
        if len(data) < SCAN_PAGE_SIZE:
            break   # last page
        if offset % 500 == 0 and offset > 0:
            print(f"  ... scanned {offset} events")

    # deduplicate by id
    seen, unique = set(), []
    for e in all_events:
        if e['id'] not in seen:
            seen.add(e['id'])
            unique.append(e)

    print(f"  Total unique events: {len(unique)}")
    return unique


# ── Step 2: Keyword pre-filter ─────────────────────────────────────────────────
def keyword_filter(events: list[dict]) -> list[dict]:
    import re
    print(f"[2/4] Keyword filtering...")
    matched = []
    for e in events:
        vol = float(e.get('volume') or 0)
        if vol < MIN_VOLUME:
            continue
        title = e.get('title', '').lower()
        for cat, kws in KEYWORD_MAP.items():
            if any(re.search(kw, title) for kw in kws):
                e['_kw_cat'] = cat
                matched.append(e)
                break

    matched.sort(key=lambda e: float(e.get('volume') or 0), reverse=True)
    print(f"  Keyword-matched: {len(matched)} events (min vol ${MIN_VOLUME:,})")
    for cat in KEYWORD_MAP:
        n = sum(1 for e in matched if e.get('_kw_cat') == cat)
        print(f"    {cat}: {n}")
    return matched


# ── Step 3: Fetch market details (markets + clobTokenIds) ─────────────────────
def fetch_market_details(events: list[dict]) -> list[dict]:
    """Fetch full event details including markets[] with clobTokenIds."""
    print(f"[3/4] Fetching market details for {len(events)} events...")
    enriched = []
    for i, e in enumerate(events):
        eid = e['id']
        data = curl(f"{GAMMA_API}/events?id={eid}", timeout=10)
        if data and isinstance(data, list) and data:
            full = data[0]
            full['_kw_cat'] = e.get('_kw_cat')
            enriched.append(full)
        else:
            e['markets'] = e.get('markets', [])
            enriched.append(e)
        if (i + 1) % 20 == 0:
            print(f"  ... {i+1}/{len(events)}")
        time.sleep(0.05)  # be polite

    print(f"  Fetched details for {len(enriched)} events")
    return enriched


# ── Step 4: OpenAI classification ─────────────────────────────────────────────
def claude_classify(events: list[dict]) -> list[dict]:
    """
    Use OpenAI gpt-4o-mini to:
    1. Confirm/correct the category
    2. Select the single best market question to track within each event
    3. Identify the outcome to track (Yes/No)
    """
    if not OPENAI_API_KEY:
        print("[4/4] WARNING: No OPENAI_API_KEY — using keyword classification only")
        return keyword_fallback(events)

    print(f"[4/4] OpenAI classification ({len(events)} events in batches of 25)...")
    results = []

    BATCH = 25
    for batch_start in range(0, len(events), BATCH):
        batch = events[batch_start:batch_start + BATCH]

        items = []
        for e in batch:
            market_questions = []
            for m in e.get('markets', [])[:6]:
                outcomes = m.get('outcomes', '[]')
                prices   = m.get('outcomePrices', '[]')
                if isinstance(outcomes, str): outcomes = json.loads(outcomes)
                if isinstance(prices, str):   prices   = json.loads(prices)
                market_questions.append({
                    "id":       m.get('id'),
                    "question": m.get('question', '')[:120],
                    "outcomes": outcomes[:4],
                    "prices":   [f"{float(p)*100:.1f}%" for p in prices[:4]],
                })
            items.append({
                "event_id":    e['id'],
                "title":       e.get('title', ''),
                "volume_usd":  round(float(e.get('volume') or 0)),
                "vol_24h_usd": round(float(e.get('volume24hr') or 0)),
                "kw_category": e.get('_kw_cat'),
                "markets":     market_questions,
            })

        system_prompt = (
            "You are a financial market analyst. Classify Polymarket prediction market events. "
            "Respond ONLY with a valid JSON array, no markdown, no explanation."
        )

        user_prompt = f"""Classify each event into one of these categories, or null if none fit:
- "iran_war":          Iran conflict, military strikes, nuclear program, regime change, ceasefire, sanctions
- "interest_rates":    Fed/FOMC decisions, rate cuts/hikes, Fed Chair, monetary policy, ECB/BOE/PBOC rates
- "economy_inflation": Recession, inflation/CPI, GDP growth, unemployment, debt downgrade, circuit breakers
- "markets_assets":    S&P 500/SPX levels, Gold/GC futures, Crude Oil/WTI/CL futures, Nasdaq/NDX

For each event return:
1. "category": one of the 4 above, or null
2. "market_id": ID of the single most informative market to track (prefer binary Yes/No on meaningful thresholds)
3. "outcome": "Yes" or "No"
4. "reason": max 10 words

Events:
{json.dumps(items, indent=2)}

Return ONLY a JSON array, one object per event, same order:
[{{"event_id":"...","category":"...","market_id":"...","outcome":"Yes","reason":"..."}}]"""

        payload = {
            "model":       OPENAI_MODEL,
            "max_tokens":  2000,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
        }

        cmd = [
            "curl", "-s", "--max-time", "45",
            "https://api.openai.com/v1/chat/completions",
            "-H", "Content-Type: application/json",
            "-H", f"Authorization: Bearer {OPENAI_API_KEY}",
            "--data", json.dumps(payload),
        ]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=50).stdout

        classifications = []
        try:
            resp = json.loads(out)
            text = resp["choices"][0]["message"]["content"].strip()
            # Strip markdown fences if present
            if text.startswith("```"):
                text = "\n".join(text.split("\n")[1:])
                text = text.rstrip("`").strip()
            classifications = json.loads(text)
        except Exception as ex:
            print(f"  WARNING: OpenAI parse error batch {batch_start}: {ex}")
            print(f"  Raw: {out[:300]}")

        cls_map = {str(c['event_id']): c for c in classifications if isinstance(c, dict)}

        for e in batch:
            eid = str(e['id'])
            cls = cls_map.get(eid)
            if cls and cls.get('category') and cls.get('market_id'):
                items_out = build_result(e, cls)
            else:
                items_out = keyword_fallback_single(e)
            results.extend(items_out)

        print(f"  Batch {batch_start//BATCH + 1}: {len(cls_map)} classified")
        time.sleep(0.3)

    print(f"  Total classified: {len(results)}")
    return results


def keyword_fallback(events: list[dict]) -> list[dict]:
    results = []
    for e in events:
        results.extend(keyword_fallback_single(e))
    return results


def keyword_fallback_single(e: dict) -> dict | None:
    cat = e.get('_kw_cat')
    if not cat:
        return None
    markets = e.get('markets', [])
    if not markets:
        return None

    # Pick first Yes/No market
    for m in markets:
        outcomes = m.get('outcomes', '[]')
        if isinstance(outcomes, str): outcomes = json.loads(outcomes)
        if 'Yes' in outcomes:
            return _make_result(e, cat, m, 'Yes')
    return None


DATE_RE = re.compile(
    r'\b(january|february|march|april|may|june|july|august|september|october|november|december'
    r'|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\b[\s\w,]*?\d{1,2}(?:,\s*\d{4})?'
    r'|\bQ[1-4]\s+\d{4}\b'
    r'|\bend of (march|june|september|december|2025|2026|2027)\b',
    re.IGNORECASE
)
PRICE_VALUE_RE = re.compile(r'\$[\d,]+(?:\.\d+)?[KMBk]?')
BPS_NUM_RE   = re.compile(r'\d+\s*\+?\s*bps|\d+\s*basis\s*point', re.IGNORECASE)
BPS_ACTION_RE= re.compile(r'\bno change\b|\bhold\b|\bhike\b|\bcut\b|\bunchanged\b|'
                           r'decrease.*by\s+\d+|increase.*by\s+\d+', re.IGNORECASE)


def _parse_list(v):
    if isinstance(v, list): return v
    try: return json.loads(v)
    except Exception: return []


def _common_prefix_len(strings: list[str]) -> int:
    if not strings: return 0
    prefix = strings[0]
    for s in strings[1:]:
        while not s.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix: return 0
    return len(prefix)


def _common_suffix_len(strings: list[str]) -> int:
    return _common_prefix_len([s[::-1] for s in strings])


def _extract_diff(q: str, prefix_len: int, suffix_len: int) -> str:
    """Extract the unique middle part of a question."""
    end = len(q) - suffix_len if suffix_len > 0 else len(q)
    diff = q[prefix_len:end].strip(' ?.,()- \t')
    return diff


def smart_label_fn(questions: list[str]):
    """Build a per-question label function that extracts what makes each unique."""
    if len(questions) < 2:
        return lambda q: q[:25]

    prefix_len = _common_prefix_len(questions)
    suffix_len  = _common_suffix_len(questions)

    def label_fn(q: str) -> str:
        diff = _extract_diff(q, prefix_len, suffix_len)

        # 1. Numeric bps — search full question (suffix may have consumed the trailing 's' of 'bps')
        bm = BPS_NUM_RE.search(q)
        if bm:
            return bm.group(0).strip()[:15]

        # 2. Price value — search full question
        pm = PRICE_VALUE_RE.search(q)
        if pm:
            q_l = q.lower()
            qual = (' H' if '(high)' in q_l else
                    ' L' if '(low)'  in q_l else
                    '+' if 'above'  in q_l else
                    '-' if 'below'  in q_l else '')
            return pm.group(0) + qual

        # 3. Date in DIFF only (avoids matching common-suffix date like "June 2026")
        dm = DATE_RE.search(diff)
        if dm:
            raw = dm.group(0).strip()
            parts = raw.split()
            if len(parts) >= 2:
                mon = parts[0][:3].capitalize()
                day = re.search(r'\d+', parts[1])
                if day:
                    return f"{mon} {day.group()}"
            return raw[:12]

        # 4. Action keyword in DIFF ("no change", "hold", "hike")
        am = BPS_ACTION_RE.search(diff)
        if am:
            return am.group(0).strip()[:20]

        # 5. Diff text (country name, entity, etc.)
        if diff and len(diff) >= 2:
            return diff[:25]

        # 6. Last resort
        tokens = [t for t in q.split() if len(t) > 2]
        return tokens[0][:20] if tokens else q[:15]

    return label_fn


def is_neg_risk(e: dict) -> bool:
    """True if this is a negRisk (mutually exclusive range) event."""
    return bool(e.get('negRisk'))


def neg_risk_label(market: dict) -> str | None:
    """Use groupItemTitle directly for negRisk range markets."""
    return market.get('groupItemTitle') or None


def is_multi_series(markets: list[dict]) -> bool:
    """True if the event has ≥2 Yes/No sub-markets that are meaningfully different."""
    yes_no = [m for m in markets
              if 'Yes' in _parse_list(m.get('outcomes', '[]'))]
    if len(yes_no) < 2:
        return False

    questions = [m.get('question', '') for m in yes_no]
    prefix_len = _common_prefix_len(questions)
    suffix_len  = _common_suffix_len(questions)

    if prefix_len < 8 and suffix_len < 8:
        return False

    diffs = [_extract_diff(q, prefix_len, suffix_len) for q in questions]
    meaningful = [d for d in diffs if len(d) >= 2]
    return len(meaningful) >= 2


def build_result(e: dict, cls: dict) -> list[dict]:
    cat     = cls.get('category')
    outcome = cls.get('outcome', 'Yes')
    mid     = str(cls.get('market_id', ''))
    markets = e.get('markets', [])

    # ── negRisk (mutually exclusive ranges): use groupItemTitle directly ──
    if is_neg_risk(e):
        active = [m for m in markets if not _is_effectively_resolved(m)]
        if len(active) < 2:
            active = markets
        multi = _make_multi_from(active, e, cat, outcome,
                                  label_fn=lambda m_dict: neg_risk_label(m_dict)
                                  if isinstance(m_dict, dict)
                                  else neg_risk_label({'groupItemTitle': None}))
        # _make_multi_from passes question string to label_fn, but we need the market dict
        # Use a dedicated helper instead:
        multi = _make_multi_neg_risk(active, e, cat, outcome)
        if multi and len(multi) >= 2:
            return multi

    # ── standard multi-series ──
    if is_multi_series(markets):
        active_markets = [m for m in markets if not _is_effectively_resolved(m)]
        if len(active_markets) < 2:
            active_markets = markets

        yes_no    = [m for m in active_markets if outcome in _parse_list(m.get('outcomes', '[]'))]
        questions = [m.get('question', '') for m in yes_no]
        if len(questions) >= 2:
            lbl_fn = smart_label_fn(questions)
            multi  = _make_multi_from(active_markets, e, cat, outcome, lbl_fn)
            if multi and len(multi) >= 2:
                return multi

    # Single-market fallback — pick the best (non-resolved) market
    best = _pick_best_market(markets, outcome)
    if not best:
        best = next((m for m in markets if str(m.get('id')) == mid), None)
    if not best and markets:
        best = markets[0]
    if not best:
        return []
    return [_make_single(e, cat, best, outcome)]


def _make_multi_neg_risk(markets_list: list[dict], e: dict, cat: str, outcome: str) -> list[dict]:
    """Build multi-series for negRisk (mutually exclusive range) events.
    Uses groupItemTitle as the label — clean, authoritative range labels from Polymarket.
    """
    group_id = _make_id(e.get('title', ''))
    slug     = e.get('slug') or e.get('ticker') or ''
    results  = []

    for m in markets_list:
        outcomes = _parse_list(m.get('outcomes', '[]'))
        tokens   = _parse_list(m.get('clobTokenIds', '[]'))
        if outcome not in outcomes:
            continue

        sub_label = m.get('groupItemTitle') or None
        if not sub_label:
            # Fallback: extract from question
            q = m.get('question', '')
            sub_label = _extract_range_label(q) or smart_label_fn([q])(q)
        if not sub_label:
            continue

        token_id = None
        for o, t in zip(outcomes, tokens):
            if o == outcome:
                token_id = t
                break

        sub_id = (group_id + '_' + _make_id(sub_label))[:60]
        results.append({
            "id":            sub_id,
            "group_id":      group_id,
            "event_id":      e['id'],
            "category":      cat,
            "label":         e.get('title', ''),
            "sub_label":     sub_label,
            "question":      m.get('question', ''),
            "market_id":     m.get('id'),
            "outcome":       outcome,
            "clob_token_id": token_id,
            "polymarket_url":f"https://polymarket.com/event/{slug}",
            "volume":        round(float(e.get('volume') or 0)),
            "volume_24h":    round(float(e.get('volume24hr') or 0)),
            "liquidity":     round(float(e.get('liquidity') or 0)),
        })

    # Sort by the range label (< first, then ascending, > last)
    def sort_key(r):
        lbl = r['sub_label'] or ''
        if lbl.startswith('<'): return (-1, 0)
        if lbl.startswith('>'): return (999999, 0)
        m = re.search(r'\$?([\d,]+)', lbl)
        if m:
            return (int(m.group(1).replace(',', '')), 0)
        return (0, lbl)
    results.sort(key=sort_key)

    return results if len(results) >= 2 else []


def _extract_range_label(question: str) -> str | None:
    """Extract a range label like '$6,400-$6,500' or '<$6,400' or '>$7,300' from a question."""
    # Range: $X,XXX-$Y,YYY
    m = re.search(r'[<>]?\$[\d,]+(?:\s*-\s*\$[\d,]+)?', question)
    if m:
        return m.group(0).strip()
    return None
    best = _pick_best_market(markets, outcome)
    if not best:
        # last resort: market_id from GPT
        best = next((m for m in markets if str(m.get('id')) == mid), None)
    if not best and markets:
        best = markets[0]
    if not best:
        return []
    return [_make_single(e, cat, best, outcome)]


def _make_multi_from(markets_list: list[dict], e: dict, cat: str,
                     outcome: str, label_fn) -> list[dict]:
    """Same as _make_multi but takes a pre-filtered markets list."""
    group_id = _make_id(e.get('title', ''))
    slug     = e.get('slug') or e.get('ticker') or ''
    results  = []
    for m in markets_list:
        outcomes = _parse_list(m.get('outcomes', '[]'))
        tokens   = _parse_list(m.get('clobTokenIds', '[]'))
        if outcome not in outcomes:
            continue
        q         = m.get('question', '')
        sub_label = label_fn(q)
        if not sub_label:
            continue
        token_id = None
        for o, t in zip(outcomes, tokens):
            if o == outcome:
                token_id = t
                break
        sub_id = (group_id + '_' + _make_id(sub_label))[:60]
        results.append({
            "id":            sub_id,
            "group_id":      group_id,
            "event_id":      e['id'],
            "category":      cat,
            "label":         e.get('title', ''),
            "sub_label":     sub_label,
            "question":      q,
            "market_id":     m.get('id'),
            "outcome":       outcome,
            "clob_token_id": token_id,
            "polymarket_url":f"https://polymarket.com/event/{slug}",
            "volume":        round(float(e.get('volume') or 0)),
            "volume_24h":    round(float(e.get('volume24hr') or 0)),
            "liquidity":     round(float(e.get('liquidity') or 0)),
        })
    return results if len(results) >= 2 else []


def _make_multi(e: dict, cat: str, outcome: str, label_fn=None) -> list[dict]:
    """Legacy wrapper — used by keyword_fallback_single."""
    if label_fn is None:
        label_fn = lambda q: q[:25]
    return _make_multi_from(e.get('markets', []), e, cat, outcome, label_fn)


def _is_effectively_resolved(market: dict) -> bool:
    """Return True if a market is effectively resolved (≥95% or ≤5%)."""
    prices = market.get('outcomePrices', '[]')
    if isinstance(prices, str):
        try: prices = json.loads(prices)
        except: return False
    outcomes = market.get('outcomes', '[]')
    if isinstance(outcomes, str):
        try: outcomes = json.loads(outcomes)
        except: return False
    for o, p in zip(outcomes, prices):
        if o in ('Yes', 'No'):
            try:
                v = float(p)
                if v >= 0.95 or v <= 0.05:
                    return True
            except: pass
    return False


def _pick_best_market(markets: list[dict], outcome: str = 'Yes') -> dict | None:
    """Pick the most informative single market from an event's market list.
    Prefers markets that:
    1. Are NOT effectively resolved (≥99% or ≤1%)
    2. Have the highest liquidity / volume
    3. Have the target outcome available
    """
    candidates = []
    for m in markets:
        outcomes = _parse_list(m.get('outcomes', '[]'))
        if outcome not in outcomes:
            continue
        if _is_effectively_resolved(m):
            continue
        prices = _parse_list(m.get('outcomePrices', '[]'))
        try:
            idx = outcomes.index(outcome)
            prob = float(prices[idx])
        except (ValueError, IndexError):
            prob = 0.5
        # Score: prefer markets near 50% (most uncertain/interesting)
        # but any non-resolved market is better than a resolved one
        uncertainty = 1 - abs(prob - 0.5) * 2  # 1.0 at 50%, 0.0 at 0% or 100%
        candidates.append((uncertainty, m))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    # Fall back to any market with the target outcome (even if resolved)
    for m in markets:
        outcomes = _parse_list(m.get('outcomes', '[]'))
        if outcome in outcomes:
            return m
    return None


def _make_single(e: dict, cat: str, market: dict, outcome: str) -> dict:
    tokens   = _parse_list(market.get('clobTokenIds', '[]'))
    outcomes = _parse_list(market.get('outcomes', '[]'))

    token_id = None
    for o, t in zip(outcomes, tokens):
        if o == outcome:
            token_id = t
            break

    slug = e.get('slug') or e.get('ticker') or ''
    return {
        "id":            _make_id(e.get('title', '')),
        "group_id":      None,
        "event_id":      e['id'],
        "category":      cat,
        "label":         e.get('title', ''),
        "sub_label":     None,
        "question":      market.get('question', ''),
        "market_id":     market.get('id'),
        "outcome":       outcome,
        "clob_token_id": token_id,
        "polymarket_url":f"https://polymarket.com/event/{slug}",
        "volume":        round(float(e.get('volume') or 0)),
        "volume_24h":    round(float(e.get('volume24hr') or 0)),
        "liquidity":     round(float(e.get('liquidity') or 0)),
    }


def _extract_date_label(question: str) -> str | None:
    """
    Pull a clean 'Month DD' or 'Month DD, YYYY' label from a question.
    Returns None if no clean date found — caller must handle this.
    Never falls back to question text fragments.
    """
    # Match "March 31" / "April 15, 2026" — day must be 1-31, not a year
    m = re.search(
        r'\b(january|february|march|april|may|june|july|august|september|october|november|december)'
        r'\s+(3[01]|[12]\d|0?[1-9])(?!\d)(?:,?\s*(\d{4}))?',
        question, re.IGNORECASE
    )
    if m:
        month = m.group(1).capitalize()[:3]   # "Mar"
        day   = m.group(2).lstrip('0') or '0' # "31", "5"
        year  = m.group(3)
        if year and year not in ('2026', '2025'):
            return f"{month} {day} {year}"
        return f"{month} {day}"

    # Match "Q1 2026", "end of 2026"
    m2 = re.search(r'\b(Q[1-4]\s*\d{4}|end of \d{4})\b', question, re.IGNORECASE)
    if m2:
        return m2.group(1)

    return None  # No clean date found


def _make_id(title: str) -> str:
    s = title.lower()
    s = re.sub(r'[^a-z0-9 ]', '', s)
    s = re.sub(r'\s+', '_', s.strip())
    return s[:50]


def keyword_fallback_single(e: dict) -> list[dict]:
    cat = e.get('_kw_cat')
    if not cat:
        return []
    markets = e.get('markets', [])
    if not markets:
        return []

    # negRisk first
    if is_neg_risk(e):
        active = [m for m in markets if not _is_effectively_resolved(m)]
        if len(active) < 2: active = markets
        multi = _make_multi_neg_risk(active, e, cat, 'Yes')
        if multi: return multi

    active = [m for m in markets if not _is_effectively_resolved(m)]
    if len(active) < 2:
        active = markets

    if is_multi_series(active):
        yes_no = [m for m in active if 'Yes' in _parse_list(m.get('outcomes', '[]'))]
        questions = [m.get('question', '') for m in yes_no]
        if len(questions) >= 2:
            multi = _make_multi_from(active, e, cat, 'Yes', smart_label_fn(questions))
            if multi: return multi

    best = _pick_best_market(markets, 'Yes')
    if best:
        return [_make_single(e, cat, best, 'Yes')]
    return []


# ── Step 5: Deduplicate + rank + limit ─────────────────────────────────────────
def deduplicate(results: list[dict]) -> list[dict]:
    from collections import defaultdict

    # Deduplicate singles by question; keep all sub-markets of a group together
    seen_questions = set()
    seen_groups    = set()
    deduped        = []

    for r in sorted(results, key=lambda x: x.get('volume', 0), reverse=True):
        gid = r.get('group_id')
        if gid:
            # Include every sub-market of a group (dedupe by group, not question)
            q_key = (gid, r.get('market_id', ''))
            if q_key in seen_questions:
                continue
            seen_questions.add(q_key)
            deduped.append(r)
        else:
            q_key = r.get('question', '')[:60].lower().strip()
            if q_key in seen_questions:
                continue
            seen_questions.add(q_key)
            deduped.append(r)

    # Filter invalid categories
    deduped = [r for r in deduped
               if r.get('category') and r['category'] != 'null'
               and r['category'] in KEYWORD_MAP]

    # Cap per category — count groups as one slot
    by_cat = defaultdict(list)
    for r in deduped:
        by_cat[r['category']].append(r)

    final = []
    for cat, items in by_cat.items():
        # Sort: groups first (by volume), then singles (by volume)
        groups  = {}
        singles = []
        for r in items:
            gid = r.get('group_id')
            if gid:
                if gid not in groups:
                    groups[gid] = []
                groups[gid].append(r)
            else:
                singles.append(r)

        # Sort groups by volume, singles by volume
        sorted_groups  = sorted(groups.values(),  key=lambda g: g[0].get('volume', 0), reverse=True)
        sorted_singles = sorted(singles, key=lambda x: x.get('volume', 0), reverse=True)

        # Interleave: add groups (all sub-markets) then singles up to cap
        slots = 0
        for grp in sorted_groups:
            if slots >= MAX_MARKETS_PER_CAT:
                break
            final.extend(grp)
            slots += 1   # a group counts as 1 slot

        for s in sorted_singles:
            if slots >= MAX_MARKETS_PER_CAT:
                break
            final.append(s)
            slots += 1

    final.sort(key=lambda x: (
        list(KEYWORD_MAP.keys()).index(x['category']),
        x.get('group_id') or '',
        -x.get('volume', 0)
    ))

    print(f"\nFinal market count: {len(final)}")
    for cat in KEYWORD_MAP:
        n = sum(1 for r in final if r['category'] == cat)
        print(f"  {cat}: {n}")

    return final


# ── Save config ────────────────────────────────────────────────────────────────
def save_config(markets: list[dict], dry_run: bool = False):
    categories = {
        cat: {
            "label": CATEGORY_LABELS[cat],
            "icon":  CATEGORY_ICONS[cat],
            "color": CATEGORY_COLORS[cat],
        }
        for cat in KEYWORD_MAP
    }

    config = {
        "last_discovered": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "categories": categories,
        "markets": markets,
    }

    if dry_run:
        print("\n── DRY RUN: markets_config.json would contain ──")
        for m in markets:
            print(f"  [{m['category']}] {m['label'][:70]}")
            print(f"    Q: {m['question'][:70]}")
            print(f"    outcome={m['outcome']} vol=${m['volume']:,} token={str(m.get('clob_token_id',''))[:20]}...")
        return

    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)
    print(f"\n✅ Saved {len(markets)} markets to {CONFIG_PATH}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    dry_run = "--dry-run" in sys.argv

    # Parse --min-volume flag
    global MIN_VOLUME
    if "--min-volume" in sys.argv:
        idx = sys.argv.index("--min-volume")
        try:
            MIN_VOLUME = int(sys.argv[idx + 1])
        except (IndexError, ValueError):
            pass

    print("=" * 60)
    print("Polymarket Auto-Discovery")
    print(f"Min volume: ${MIN_VOLUME:,} | Max per category: {MAX_MARKETS_PER_CAT}")
    print("=" * 60)

    # 1. Scan all events
    all_events = scan_events()

    # 2. Keyword pre-filter
    filtered = keyword_filter(all_events)
    if not filtered:
        print("No events matched keywords. Try lowering --min-volume.")
        return

    # 3. Fetch full market details (clobTokenIds etc.)
    enriched = fetch_market_details(filtered)

    # 4. Claude classification
    results = claude_classify(enriched)

    # 5. Deduplicate + rank
    final = deduplicate(results)

    # 6. Save
    save_config(final, dry_run=dry_run)


if __name__ == "__main__":
    main()
