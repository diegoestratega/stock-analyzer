import os
import time
import requests
from datetime import datetime, date

from cache_upstash import get_json, set_json
from scoring import StockScorer

# Cache bust: change this string any time you want a clean slate in Upstash.
CACHE_VERSION = "v2"

FMP_API_KEY = (os.getenv("FMP_API_KEY") or os.getenv("FMPAPIKEY") or "").strip()

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

sess = requests.Session()
sess.headers.update(
    {
        "User-Agent": UA,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://finance.yahoo.com/",
    }
)

scorer = StockScorer()


def _sleep_backoff(attempt: int):
    time.sleep(min(10, (2**attempt) + 0.5))


def _safe_get_json(url: str, params=None, timeout=18):
    for attempt in range(3):
        try:
            r = sess.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                _sleep_backoff(attempt)
                continue
            return None
        except Exception:
            _sleep_backoff(attempt)
    return None


def _ck(key: str) -> str:
    return f"{CACHE_VERSION}:{key}"


# -------------------------
# Yahoo (primary)
# -------------------------
def _yahoo_quote(ticker: str) -> dict | None:
    # Try v7 then v6 (some environments block one but not the other)
    for base in (
        "https://query1.finance.yahoo.com/v7/finance/quote",
        "https://query1.finance.yahoo.com/v6/finance/quote",
    ):
        data = _safe_get_json(base, params={"symbols": ticker})
        if not data:
            continue

        res = (data.get("quoteResponse") or {}).get("result") or []
        if not res:
            continue

        q = res[0] or {}
        price = (
            q.get("regularMarketPrice")
            or q.get("postMarketPrice")
            or q.get("preMarketPrice")
            or 0
        )
        if not price:
            continue

        div_yield = q.get("dividendYield") or 0  # may be fraction
        if div_yield and div_yield < 1:
            div_yield = div_yield * 100

        return {
            "symbol": ticker,
            "price": float(price),
            "market_cap": float(q.get("marketCap") or 0),
            "high_52w": float(q.get("fiftyTwoWeekHigh") or 0),
            "low_52w": float(q.get("fiftyTwoWeekLow") or 0),
            "pe_trailing": float(q.get("trailingPE") or 0),
            "pe_forward": float(q.get("forwardPE") or 0),
            "dividend_yield": float(div_yield or 0),
            "price_to_book": float(q.get("priceToBook") or 0),
        }

    return None


def _yahoo_chart_5y_monthly(ticker: str) -> dict | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    data = _safe_get_json(url, params={"range": "5y", "interval": "1mo"})
    if not data:
        return None

    result = (((data.get("chart") or {}).get("result")) or [])
    if not result:
        return None

    r0 = result[0] or {}
    ts = r0.get("timestamp") or []
    quote = (((r0.get("indicators") or {}).get("quote")) or [])
    if not ts or not quote:
        return None

    q0 = quote[0] or {}
    opens = q0.get("open") or []
    highs = q0.get("high") or []
    lows = q0.get("low") or []
    closes = q0.get("close") or []

    candles = []
    global_high = None
    global_low = None

    n = min(len(ts), len(opens), len(highs), len(lows), len(closes))
    for i in range(n):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        if o is None or h is None or l is None or c is None:
            continue

        d = datetime.utcfromtimestamp(ts[i])
        ym = f"{d.year:04d}-{d.month:02d}"
        candle = {
            "date": ym,
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
        }
        candles.append(candle)

        if global_high is None or candle["high"] > global_high["price"]:
            global_high = {"price": candle["high"], "date": candle["date"]}
        if global_low is None or candle["low"] < global_low["price"]:
            global_low = {"price": candle["low"], "date": candle["date"]}

    if not candles:
        return None

    return {
        "candles": candles,
        "global_high": global_high,
        "global_low": global_low,
        # backward compat keys
        "globalhigh": global_high,
        "globallow": global_low,
    }


# -------------------------
# FMP (fallback)
# -------------------------
def _fmp_get(endpoint: str, params: dict):
    if not FMP_API_KEY:
        return None
    base = "https://financialmodelingprep.com/stable"
    p = dict(params or {})
    p["apikey"] = FMP_API_KEY
    return _safe_get_json(f"{base}/{endpoint}", params=p, timeout=22)


def _fmp_quote(ticker: str) -> dict | None:
    data = _fmp_get("quote", {"symbol": ticker})
    if not isinstance(data, list) or not data:
        return None

    q = data[0] if isinstance(data[0], dict) else {}
    price = q.get("price") or 0
    if not price:
        return None

    last_div = q.get("lastDiv") or 0
    dividend_yield = ((last_div / price) * 100.0) if (price and last_div) else 0.0

    return {
        "symbol": ticker,
        "price": float(price),
        "market_cap": float(q.get("marketCap") or 0),
        "high_52w": float(q.get("yearHigh") or 0),
        "low_52w": float(q.get("yearLow") or 0),
        "pe_trailing": float(q.get("pe") or 0),
        "pe_forward": float(q.get("forwardPE") or 0),
        "dividend_yield": float(dividend_yield),
        "price_to_book": float(q.get("priceToBook") or 0),
    }


def _fmp_chart_5y_monthly(ticker: str) -> dict | None:
    raw = _fmp_get("historical-price-eod/light", {"symbol": ticker})
    if raw is None:
        return None

    rows = (
        raw
        if isinstance(raw, list)
        else (raw.get("historical") if isinstance(raw, dict) else None)
    )
    if not isinstance(rows, list) or not rows:
        return None

    today = date.today()
    cutoff = date(today.year - 5, today.month, min(today.day, 28))

    def parse_date(s):
        try:
            return datetime.strptime((s or "")[:10], "%Y-%m-%d").date()
        except Exception:
            return None

    filtered = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = parse_date(r.get("date"))
        if d and d >= cutoff:
            filtered.append((d, r))
    filtered.sort(key=lambda x: x[0])  # old -> new

    if not filtered:
        return None

    # Monthly aggregation
    by_month = {}
    for d, r in filtered:
        key = f"{d.year:04d}-{d.month:02d}"
        o = float(r.get("open") or 0)
        h = float(r.get("high") or 0)
        l = float(r.get("low") or 0)
        c = float(r.get("close") or 0)
        if not (o and h and l and c):
            continue

        if key not in by_month:
            by_month[key] = {"date": key, "open": o, "high": h, "low": l, "close": c}
        else:
            by_month[key]["high"] = max(by_month[key]["high"], h)
            by_month[key]["low"] = min(by_month[key]["low"], l)
            by_month[key]["close"] = c

    candles = list(by_month.values())
    if not candles:
        return None

    global_high = None
    global_low = None
    for c in candles:
        if global_high is None or c["high"] > global_high["price"]:
            global_high = {"price": c["high"], "date": c["date"]}
        if global_low is None or c["low"] < global_low["price"]:
            global_low = {"price": c["low"], "date": c["date"]}

    return {
        "candles": candles,
        "global_high": global_high,
        "global_low": global_low,
        "globalhigh": global_high,
        "globallow": global_low,
    }


def _fmp_growth_eps_enrich(ticker: str) -> dict:
    # Optional enrichment; cache 24h because FMP free is tight
    cache_key = _ck(f"fmp:enrich:{ticker}")
    cached = get_json(cache_key)
    if cached:
        return cached

    out = {
        "debt_to_equity": 0.0,
        "revenue_growth_annual_yoy": 0.0,
        "revenue_growth_quarterly_yoy": 0.0,
        "eps_growth_annual_yoy": 0.0,
        "eps_growth_quarterly_yoy": 0.0,
        "eps_history_5q": [],
    }

    km = _fmp_get("key-metrics-ttm", {"symbol": ticker})
    if isinstance(km, list) and km and isinstance(km[0], dict):
        out["debt_to_equity"] = float(km[0].get("debtToEquityRatioTTM") or 0)

    inc = _fmp_get("income-statement", {"symbol": ticker, "period": "quarter", "limit": 8})
    if isinstance(inc, list) and inc:

        def dt(x):
            try:
                return datetime.strptime((x.get("date") or "")[:10], "%Y-%m-%d")
            except Exception:
                return datetime.min

        rows = sorted([x for x in inc if isinstance(x, dict)], key=dt, reverse=True)

        out["eps_history_5q"] = [
            {
                "date": (r.get("date") or "Unknown")[:10],
                "eps": float((r.get("eps") or r.get("epsDiluted") or 0)),
            }
            for r in rows[:5]
        ]

        if len(rows) >= 5:
            rev0 = float(rows[0].get("revenue") or 0)
            rev4 = float(rows[4].get("revenue") or 0)
            if rev4:
                out["revenue_growth_quarterly_yoy"] = ((rev0 - rev4) / rev4) * 100.0

            eps0 = float(rows[0].get("eps") or rows[0].get("epsDiluted") or 0)
            eps4 = float(rows[4].get("eps") or rows[4].get("epsDiluted") or 0)
            if eps4:
                out["eps_growth_quarterly_yoy"] = ((eps0 - eps4) / abs(eps4)) * 100.0

        if len(rows) >= 8:
            rev_ttm1 = sum(float(rows[i].get("revenue") or 0) for i in range(0, 4))
            rev_ttm2 = sum(float(rows[i].get("revenue") or 0) for i in range(4, 8))
            if rev_ttm2:
                out["revenue_growth_annual_yoy"] = ((rev_ttm1 - rev_ttm2) / rev_ttm2) * 100.0

            eps_ttm1 = sum(float(rows[i].get("eps") or rows[i].get("epsDiluted") or 0) for i in range(0, 4))
            eps_ttm2 = sum(float(rows[i].get("eps") or rows[i].get("epsDiluted") or 0) for i in range(4, 8))
            if eps_ttm2:
                out["eps_growth_annual_yoy"] = ((eps_ttm1 - eps_ttm2) / abs(eps_ttm2)) * 100.0

    set_json(cache_key, out, ttl_seconds=24 * 3600)
    return out


def _for_scoring(f: dict) -> dict:
    return {
        "price": f.get("price", 0) or 0,
        "low52w": f.get("low_52w", 0) or 0,
        "marketcap": f.get("market_cap", 0) or 0,
        "petrailing": f.get("pe_trailing", 0) or 0,
        "peforward": f.get("pe_forward", 0) or 0,
        "debttoequity": f.get("debt_to_equity", 0) or 0,
        "revenuegrowthquarterlyyoy": f.get("revenue_growth_quarterly_yoy", 0) or 0,
        "epsgrowthquarterlyyoy": f.get("eps_growth_quarterly_yoy", 0) or 0,
    }


def get_analysis(ticker: str, debug: bool = False) -> dict | None:
    main_key = _ck(f"analysis:{ticker}")
    cached = get_json(main_key)
    if cached:
        if debug and "debug" not in cached:
            cached["debug"] = {"served": "cache", "cache_version": CACHE_VERSION}
        return cached

    debug_info = {
        "cache_version": CACHE_VERSION,
        "quote_source": None,
        "chart_source": None,
        "enrich_source": None,
    }

    # Quote: Yahoo -> FMP
    quote = _yahoo_quote(ticker)
    if quote:
        debug_info["quote_source"] = "yahoo"
    else:
        quote = _fmp_quote(ticker)
        if quote:
            debug_info["quote_source"] = "fmp"

    lastgood_key = _ck(f"analysis:lastgood:{ticker}")
    if not quote:
        last_good = get_json(lastgood_key)
        if last_good:
            last_good["stale"] = True
            if debug:
                last_good["debug"] = {"served": "lastgood", **debug_info}
            return last_good
        return None

    # Enrich (optional): FMP cached 24h
    if FMP_API_KEY:
        enrich = _fmp_growth_eps_enrich(ticker)
        debug_info["enrich_source"] = "fmp"
    else:
        enrich = {
            "debt_to_equity": 0.0,
            "revenue_growth_annual_yoy": 0.0,
            "revenue_growth_quarterly_yoy": 0.0,
            "eps_growth_annual_yoy": 0.0,
            "eps_growth_quarterly_yoy": 0.0,
            "eps_history_5q": [],
        }
        debug_info["enrich_source"] = "none"

    funds = {**quote, **enrich}

    # Chart cached separately (6h): Yahoo -> FMP
    chart_key = _ck(f"chart:{ticker}")
    chart = get_json(chart_key)
    if not chart:
        chart = _yahoo_chart_5y_monthly(ticker)
        if chart:
            debug_info["chart_source"] = "yahoo"
        else:
            chart = _fmp_chart_5y_monthly(ticker)
            if chart:
                debug_info["chart_source"] = "fmp"

        if not chart:
            chart = {
                "candles": [],
                "global_high": None,
                "global_low": None,
                "globalhigh": None,
                "globallow": None,
            }

        set_json(chart_key, chart, ttl_seconds=6 * 3600)

    # Score
    score = scorer.evaluate(_for_scoring(funds))

    # Force the fields you were searching for to always exist in the JSON
    # (both snake_case + legacy names the UI might reference).
    funds_out = dict(funds)
    funds_out.setdefault("pe_trailing", 0.0)
    funds_out.setdefault("pe_forward", 0.0)
    funds_out.setdefault("debt_to_equity", 0.0)
    funds_out.setdefault("eps_history_5q", [])
    funds_out.setdefault("market_cap", 0.0)
    funds_out.setdefault("price_to_book", 0.0)

    # Legacy compatibility keys
    funds_out.update(
        {
            "marketcap": funds_out.get("market_cap", 0),
            "high52w": funds_out.get("high_52w", 0),
            "low52w": funds_out.get("low_52w", 0),
            "petrailing": funds_out.get("pe_trailing", 0),
            "peforward": funds_out.get("pe_forward", 0),
            "dividendyield": funds_out.get("dividend_yield", 0),
            "pricetobook": funds_out.get("price_to_book", 0),
            "debttoequity": funds_out.get("debt_to_equity", 0),
            "revenuegrowthannualyoy": funds_out.get("revenue_growth_annual_yoy", 0),
            "revenuegrowthquarterlyyoy": funds_out.get("revenue_growth_quarterly_yoy", 0),
            "epsgrowthannualyoy": funds_out.get("eps_growth_annual_yoy", 0),
            "epsgrowthquarterlyyoy": funds_out.get("eps_growth_quarterly_yoy", 0),
            "epshistory5q": funds_out.get("eps_history_5q", []),
        }
    )

    out = {"ticker": ticker, "fundamentals": funds_out, "chart": chart, "score": score}
    if debug:
        out["debug"] = debug_info

    set_json(main_key, out, ttl_seconds=5 * 60)
    set_json(lastgood_key, out, ttl_seconds=7 * 24 * 3600)
    return out
