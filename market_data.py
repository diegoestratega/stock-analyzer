import os
import time
import math
import requests
from datetime import datetime

from cache_upstash import get_json, set_json
from scoring import StockScorer

FMP_API_KEY = os.getenv("FMP_API_KEY", "")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

scorer = StockScorer()
sess = requests.Session()
sess.headers.update({"User-Agent": UA, "Accept": "application/json,text/plain,*/*"})

def _sleep_backoff(attempt: int):
    time.sleep(min(8, (2 ** attempt) + 0.2))

def _safe_get(url: str, params=None, timeout=15):
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

def _yahoo_quote(ticker: str) -> dict | None:
    url = "https://query1.finance.yahoo.com/v7/finance/quote"
    data = _safe_get(url, params={"symbols": ticker})
    if not data:
        return None
    res = (data.get("quoteResponse") or {}).get("result") or []
    if not res:
        return None
    q = res[0] or {}

    price = q.get("regularMarketPrice") or q.get("postMarketPrice") or q.get("preMarketPrice") or 0
    if not price:
        return None

    div_yield = q.get("dividendYield") or 0  # often fraction (0.0043)
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

def _yahoo_chart_5y_monthly(ticker: str) -> dict | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    data = _safe_get(url, params={"range": "5y", "interval": "1mo"})
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

    for i in range(min(len(ts), len(opens), len(highs), len(lows), len(closes))):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        if o is None or h is None or l is None or c is None:
            continue
        d = datetime.utcfromtimestamp(ts[i])
        ym = f"{d.year:04d}-{d.month:02d}"
        candle = {"date": ym, "open": float(o), "high": float(h), "low": float(l), "close": float(c)}
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
        "globalhigh": global_high,
        "globallow": global_low,
    }

def _fmp_get(endpoint: str, params: dict):
    if not FMP_API_KEY:
        return None
    base = "https://financialmodelingprep.com/stable"
    params = dict(params or {})
    params["apikey"] = FMP_API_KEY
    return _safe_get(f"{base}/{endpoint}", params=params, timeout=20)

def _fmp_growth_and_eps(ticker: str) -> dict:
    # cached long because FMP free is limited
    cache_key = f"fmp:growth:{ticker}"
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
        def parse_date(x):
            try:
                return datetime.strptime((x.get("date") or "")[:10], "%Y-%m-%d")
            except Exception:
                return datetime.min

        rows = sorted([x for x in inc if isinstance(x, dict)], key=parse_date, reverse=True)

        # EPS history (5)
        eps_hist = []
        for row in rows[:5]:
            eps_val = row.get("eps")
            if eps_val is None:
                eps_val = row.get("epsDiluted", 0)
            eps_hist.append({"date": (row.get("date") or "Unknown")[:10], "eps": float(eps_val or 0)})
        out["eps_history_5q"] = eps_hist

        # Quarterly YoY (0 vs 4)
        if len(rows) >= 5:
            rev0 = float(rows[0].get("revenue") or 0)
            rev4 = float(rows[4].get("revenue") or 0)
            if rev4:
                out["revenue_growth_quarterly_yoy"] = ((rev0 - rev4) / rev4) * 100.0

            eps0 = float(rows[0].get("eps") or rows[0].get("epsDiluted") or 0)
            eps4 = float(rows[4].get("eps") or rows[4].get("epsDiluted") or 0)
            if eps4:
                out["eps_growth_quarterly_yoy"] = ((eps0 - eps4) / abs(eps4)) * 100.0

        # Annual YoY TTM (0..3 vs 4..7)
        if len(rows) >= 8:
            rev_ttm1 = sum(float(rows[i].get("revenue") or 0) for i in range(0, 4))
            rev_ttm2 = sum(float(rows[i].get("revenue") or 0) for i in range(4, 8))
            if rev_ttm2:
                out["revenue_growth_annual_yoy"] = ((rev_ttm1 - rev_ttm2) / rev_ttm2) * 100.0

            eps_ttm1 = sum(float(rows[i].get("eps") or rows[i].get("epsDiluted") or 0) for i in range(0, 4))
            eps_ttm2 = sum(float(rows[i].get("eps") or rows[i].get("epsDiluted") or 0) for i in range(4, 8))
            if eps_ttm2:
                out["eps_growth_annual_yoy"] = ((eps_ttm1 - eps_ttm2) / abs(eps_ttm2)) * 100.0

    set_json(cache_key, out, ttl_seconds=24 * 3600)  # 24h
    return out

def _normalize_for_scoring(funds: dict) -> dict:
    price = funds.get("price") or 0
    low52w = funds.get("low_52w") or 0
    marketcap = funds.get("market_cap") or 0
    petrailing = funds.get("pe_trailing") or 0
    peforward = funds.get("pe_forward") or 0
    debttoequity = funds.get("debt_to_equity") or 0
    revgrowth = funds.get("revenue_growth_quarterly_yoy") or 0
    epsgrowth = funds.get("eps_growth_quarterly_yoy") or 0

    return {
        "price": price,
        "low52w": low52w,
        "marketcap": marketcap,
        "petrailing": petrailing,
        "peforward": peforward,
        "debttoequity": debttoequity,
        "revenuegrowthquarterlyyoy": revgrowth,
        "epsgrowthquarterlyyoy": epsgrowth,
    }

def get_analysis(ticker: str) -> dict | None:
    # Short TTL for interactive UI
    cache_key = f"analysis:{ticker}"
    cached = get_json(cache_key)
    if cached:
        return cached

    # Chart cached separately, long TTL
    chart_key = f"chart:{ticker}"
    chart = get_json(chart_key)

    quote = _yahoo_quote(ticker)
    if quote:
        # Long-lived enrichments from FMP (cached 24h)
        enrich = _fmp_growth_and_eps(ticker)
        funds = {**quote, **enrich}

        # Get chart (cached 6h)
        if not chart:
            chart = _yahoo_chart_5y_monthly(ticker) or {"candles": [], "global_high": None, "global_low": None}
            set_json(chart_key, chart, ttl_seconds=6 * 3600)

        score_in = _normalize_for_scoring(funds)
        score = scorer.evaluate(score_in)
        score["total_score"] = score.get("totalscore")
        score["final_grade"] = score.get("finalgrade")

        # Provide both naming styles for safety
        funds_compat = dict(funds)
        funds_compat.update({
            "marketcap": funds.get("market_cap", 0),
            "high52w": funds.get("high_52w", 0),
            "low52w": funds.get("low_52w", 0),
            "petrailing": funds.get("pe_trailing", 0),
            "peforward": funds.get("pe_forward", 0),
            "dividendyield": funds.get("dividend_yield", 0),
            "pricetobook": funds.get("price_to_book", 0),
            "debttoequity": funds.get("debt_to_equity", 0),
            "revenuegrowthannualyoy": funds.get("revenue_growth_annual_yoy", 0),
            "revenuegrowthquarterlyyoy": funds.get("revenue_growth_quarterly_yoy", 0),
            "epsgrowthannualyoy": funds.get("eps_growth_annual_yoy", 0),
            "epsgrowthquarterlyyoy": funds.get("eps_growth_quarterly_yoy", 0),
            "epshistory5q": funds.get("eps_history_5q", []),
        })

        out = {"ticker": ticker, "fundamentals": funds_compat, "chart": chart, "score": score}

        # Main cache + “last good” cache
        set_json(cache_key, out, ttl_seconds=5 * 60)
        set_json(f"analysis:lastgood:{ticker}", out, ttl_seconds=7 * 24 * 3600)
        return out

    # If Yahoo fails, serve last known good result (best UX)
    last_good = get_json(f"analysis:lastgood:{ticker}")
    if last_good:
        last_good["stale"] = True
        return last_good

    return None
