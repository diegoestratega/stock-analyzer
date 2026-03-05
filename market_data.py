import os
import time
import random
import requests
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List, Tuple

from cache_upstash import get_json, set_json
from scoring import StockScorer

CACHE_VERSION = "v11"

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


def _num(x, dflt=0.0) -> float:
    try:
        if x is None:
            return dflt
        return float(x)
    except Exception:
        return dflt


def _pct_from_frac(v: float) -> float:
    v = _num(v, 0.0)
    if v <= 0:
        return 0.0
    return v * 100.0 if v < 1 else v


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


def _sleep_jitter(attempt: int, cap: float = 12.0):
    time.sleep(min(cap, (2**attempt) + 0.5 + random.random()))


def _safe_get_json(url: str, params=None, timeout=18) -> Optional[Any]:
    # Harder-to-fail retries than your current v9/v10-style implementation. [file:93]
    for attempt in range(4):
        try:
            r = sess.get(url, params=params, timeout=timeout, allow_redirects=True)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                _sleep_jitter(attempt)
                continue
            return None
        except Exception:
            _sleep_jitter(attempt)
    return None


# -------------------------
# Yahoo helpers
# -------------------------
def _yahoo_bootstrap():
    # Populate cookies (best-effort) so quoteSummary is less flaky in serverless.
    # Cache the fact we tried bootstrapping.
    key = _ck("yahoo:boot")
    if get_json(key):
        return
    try:
        sess.get("https://fc.yahoo.com/", timeout=10, allow_redirects=True)
    except Exception:
        pass
    set_json(key, {"ok": True}, ttl_seconds=6 * 3600)


def _dig_raw(obj: Any) -> float:
    # Yahoo quoteSummary numbers commonly look like {"raw": 123, "fmt": "123"}
    if obj is None:
        return 0.0
    if isinstance(obj, (int, float)):
        return float(obj)
    if isinstance(obj, dict):
        return _num(obj.get("raw"), _num(obj.get("value"), 0.0))
    return 0.0


def _yahoo_quote_summary(ticker: str) -> Optional[Dict[str, Any]]:
    """
    Closest HTTP equivalent to what yfinance.info surfaces (price/marketCap/PE/dividend/PB/etc.). [file:7]
    """
    cache_key = _ck(f"yahoo:qs:{ticker}")
    cached = get_json(cache_key)
    if cached:
        return cached

    _yahoo_bootstrap()

    modules = ",".join(
        [
            "price",
            "summaryDetail",
            "defaultKeyStatistics",
            "financialData",
            "earningsHistory",
            "incomeStatementHistoryQuarterly",
        ]
    )

    url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
    data = _safe_get_json(url, params={"modules": modules}, timeout=18)
    if not data:
        return None

    res = (((data.get("quoteSummary") or {}).get("result")) or [])
    if not res:
        return None

    r0 = res[0] or {}
    price = r0.get("price") or {}
    sd = r0.get("summaryDetail") or {}
    dks = r0.get("defaultKeyStatistics") or {}
    fd = r0.get("financialData") or {}

    out = {
        "symbol": ticker,
        "price": _dig_raw(price.get("regularMarketPrice")) or _dig_raw(sd.get("regularMarketPrice")),
        "market_cap": _dig_raw(price.get("marketCap")) or _dig_raw(sd.get("marketCap")),
        "high_52w": _dig_raw(sd.get("fiftyTwoWeekHigh")),
        "low_52w": _dig_raw(sd.get("fiftyTwoWeekLow")),
        "pe_trailing": _dig_raw(sd.get("trailingPE")),
        "pe_forward": _dig_raw(dks.get("forwardPE")) or _dig_raw(fd.get("forwardPE")),
        "price_to_book": _dig_raw(dks.get("priceToBook")),
    }

    div_rate = _dig_raw(sd.get("dividendRate")) or _dig_raw(sd.get("trailingAnnualDividendRate"))
    div_yield = _dig_raw(sd.get("dividendYield"))
    if div_yield:
        out["dividend_yield"] = _pct_from_frac(div_yield)
    else:
        p = _num(out.get("price"), 0.0)
        out["dividend_yield"] = (div_rate / p) * 100.0 if (p and div_rate) else 0.0

    set_json(cache_key, out, ttl_seconds=10 * 60)
    return out


def _yahoo_quote_v7v6(ticker: str) -> Optional[Dict[str, Any]]:
    # Secondary Yahoo source (sometimes blocked, but cheap to try). [file:93]
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
        if not price:
            continue

        div_yield = q.get("dividendYield") or 0
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


def _yahoo_meta_quote(ticker: str) -> Optional[Dict[str, Any]]:
    # Very reliable for price/52w/PEs, but often missing marketCap for some symbols.
    cache_key = _ck(f"yahoo:meta:{ticker}")
    cached = get_json(cache_key)
    if cached:
        return cached

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    data = _safe_get_json(url, params={"range": "1d", "interval": "1d"})
    if not data:
        return None

    result = (((data.get("chart") or {}).get("result")) or [])
    if not result:
        return None

    meta = (result[0] or {}).get("meta") or {}
    if not isinstance(meta, dict) or not meta:
        return None

    out = {
        "symbol": ticker,
        "price": _num(meta.get("regularMarketPrice") or meta.get("chartPreviousClose") or 0, 0.0),
        "market_cap": _num(meta.get("marketCap"), 0.0),
        "high_52w": _num(meta.get("fiftyTwoWeekHigh"), 0.0),
        "low_52w": _num(meta.get("fiftyTwoWeekLow"), 0.0),
        "pe_trailing": _num(meta.get("trailingPE"), 0.0),
        "pe_forward": _num(meta.get("forwardPE"), 0.0),
        "dividend_yield": _pct_from_frac(_num(meta.get("dividendYield"), 0.0)),
        "price_to_book": _num(meta.get("priceToBook"), 0.0),
    }

    set_json(cache_key, out, ttl_seconds=5 * 60)
    return out


def _yahoo_dividend_yield_from_events(ticker: str, price: float) -> Tuple[float, float]:
    if not price:
        return 0.0, 0.0

    cache_key = _ck(f"yahoo:divtrail:{ticker}")
    cached = get_json(cache_key)
    if cached and isinstance(cached, dict):
        return _num(cached.get("annual_div", 0.0)), _num(cached.get("yield_pct", 0.0))

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    data = _safe_get_json(
        url,
        params={
            "range": "2y",
            "interval": "1d",
            "events": "div|split",
            "includePrePost": "false",
        },
        timeout=18,
    )
    if not data:
        return 0.0, 0.0

    result = (((data.get("chart") or {}).get("result")) or [])
    if not result:
        return 0.0, 0.0

    events = (result[0] or {}).get("events") or {}
    divs = events.get("dividends") or {}
    if not isinstance(divs, dict) or not divs:
        return 0.0, 0.0

    cutoff = datetime.utcnow() - timedelta(days=365)
    total = 0.0

    for _, v in divs.items():
        if not isinstance(v, dict):
            continue
        ts = v.get("date")
        amt = _num(v.get("amount"), 0.0)
        if not ts or amt <= 0:
            continue
        try:
            d = datetime.utcfromtimestamp(int(ts))
        except Exception:
            continue
        if d >= cutoff:
            total += amt

    yld = (total / float(price)) * 100.0 if (price and total) else 0.0
    set_json(cache_key, {"annual_div": total, "yield_pct": yld}, ttl_seconds=6 * 3600)
    return total, yld


def _yahoo_eps_history_5q_from_qs(ticker: str) -> List[Dict[str, Any]]:
    cache_key = _ck(f"yahoo:eps5:{ticker}")
    cached = get_json(cache_key)
    if cached and isinstance(cached, list):
        return cached

    _yahoo_bootstrap()
    url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
    data = _safe_get_json(url, params={"modules": "earningsHistory"}, timeout=18)
    if not data:
        return []

    res = (((data.get("quoteSummary") or {}).get("result")) or [])
    if not res:
        return []

    eh = (res[0] or {}).get("earningsHistory") or {}
    hist = eh.get("history") or []
    if not isinstance(hist, list) or not hist:
        return []

    rows = []
    for r in hist:
        if not isinstance(r, dict):
            continue
        qtr = r.get("quarter") or {}
        ts = _dig_raw(qtr.get("raw")) if isinstance(qtr, dict) else 0
        d = datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d") if ts else "Unknown"
        eps = _dig_raw(r.get("epsActual")) or _dig_raw(r.get("epsEstimate"))
        if eps == 0:
            continue
        rows.append({"date": d, "eps": float(eps)})

    rows = rows[:5]
    set_json(cache_key, rows, ttl_seconds=6 * 3600)
    return rows


def _yahoo_income_stmt_qtr_5_from_qs(ticker: str) -> List[Dict[str, Any]]:
    cache_key = _ck(f"yahoo:isq5:{ticker}")
    cached = get_json(cache_key)
    if cached and isinstance(cached, list):
        return cached

    _yahoo_bootstrap()
    url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
    data = _safe_get_json(url, params={"modules": "incomeStatementHistoryQuarterly"}, timeout=18)
    if not data:
        return []

    res = (((data.get("quoteSummary") or {}).get("result")) or [])
    if not res:
        return []

    ishq = (res[0] or {}).get("incomeStatementHistoryQuarterly") or {}
    stmts = ishq.get("incomeStatementHistory") or []
    if not isinstance(stmts, list) or not stmts:
        return []

    rows = []
    for s in stmts:
        if not isinstance(s, dict):
            continue
        end = s.get("endDate") or {}
        ts = _dig_raw(end.get("raw")) if isinstance(end, dict) else 0
        d = datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d") if ts else "Unknown"
        rev = _dig_raw(s.get("totalRevenue"))
        rows.append({"date": d, "revenue": float(rev) if rev else 0.0})

    rows = rows[:5]
    set_json(cache_key, rows, ttl_seconds=6 * 3600)
    return rows


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
# FMP Stable
# -------------------------
def _fmp_stable_get(endpoint: str, params: Dict[str, Any]):
    if not FMP_API_KEY:
        return None
    base = "https://financialmodelingprep.com/stable"
    p = dict(params or {})
    p["apikey"] = FMP_API_KEY
    return _safe_get_json(f"{base}/{endpoint}", params=p, timeout=22)


def _fmp_quote_stable(ticker: str) -> Optional[Dict[str, Any]]:
    data = _fmp_stable_get("quote", {"symbol": ticker})
    if not isinstance(data, list) or not data:
        return None

    q = data[0] if isinstance(data[0], dict) else {}
    price = _num(q.get("price"), 0.0)
    if not price:
        return None

    # FMP lastDiv is not reliably annual; keep it low priority compared to Yahoo QuoteSummary.
    last_div = _num(q.get("lastDiv"), 0.0)
    div_yield = ((last_div * 4.0) / price) * 100.0 if (price and last_div) else 0.0

    return {
        "symbol": ticker,
        "price": price,
        "market_cap": _num(q.get("marketCap"), 0.0),
        "high_52w": _num(q.get("yearHigh"), 0.0),
        "low_52w": _num(q.get("yearLow"), 0.0),
        "pe_trailing": _num(q.get("pe"), 0.0),
        "pe_forward": _num(q.get("forwardPE"), 0.0),
        "dividend_yield": _num(div_yield, 0.0),
        "price_to_book": _num(q.get("priceToBook"), 0.0),
    }


def _fmp_income_statement_quarterly_5q(ticker: str) -> Tuple[bool, List[Dict[str, Any]]]:
    inc = _fmp_stable_get("income-statement", {"symbol": ticker, "period": "quarter", "limit": 5})
    if isinstance(inc, list) and inc and isinstance(inc[0], dict):
        rows = [r for r in inc if isinstance(r, dict)]
        rows.sort(key=lambda r: _parse_ymd(r.get("date") or "") or datetime.min, reverse=True)
        return True, rows
    return False, []


def _fmp_income_statement_annual_2y(ticker: str) -> Tuple[bool, List[Dict[str, Any]]]:
    inc = _fmp_stable_get("income-statement", {"symbol": ticker, "period": "annual", "limit": 2})
    if isinstance(inc, list) and inc and isinstance(inc[0], dict):
        rows = [r for r in inc if isinstance(r, dict)]
        rows.sort(key=lambda r: _parse_ymd(r.get("date") or "") or datetime.min, reverse=True)
        return True, rows
    return False, []


def _fmp_balance_sheet_annual_1(ticker: str) -> Tuple[bool, Dict[str, Any]]:
    bs = _fmp_stable_get("balance-sheet-statement", {"symbol": ticker, "period": "annual", "limit": 1})
    if not (isinstance(bs, list) and bs and isinstance(bs[0], dict)):
        return False, {}

    r = bs[0]
    equity = _num(r.get("totalStockholdersEquity") or r.get("totalEquity") or 0, 0.0)

    total_debt = _num(r.get("totalDebt"), 0.0)
    if total_debt <= 0:
        total_debt = _num(r.get("shortTermDebt"), 0.0) + _num(r.get("longTermDebt"), 0.0)

    out = {"__equity": equity, "__total_debt": total_debt}
    if equity > 0 and total_debt > 0:
        out["debt_to_equity"] = total_debt / equity

    return True, out


def _fmp_forward_pe_from_analyst_estimates(ticker: str, price: float) -> float:
    if not price or not FMP_API_KEY:
        return 0.0

    cache_key = _ck(f"fmp:pefwd:{ticker}")
    cached = get_json(cache_key)
    if cached and isinstance(cached, dict):
        return _num(cached.get("pe_forward", 0.0))

    data = _fmp_stable_get(
        "analyst-estimates",
        {"symbol": ticker, "period": "annual", "page": 0, "limit": 6},
    )
    if not isinstance(data, list) or not data:
        return 0.0

    current_year = date.today().year
    best_eps = 0.0
    best_year = None

    for r in data:
        if not isinstance(r, dict):
            continue

        y = r.get("date") or r.get("year")
        yr = None
        if isinstance(y, int):
            yr = y
        elif isinstance(y, str):
            try:
                yr = int(y[:4])
            except Exception:
                yr = None

        eps = _num(
            r.get("estimatedEpsAvg")
            or r.get("estimatedEPSAvg")
            or r.get("epsEstimatedAverage")
            or r.get("epsAvg")
            or 0.0,
            0.0,
        )
        if eps <= 0:
            continue

        if yr is not None and yr >= current_year:
            if best_year is None or yr < best_year:
                best_year = yr
                best_eps = eps
        elif best_year is None and eps > 0:
            best_eps = eps

    if best_eps <= 0:
        return 0.0

    pe_fwd = float(price) / float(best_eps)
    set_json(cache_key, {"pe_forward": pe_fwd}, ttl_seconds=24 * 3600)
    return pe_fwd


def _compute_pe_from_eps_ttm(price: float, eps_history_5q: List[Dict[str, Any]]) -> float:
    if not price or not eps_history_5q:
        return 0.0
    eps_vals = [_num(r.get("eps"), 0.0) for r in eps_history_5q[:4]]
    if len(eps_vals) < 4:
        return 0.0
    eps_ttm = sum(eps_vals)
    return (float(price) / float(eps_ttm)) if eps_ttm else 0.0


def _enrich_growth_eps(ticker: str) -> Dict[str, Any]:
    cache_key = _ck(f"enrich:{ticker}")
    cached = get_json(cache_key)
    if cached:
        return cached

    out = {
        "revenue_growth_annual_yoy": 0.0,
        "revenue_growth_quarterly_yoy": 0.0,
        "eps_growth_annual_yoy": 0.0,
        "eps_growth_quarterly_yoy": 0.0,
        "eps_history_5q": [],
        "__fmp_q_ok": False,
        "__fmp_a_ok": False,
        "__y_eps_ok": False,
        "__y_rev_ok": False,
    }

    ok_q, rows_q = _fmp_income_statement_quarterly_5q(ticker)
    out["__fmp_q_ok"] = bool(ok_q)

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

    ok_a, rows_a = _fmp_income_statement_annual_2y(ticker)
    out["__fmp_a_ok"] = bool(ok_a)
    if ok_a and len(rows_a) >= 2:
        rev_now = _num(rows_a[0].get("revenue"), 0.0)
        rev_prev = _num(rows_a[1].get("revenue"), 0.0)
        if rev_prev:
            out["revenue_growth_annual_yoy"] = ((rev_now - rev_prev) / rev_prev) * 100.0

        eps_now = _num(rows_a[0].get("eps") or rows_a[0].get("epsDiluted") or 0, 0.0)
        eps_prev = _num(rows_a[1].get("eps") or rows_a[1].get("epsDiluted") or 0, 0.0)
        if eps_prev:
            out["eps_growth_annual_yoy"] = ((eps_now - eps_prev) / abs(eps_prev)) * 100.0

    # Yahoo EPS/revenue fallbacks (for symbols where FMP returns empty/rate-limited)
    if not out["eps_history_5q"]:
        y_eps = _yahoo_eps_history_5q_from_qs(ticker)
        if y_eps:
            out["eps_history_5q"] = y_eps[:5]
            out["__y_eps_ok"] = True
            if len(y_eps) >= 5:
                eps0 = _num(y_eps[0].get("eps"), 0.0)
                eps4 = _num(y_eps[4].get("eps"), 0.0)
                if eps4:
                    out["eps_growth_quarterly_yoy"] = ((eps0 - eps4) / abs(eps4)) * 100.0

    if _num(out.get("revenue_growth_quarterly_yoy"), 0.0) == 0.0:
        y_isq = _yahoo_income_stmt_qtr_5_from_qs(ticker)
        if y_isq and len(y_isq) >= 5:
            rev0 = _num(y_isq[0].get("revenue"), 0.0)
            rev4 = _num(y_isq[4].get("revenue"), 0.0)
            if rev4:
                out["revenue_growth_quarterly_yoy"] = ((rev0 - rev4) / rev4) * 100.0
                out["__y_rev_ok"] = True

    set_json(cache_key, out, ttl_seconds=12 * 3600)
    return out


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
    ticker = (ticker or "").upper().strip()

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
        "yahoo_qs_ok": False,
        "yahoo_v7v6_ok": False,
        "yahoo_meta_ok": False,
        "fmp_quote_ok": False,
        "fmp_bs_ok": False,
        "enrich_fmp_q_ok": False,
        "enrich_fmp_a_ok": False,
        "enrich_y_eps_ok": False,
        "enrich_y_rev_ok": False,
        "dividend_yield_source": "none",
        "pe_trailing_source": "none",
        "pe_forward_source": "none",
        "chart_source": None,
    }

    # Quote priority to match offline yfinance best:
    # 1) Yahoo QuoteSummary (closest to yfinance.info)
    # 2) Yahoo /v7 /v6 quote
    # 3) FMP stable quote
    # 4) Yahoo chart meta
    qs_q = _yahoo_quote_summary(ticker)
    yq = _yahoo_quote_v7v6(ticker)
    fmp_q = _fmp_quote_stable(ticker) if FMP_API_KEY else None
    meta_q = _yahoo_meta_quote(ticker)

    debug_info["yahoo_qs_ok"] = bool(qs_q)
    debug_info["yahoo_v7v6_ok"] = bool(yq)
    debug_info["fmp_quote_ok"] = bool(fmp_q)
    debug_info["yahoo_meta_ok"] = bool(meta_q)

    quote = None
    if qs_q:
        quote = dict(qs_q)
        debug_info["quote_source"] = "yahoo_qs"
        quote = _merge_fill_missing(quote, yq or {})
        quote = _merge_fill_missing(quote, fmp_q or {})
        quote = _merge_fill_missing(quote, meta_q or {})
    elif yq:
        quote = dict(yq)
        debug_info["quote_source"] = "yahoo_v7v6"
        quote = _merge_fill_missing(quote, qs_q or {})
        quote = _merge_fill_missing(quote, fmp_q or {})
        quote = _merge_fill_missing(quote, meta_q or {})
    elif fmp_q:
        quote = dict(fmp_q)
        debug_info["quote_source"] = "fmp"
        quote = _merge_fill_missing(quote, qs_q or {})
        quote = _merge_fill_missing(quote, yq or {})
        quote = _merge_fill_missing(quote, meta_q or {})
    elif meta_q:
        quote = dict(meta_q)
        debug_info["quote_source"] = "yahoo_meta"
    else:
        last_good = get_json(_ck(f"analysis:lastgood:{ticker}"))
        if last_good:
            last_good["stale"] = True
            if debug:
                last_good["debug"] = {"served": "lastgood", **debug_info}
            return last_good
        return None

    price = _num(quote.get("price"), 0.0)

    # Enrich growth/EPS (FMP first, Yahoo fallbacks)
    enrich = _enrich_growth_eps(ticker) if FMP_API_KEY else {
        "revenue_growth_annual_yoy": 0.0,
        "revenue_growth_quarterly_yoy": 0.0,
        "eps_growth_annual_yoy": 0.0,
        "eps_growth_quarterly_yoy": 0.0,
        "eps_history_5q": [],
        "__fmp_q_ok": False,
        "__fmp_a_ok": False,
        "__y_eps_ok": False,
        "__y_rev_ok": False,
    }

    debug_info["enrich_fmp_q_ok"] = bool(enrich.get("__fmp_q_ok"))
    debug_info["enrich_fmp_a_ok"] = bool(enrich.get("__fmp_a_ok"))
    debug_info["enrich_y_eps_ok"] = bool(enrich.get("__y_eps_ok"))
    debug_info["enrich_y_rev_ok"] = bool(enrich.get("__y_rev_ok"))

    funds = _merge_fill_missing(quote, {k: v for k, v in enrich.items() if not str(k).startswith("__")})

    # Balance sheet => D/E + compute P/B if missing
    if FMP_API_KEY:
        bs_ok, bs_calc = _fmp_balance_sheet_annual_1(ticker)
        debug_info["fmp_bs_ok"] = bool(bs_ok)
        funds = _merge_fill_missing(funds, bs_calc)

        equity = _num(bs_calc.get("__equity"), 0.0)
        mcap = _num(funds.get("market_cap"), 0.0)
        if equity > 0 and mcap > 0 and _num(funds.get("price_to_book"), 0.0) <= 0:
            funds["price_to_book"] = mcap / equity

    # Trailing PE: upstream else EPS TTM
    if _num(funds.get("pe_trailing"), 0.0) > 0:
        debug_info["pe_trailing_source"] = "upstream"
    else:
        pe_calc = _compute_pe_from_eps_ttm(price, funds.get("eps_history_5q") or [])
        if pe_calc > 0:
            funds["pe_trailing"] = pe_calc
            debug_info["pe_trailing_source"] = "eps_ttm"

    # Dividend yield: upstream else Yahoo dividend events trailing
    if _num(funds.get("dividend_yield"), 0.0) > 0:
        debug_info["dividend_yield_source"] = "upstream"
    else:
        _, yld = _yahoo_dividend_yield_from_events(ticker, price)
        if yld > 0:
            funds["dividend_yield"] = yld
            debug_info["dividend_yield_source"] = "yahoo_events_trailing"

    # Forward PE: upstream else FMP analyst estimates
    if _num(funds.get("pe_forward"), 0.0) > 0:
        debug_info["pe_forward_source"] = "upstream"
    else:
        pe_fwd = _fmp_forward_pe_from_analyst_estimates(ticker, price)
        if pe_fwd > 0:
            funds["pe_forward"] = pe_fwd
            debug_info["pe_forward_source"] = "fmp_analyst_estimates"

    # Chart cache 6h
    chart_key = _ck(f"chart:{ticker}")
    chart = get_json(chart_key)
    if chart:
        debug_info["chart_source"] = "cache"
    else:
        chart = _yahoo_chart_5y_monthly(ticker)
        debug_info["chart_source"] = "yahoo" if chart else "none"
        if not chart:
            chart = {"candles": [], "global_high": None, "global_low": None, "globalhigh": None, "globallow": None}
        set_json(chart_key, chart, ttl_seconds=6 * 3600)

    # Ensure keys exist
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

    # Frontend expects legacy keys too. [file:93]
    fundamentals_clean = {k: v for k, v in funds.items() if not str(k).startswith("__")}
    fundamentals_clean.update(
        {
            "marketcap": fundamentals_clean.get("market_cap", 0),
            "high52w": fundamentals_clean.get("high_52w", 0),
            "low52w": fundamentals_clean.get("low_52w", 0),
            "petrailing": fundamentals_clean.get("pe_trailing", 0),
            "peforward": fundamentals_clean.get("pe_forward", 0),
            "dividendyield": fundamentals_clean.get("dividend_yield", 0),
            "pricetobook": fundamentals_clean.get("price_to_book", 0),
            "debttoequity": fundamentals_clean.get("debt_to_equity", 0),
            "revenuegrowthannualyoy": fundamentals_clean.get("revenue_growth_annual_yoy", 0),
            "revenuegrowthquarterlyyoy": fundamentals_clean.get("revenue_growth_quarterly_yoy", 0),
            "epsgrowthannualyoy": fundamentals_clean.get("eps_growth_annual_yoy", 0),
            "epsgrowthquarterlyyoy": fundamentals_clean.get("eps_growth_quarterly_yoy", 0),
            "epshistory5q": fundamentals_clean.get("eps_history_5q", []),
        }
    )

    out = {"ticker": ticker, "fundamentals": fundamentals_clean, "chart": chart, "score": score}
    if debug:
        out["debug"] = debug_info

    set_json(main_key, out, ttl_seconds=5 * 60)
    set_json(_ck(f"analysis:lastgood:{ticker}"), out, ttl_seconds=7 * 24 * 3600)
    return out
