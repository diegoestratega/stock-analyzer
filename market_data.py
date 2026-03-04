import os
import time
import requests
from datetime import datetime, date
from typing import Optional, Dict, Any, List, Tuple

from cache_upstash import get_json, set_json
from scoring import StockScorer

CACHE_VERSION = "v6"

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


def _ck(key: str) -> str:
    return f"{CACHE_VERSION}:{key}"


def _sleep_backoff(attempt: int):
    time.sleep(min(10, (2**attempt) + 0.5))


def _safe_get_json(url: str, params=None, timeout=18) -> Optional[Any]:
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


def _num(x, dflt=0.0) -> float:
    try:
        if x is None:
            return dflt
        return float(x)
    except Exception:
        return dflt


def _parse_ymd(s: str) -> Optional[datetime]:
    try:
        return datetime.strptime((s or "")[:10], "%Y-%m-%d")
    except Exception:
        return None


def _merge_fill_missing(primary: Dict[str, Any], secondary: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(primary or {})
    sec = secondary or {}
    for k, v in sec.items():
        cur = out.get(k)
        missing = (cur is None) or (cur == 0) or (cur == 0.0) or (cur == "") or (cur == [])
        if missing and v not in (None, "", [], 0, 0.0):
            out[k] = v
    return out


def _pct_from_frac(v: float) -> float:
    v = _num(v, 0.0)
    if v <= 0:
        return 0.0
    return v * 100.0 if v < 1 else v


# -------------------------
# Yahoo (quote + chart)
# -------------------------
def _yahoo_quote(ticker: str) -> Optional[Dict[str, Any]]:
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
        price = q.get("regularMarketPrice") or q.get("postMarketPrice") or q.get("preMarketPrice") or 0
        price = _num(price, 0.0)
        if not price:
            continue

        div_yield = _pct_from_frac(_num(q.get("dividendYield"), 0.0))

        return {
            "symbol": ticker,
            "price": price,
            "market_cap": _num(q.get("marketCap"), 0.0),
            "high_52w": _num(q.get("fiftyTwoWeekHigh"), 0.0),
            "low_52w": _num(q.get("fiftyTwoWeekLow"), 0.0),
            "pe_trailing": _num(q.get("trailingPE"), 0.0),
            "pe_forward": _num(q.get("forwardPE"), 0.0),
            "dividend_yield": _num(div_yield, 0.0),
            "price_to_book": _num(q.get("priceToBook"), 0.0),
        }

    return None


def _yahoo_chart_5y_monthly(ticker: str) -> Optional[Dict[str, Any]]:
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
        candle = {"date": ym, "open": _num(o), "high": _num(h), "low": _num(l), "close": _num(c)}
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


# -------------------------
# FMP (Stable + v3)
# -------------------------
def _fmp_stable_get(endpoint: str, params: Dict[str, Any]):
    if not FMP_API_KEY:
        return None
    base = "https://financialmodelingprep.com/stable"
    p = dict(params or {})
    p["apikey"] = FMP_API_KEY
    return _safe_get_json(f"{base}/{endpoint}", params=p, timeout=22)


def _fmp_v3_get(path: str, params: Dict[str, Any]):
    if not FMP_API_KEY:
        return None
    base = "https://financialmodelingprep.com/api/v3"
    p = dict(params or {})
    p["apikey"] = FMP_API_KEY
    return _safe_get_json(f"{base}/{path.lstrip('/')}", params=p, timeout=22)


def _fmp_quote_stable(ticker: str) -> Optional[Dict[str, Any]]:
    data = _fmp_stable_get("quote", {"symbol": ticker})
    if not isinstance(data, list) or not data:
        return None

    q = data[0] if isinstance(data[0], dict) else {}
    price = _num(q.get("price"), 0.0)
    if not price:
        return None

    last_div = _num(q.get("lastDiv"), 0.0)
    dividend_yield = ((last_div / price) * 100.0) if (price and last_div) else 0.0

    return {
        "symbol": ticker,
        "price": price,
        "market_cap": _num(q.get("marketCap"), 0.0),
        "high_52w": _num(q.get("yearHigh"), 0.0),
        "low_52w": _num(q.get("yearLow"), 0.0),
        "pe_trailing": _num(q.get("pe"), 0.0),
        "pe_forward": _num(q.get("forwardPE"), 0.0),
        "dividend_yield": _num(dividend_yield, 0.0),
        "price_to_book": _num(q.get("priceToBook"), 0.0),
    }


def _fmp_quote_v3(ticker: str) -> Optional[Dict[str, Any]]:
    # v3 quote endpoint is commonly used as /api/v3/quote/{symbol} [web:105]
    data = _fmp_v3_get(f"quote/{ticker}", {})
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None
    q = data[0]
    price = _num(q.get("price"), 0.0)
    if not price:
        return None

    dy = _num(q.get("dividendYield"), 0.0)
    dy = _pct_from_frac(dy)

    return {
        "symbol": ticker,
        "price": price,
        "market_cap": _num(q.get("marketCap"), 0.0),
        "high_52w": _num(q.get("yearHigh"), 0.0),
        "low_52w": _num(q.get("yearLow"), 0.0),
        "pe_trailing": _num(q.get("pe"), 0.0),
        "pe_forward": _num(q.get("forwardPE"), 0.0),
        "dividend_yield": dy,
        "price_to_book": _num(q.get("priceToBook"), 0.0),
    }


def _fmp_ratios_ttm_v3(ticker: str) -> Dict[str, Any]:
    # Company TTM ratios endpoint: /api/v3/ratios-ttm/{symbol} [web:105]
    out = {"__ratios_ok": False}
    data = _fmp_v3_get(f"ratios-ttm/{ticker}", {})
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return out

    r = data[0]
    out["__ratios_ok"] = True

    pb = _num(r.get("priceToBookRatioTTM"), 0.0)
    de = _num(r.get("debtEquityRatioTTM"), 0.0)
    dy = _pct_from_frac(_num(r.get("dividendYieldTTM"), 0.0))
    pe = _num(r.get("priceEarningsRatioTTM"), 0.0)
    if pe <= 0:
        pe = _num(r.get("peRatioTTM"), 0.0)

    out.update(
        {
            "price_to_book": pb,
            "debt_to_equity": de,
            "dividend_yield": dy,
            "pe_trailing": pe,
        }
    )
    return out


def _fmp_key_metrics_ttm_v3(ticker: str) -> Dict[str, Any]:
    # Company TTM key metrics endpoint: /api/v3/key-metrics-ttm/{symbol} [web:105]
    out = {"__km_v3_ok": False}
    data = _fmp_v3_get(f"key-metrics-ttm/{ticker}", {"limit": 1})
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return out

    m = data[0]
    out["__km_v3_ok"] = True

    out.update(
        {
            "debt_to_equity": _num(m.get("debtToEquityRatioTTM"), 0.0),
            "price_to_book": _num(m.get("pbRatioTTM") or m.get("priceToBookRatioTTM"), 0.0),
            "pe_trailing": _num(m.get("peRatioTTM") or m.get("peTTM"), 0.0),
            "dividend_yield": _pct_from_frac(_num(m.get("dividendYieldTTM") or m.get("dividendYieldPercentageTTM"), 0.0)),
        }
    )
    return out


def _fmp_income_statement_quarterly_5q(ticker: str) -> Tuple[bool, List[Dict[str, Any]], str]:
    # Stable income statement endpoint supports period+limit params [page:0]
    inc = _fmp_stable_get("income-statement", {"symbol": ticker, "period": "quarter", "limit": 5})
    if isinstance(inc, list) and inc and isinstance(inc[0], dict):
        rows = [r for r in inc if isinstance(r, dict)]
        rows.sort(key=lambda r: _parse_ymd(r.get("date") or "") or datetime.min, reverse=True)
        return True, rows, "stable"

    inc2 = _fmp_v3_get(f"income-statement/{ticker}", {"period": "quarter", "limit": 5})
    if isinstance(inc2, list) and inc2 and isinstance(inc2[0], dict):
        rows = [r for r in inc2 if isinstance(r, dict)]
        rows.sort(key=lambda r: _parse_ymd(r.get("date") or "") or datetime.min, reverse=True)
        return True, rows, "v3"

    return False, [], "none"


def _fmp_income_statement_annual_2y(ticker: str) -> Tuple[bool, List[Dict[str, Any]], str]:
    inc = _fmp_stable_get("income-statement", {"symbol": ticker, "period": "annual", "limit": 2})
    if isinstance(inc, list) and inc and isinstance(inc[0], dict):
        rows = [r for r in inc if isinstance(r, dict)]
        rows.sort(key=lambda r: _parse_ymd(r.get("date") or "") or datetime.min, reverse=True)
        return True, rows, "stable"

    inc2 = _fmp_v3_get(f"income-statement/{ticker}", {"period": "annual", "limit": 2})
    if isinstance(inc2, list) and inc2 and isinstance(inc2[0], dict):
        rows = [r for r in inc2 if isinstance(r, dict)]
        rows.sort(key=lambda r: _parse_ymd(r.get("date") or "") or datetime.min, reverse=True)
        return True, rows, "v3"

    return False, [], "none"


def _fmp_enrich_5q(ticker: str, price: float) -> Dict[str, Any]:
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
        "pe_trailing": 0.0,
        "pe_forward": 0.0,
        "price_to_book": 0.0,
        "dividend_yield": 0.0,
        "__q_ok": False,
        "__q_src": "none",
        "__a_ok": False,
        "__a_src": "none",
        "__ratios_ok": False,
        "__km_v3_ok": False,
    }

    # 1) Ratios-ttm + key-metrics-ttm (v3) to fill PB/DY/DE/PE [web:105]
    ratios = _fmp_ratios_ttm_v3(ticker)
    kmv3 = _fmp_key_metrics_ttm_v3(ticker)
    out = _merge_fill_missing(out, ratios)
    out = _merge_fill_missing(out, kmv3)
    out["__ratios_ok"] = bool(ratios.get("__ratios_ok"))
    out["__km_v3_ok"] = bool(kmv3.get("__km_v3_ok"))

    # 2) Quarterly EPS (limit=5)
    ok_q, rows_q, q_src = _fmp_income_statement_quarterly_5q(ticker)
    out["__q_ok"] = bool(ok_q)
    out["__q_src"] = q_src

    if ok_q and rows_q:
        eps_hist = []
        for r in rows_q[:5]:
            eps_val = r.get("eps")
            if eps_val is None:
                eps_val = r.get("epsDiluted", 0)
            eps_hist.append({"date": (r.get("date") or "Unknown")[:10], "eps": _num(eps_val, 0.0)})
        out["eps_history_5q"] = eps_hist

        if len(rows_q) >= 5:
            rev0 = _num(rows_q[0].get("revenue"), 0.0)
            rev4 = _num(rows_q[4].get("revenue"), 0.0)
            if rev4:
                out["revenue_growth_quarterly_yoy"] = ((rev0 - rev4) / rev4) * 100.0

            eps0 = _num(rows_q[0].get("eps") or rows_q[0].get("epsDiluted") or 0, 0.0)
            eps4 = _num(rows_q[4].get("eps") or rows_q[4].get("epsDiluted") or 0, 0.0)
            if eps4:
                out["eps_growth_quarterly_yoy"] = ((eps0 - eps4) / abs(eps4)) * 100.0

        # trailing PE from EPS TTM if still missing
        if out.get("pe_trailing", 0.0) <= 0 and len(rows_q) >= 4:
            eps_ttm = sum(_num(rows_q[i].get("eps") or rows_q[i].get("epsDiluted") or 0, 0.0) for i in range(0, 4))
            if eps_ttm:
                out["pe_trailing"] = float(price) / float(eps_ttm)

    # 3) Annual YoY (limit=2)
    ok_a, rows_a, a_src = _fmp_income_statement_annual_2y(ticker)
    out["__a_ok"] = bool(ok_a)
    out["__a_src"] = a_src
    if ok_a and len(rows_a) >= 2:
        rev_now = _num(rows_a[0].get("revenue"), 0.0)
        rev_prev = _num(rows_a[1].get("revenue"), 0.0)
        if rev_prev:
            out["revenue_growth_annual_yoy"] = ((rev_now - rev_prev) / rev_prev) * 100.0

        eps_now = _num(rows_a[0].get("eps") or rows_a[0].get("epsDiluted") or 0, 0.0)
        eps_prev = _num(rows_a[1].get("eps") or rows_a[1].get("epsDiluted") or 0, 0.0)
        if eps_prev:
            out["eps_growth_annual_yoy"] = ((eps_now - eps_prev) / abs(eps_prev)) * 100.0

    set_json(cache_key, out, ttl_seconds=12 * 3600)
    return out


# -------------------------
# FMP chart fallback
# -------------------------
def _fmp_chart_5y_monthly(ticker: str) -> Optional[Dict[str, Any]]:
    raw = _fmp_stable_get("historical-price-eod/light", {"symbol": ticker})
    if raw is None:
        return None

    rows = raw if isinstance(raw, list) else (raw.get("historical") if isinstance(raw, dict) else None)
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
    filtered.sort(key=lambda x: x[0])

    if not filtered:
        return None

    by_month = {}
    for d, r in filtered:
        key = f"{d.year:04d}-{d.month:02d}"
        o = _num(r.get("open"))
        h = _num(r.get("high"))
        l = _num(r.get("low"))
        c = _num(r.get("close"))
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


def _for_scoring(f: Dict[str, Any]) -> Dict[str, Any]:
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


def get_analysis(ticker: str, debug: bool = False) -> Optional[Dict[str, Any]]:
    main_key = _ck(f"analysis:{ticker}")
    cached = get_json(main_key)
    if cached:
        if debug and "debug" not in cached:
            cached["debug"] = {"served": "cache", "cache_version": CACHE_VERSION}
        return cached

    debug_info = {
        "cache_version": CACHE_VERSION,
        "fmp_key_present": bool(FMP_API_KEY),
        "quote_source": None,
        "yahoo_quote_ok": None,
        "fmp_quote_ok": None,
        "fmp_quote_v3_ok": None,
        "enrich_source": None,
        "fmp_q_ok": None,
        "fmp_q_src": None,
        "fmp_a_ok": None,
        "fmp_a_src": None,
        "fmp_ratios_ok": None,
        "fmp_km_v3_ok": None,
        "chart_source": None,
    }

    # Quote: FMP Stable first, merge in FMP v3 quote, then Yahoo fill
    fmp_q = _fmp_quote_stable(ticker) if FMP_API_KEY else None
    fmp_q_v3 = _fmp_quote_v3(ticker) if FMP_API_KEY else None
    yahoo_q = _yahoo_quote(ticker)

    debug_info["fmp_quote_ok"] = bool(fmp_q)
    debug_info["fmp_quote_v3_ok"] = bool(fmp_q_v3)
    debug_info["yahoo_quote_ok"] = bool(yahoo_q)

    if fmp_q:
        quote = dict(fmp_q)
        debug_info["quote_source"] = "fmp"
        if fmp_q_v3:
            quote = _merge_fill_missing(quote, fmp_q_v3)
        if yahoo_q:
            quote = _merge_fill_missing(quote, yahoo_q)
    elif yahoo_q:
        quote = dict(yahoo_q)
        debug_info["quote_source"] = "yahoo"
    else:
        last_good = get_json(_ck(f"analysis:lastgood:{ticker}"))
        if last_good:
            last_good["stale"] = True
            if debug:
                last_good["debug"] = {"served": "lastgood", **debug_info}
            return last_good
        return None

    enrich = _fmp_enrich_5q(ticker, price=_num(quote.get("price", 0.0))) if FMP_API_KEY else {}
    debug_info["enrich_source"] = "fmp" if FMP_API_KEY else "none"
    debug_info["fmp_q_ok"] = bool(enrich.get("__q_ok"))
    debug_info["fmp_q_src"] = enrich.get("__q_src")
    debug_info["fmp_a_ok"] = bool(enrich.get("__a_ok"))
    debug_info["fmp_a_src"] = enrich.get("__a_src")
    debug_info["fmp_ratios_ok"] = bool(enrich.get("__ratios_ok"))
    debug_info["fmp_km_v3_ok"] = bool(enrich.get("__km_v3_ok"))

    funds = _merge_fill_missing(quote, {k: v for k, v in enrich.items() if not str(k).startswith("__")})
    if yahoo_q:
        funds = _merge_fill_missing(funds, yahoo_q)

    chart_key = _ck(f"chart:{ticker}")
    chart = get_json(chart_key)
    if chart:
        debug_info["chart_source"] = "cache"
    else:
        chart = _yahoo_chart_5y_monthly(ticker)
        if chart:
            debug_info["chart_source"] = "yahoo"
        else:
            chart = _fmp_chart_5y_monthly(ticker)
            if chart:
                debug_info["chart_source"] = "fmp"
            else:
                chart = {"candles": [], "global_high": None, "global_low": None, "globalhigh": None, "globallow": None}
                debug_info["chart_source"] = "none"
        set_json(chart_key, chart, ttl_seconds=6 * 3600)

    # Ensure fields exist
    funds.setdefault("pe_trailing", 0.0)
    funds.setdefault("pe_forward", 0.0)
    funds.setdefault("price_to_book", 0.0)
    funds.setdefault("dividend_yield", 0.0)
    funds.setdefault("debt_to_equity", 0.0)
    funds.setdefault("revenue_growth_annual_yoy", 0.0)
    funds.setdefault("revenue_growth_quarterly_yoy", 0.0)
    funds.setdefault("eps_growth_annual_yoy", 0.0)
    funds.setdefault("eps_growth_quarterly_yoy", 0.0)
    funds.setdefault("eps_history_5q", [])

    score = scorer.evaluate(_for_scoring(funds))

    # Legacy keys
    funds_compat = dict(funds)
    funds_compat.update(
        {
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
        }
    )

    out = {"ticker": ticker, "fundamentals": funds_compat, "chart": chart, "score": score}
    if debug:
        out["debug"] = debug_info

    set_json(main_key, out, ttl_seconds=5 * 60)
    set_json(_ck(f"analysis:lastgood:{ticker}"), out, ttl_seconds=7 * 24 * 3600)
    return out
