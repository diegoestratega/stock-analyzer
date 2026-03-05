import os
import time
import requests
from datetime import datetime, date
from typing import Optional, Dict, Any, List, Tuple

from cache_upstash import get_json, set_json
from scoring import StockScorer

CACHE_VERSION = "v8"  # bump to bust cache

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


# -------------------------
# Yahoo meta quote (via chart meta)
# -------------------------
def _yahoo_meta_quote(ticker: str) -> Optional[Dict[str, Any]]:
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

    price = _num(meta.get("regularMarketPrice") or meta.get("chartPreviousClose") or 0, 0.0)

    out = {
        "symbol": ticker,
        "price": price,
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

    last_div = _num(q.get("lastDiv"), 0.0)
    dividend_yield = ((last_div * 4.0) / price) * 100.0 if (price and last_div) else 0.0

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

    out = {"__bs_ok": True, "__equity": equity, "__total_debt": total_debt}

    if equity > 0 and total_debt > 0:
        out["debt_to_equity"] = total_debt / equity

    return True, out


def _compute_pe_from_eps_ttm(price: float, eps_history_5q: List[Dict[str, Any]]) -> float:
    if not price or not eps_history_5q:
        return 0.0
    eps_vals = []
    for r in eps_history_5q[:4]:
        eps_vals.append(_num(r.get("eps"), 0.0))
    if len(eps_vals) < 4:
        return 0.0
    eps_ttm = sum(eps_vals)
    if eps_ttm == 0:
        return 0.0
    return float(price) / float(eps_ttm)


def _fmp_enrich_5q(ticker: str, price: float) -> Dict[str, Any]:
    cache_key = _ck(f"fmp:enrich:{ticker}")
    cached = get_json(cache_key)
    if cached:
        return cached

    out = {
        "revenue_growth_annual_yoy": 0.0,
        "revenue_growth_quarterly_yoy": 0.0,
        "eps_growth_annual_yoy": 0.0,
        "eps_growth_quarterly_yoy": 0.0,
        "eps_history_5q": [],
        "__q_ok": False,
        "__a_ok": False,
    }

    ok_q, rows_q = _fmp_income_statement_quarterly_5q(ticker)
    out["__q_ok"] = bool(ok_q)

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
    out["__a_ok"] = bool(ok_a)
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
        "fmp_quote_ok": None,
        "yahoo_meta_ok": None,
        "enrich_source": None,
        "fmp_q_ok": None,
        "fmp_a_ok": None,
        "fmp_bs_ok": None,
        "pe_from_eps_ttm_used": False,
        "chart_source": None,
    }

    fmp_q = _fmp_quote_stable(ticker) if FMP_API_KEY else None
    yahoo_meta = _yahoo_meta_quote(ticker)

    debug_info["fmp_quote_ok"] = bool(fmp_q)
    debug_info["yahoo_meta_ok"] = bool(yahoo_meta)

    if fmp_q:
        quote = dict(fmp_q)
        debug_info["quote_source"] = "fmp"
        if yahoo_meta:
            quote = _merge_fill_missing(quote, yahoo_meta)
    elif yahoo_meta:
        quote = dict(yahoo_meta)
        debug_info["quote_source"] = "yahoo_meta"
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
    debug_info["fmp_a_ok"] = bool(enrich.get("__a_ok"))

    funds = _merge_fill_missing(quote, {k: v for k, v in enrich.items() if not str(k).startswith("__")})

    # Balance-sheet derived DE + PB
    bs_calc = {}
    if FMP_API_KEY:
        bs_ok, bs_calc = _fmp_balance_sheet_annual_1(ticker)
        debug_info["fmp_bs_ok"] = bool(bs_ok)
        funds = _merge_fill_missing(funds, bs_calc)

        equity = _num(bs_calc.get("__equity"), 0.0)
        mcap = _num(funds.get("market_cap"), 0.0)
        if equity > 0 and mcap > 0 and _num(funds.get("price_to_book"), 0.0) <= 0:
            funds["price_to_book"] = mcap / equity
    else:
        debug_info["fmp_bs_ok"] = False

    # Compute trailing PE from EPS TTM if still missing
    if _num(funds.get("pe_trailing"), 0.0) <= 0:
        pe_calc = _compute_pe_from_eps_ttm(_num(funds.get("price"), 0.0), funds.get("eps_history_5q") or [])
        if pe_calc > 0:
            funds["pe_trailing"] = pe_calc
            debug_info["pe_from_eps_ttm_used"] = True

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

    # Strip internal __* fields from fundamentals output
    fundamentals_clean = {k: v for k, v in funds.items() if not str(k).startswith("__")}

    # Legacy compatibility keys for frontend
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
        # Put BS internals in debug only (useful for validation)
        debug_info["bs_equity"] = bs_calc.get("__equity") if isinstance(bs_calc, dict) else None
        debug_info["bs_total_debt"] = bs_calc.get("__total_debt") if isinstance(bs_calc, dict) else None
        out["debug"] = debug_info

    set_json(main_key, out, ttl_seconds=5 * 60)
    set_json(_ck(f"analysis:lastgood:{ticker}"), out, ttl_seconds=7 * 24 * 3600)
    return out
