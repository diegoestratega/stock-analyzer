import os
import time
import random
import requests
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

from cache_upstash import get_json, set_json
from scoring import StockScorer

CACHE_VERSION = "v20"

ALPHAVANTAGE_API_KEY = (os.getenv("ALPHAVANTAGE_API_KEY") or os.getenv("ALPHA_VANTAGE_API_KEY") or "").strip()
LOG_UPSTREAM = (os.getenv("LOG_UPSTREAM") or "0").strip() == "1"
AV_MIN_INTERVAL_SEC = 1.1

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
_last_av_call_ts = 0.0


def _ck(key: str) -> str:
    return f"{CACHE_VERSION}:{key}"


def _num(x, dflt=0.0) -> float:
    try:
        if x is None:
            return dflt
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x).strip()
        if s in ("", "None", "null", "NaN", "-", "N/A"):
            return dflt
        return float(s)
    except Exception:
        return dflt


def _has_pos(x) -> bool:
    try:
        return x is not None and float(x) > 0
    except Exception:
        return False


def _pct_from_frac(v: float) -> float:
    v = _num(v, 0.0)
    if v <= 0:
        return 0.0
    return v * 100.0 if v < 1 else v


def _sleep_jitter(attempt: int, cap: float = 12.0):
    time.sleep(min(cap, (2 ** attempt) + 0.5 + random.random()))


def _redact_url(u: str) -> str:
    if not u:
        return u
    for key in ("apikey=", "apiKey=", "token=", "key="):
        if key in u:
            parts = u.split(key)
            if len(parts) >= 2:
                tail = parts[1]
                if "&" in tail:
                    tail = tail.split("&", 1)[1]
                    return parts[0] + key + "REDACTED&" + tail
                return parts[0] + key + "REDACTED"
    return u


def _safe_get_json(url: str, params=None, timeout=22) -> Optional[Any]:
    for attempt in range(4):
        try:
            r = sess.get(url, params=params, timeout=timeout, allow_redirects=True)

            if LOG_UPSTREAM:
                print("HTTP", r.status_code, r.request.method, _redact_url(r.url))

            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    try:
                        print("JSON_ERR", _redact_url(r.url), (r.text or "")[:200])
                    except Exception:
                        pass
                    return None

            try:
                print("HTTP_ERR", r.status_code, r.request.method, _redact_url(r.url), (r.text or "")[:200])
            except Exception:
                pass

            if r.status_code in (429, 500, 502, 503, 504):
                _sleep_jitter(attempt)
                continue

            return None
        except Exception:
            _sleep_jitter(attempt)

    return None


# -------------------------
# Yahoo
# -------------------------
def _yahoo_meta_quote(ticker: str) -> Optional[Dict[str, Any]]:
    cache_key = _ck(f"yahoo:meta:{ticker}")
    cached = get_json(cache_key)
    if cached:
        return cached

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    data = _safe_get_json(url, params={"range": "1d", "interval": "1d"}, timeout=18)
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
        "high_52w": _num(meta.get("fiftyTwoWeekHigh"), 0.0),
        "low_52w": _num(meta.get("fiftyTwoWeekLow"), 0.0),
    }

    set_json(cache_key, out, ttl_seconds=60)
    return out


def _yahoo_quote_snapshot(ticker: str) -> Optional[Dict[str, Any]]:
    cache_key = _ck(f"yahoo:quote:{ticker}")
    cached = get_json(cache_key)
    if cached:
        return cached

    for base in (
        "https://query1.finance.yahoo.com/v7/finance/quote",
        "https://query1.finance.yahoo.com/v6/finance/quote",
    ):
        data = _safe_get_json(base, params={"symbols": ticker}, timeout=18)
        if not data:
            continue

        res = ((data.get("quoteResponse") or {}).get("result")) or []
        if not res:
            continue

        q = res[0] or {}
        price = q.get("regularMarketPrice") or q.get("postMarketPrice") or q.get("preMarketPrice") or 0
        div_yield = q.get("dividendYield") or 0
        if div_yield and div_yield < 1:
            div_yield = div_yield * 100

        out = {
            "symbol": ticker,
            "price": _num(price, 0.0),
            "market_cap": _num(q.get("marketCap"), 0.0),
            "high_52w": _num(q.get("fiftyTwoWeekHigh"), 0.0),
            "low_52w": _num(q.get("fiftyTwoWeekLow"), 0.0),
            "pe_trailing": _num(q.get("trailingPE"), 0.0),
            "pe_forward": _num(q.get("forwardPE"), 0.0),
            "dividend_yield": _num(div_yield, 0.0),
            "price_to_book": _num(q.get("priceToBook"), 0.0),
        }

        set_json(cache_key, out, ttl_seconds=300)
        return out

    return None


def _yahoo_chart_5y_monthly(ticker: str) -> Optional[Dict[str, Any]]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    data = _safe_get_json(url, params={"range": "5y", "interval": "1mo"}, timeout=18)
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

    by_month: Dict[str, Dict[str, Any]] = {}
    global_high = None
    global_low = None

    n = min(len(ts), len(opens), len(highs), len(lows), len(closes))
    for i in range(n):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        if o is None or h is None or l is None or c is None:
            continue

        d = datetime.utcfromtimestamp(ts[i])
        ym = f"{d.year:04d}-{d.month:02d}"

        o = _num(o)
        h = _num(h)
        l = _num(l)
        c = _num(c)

        if ym not in by_month:
            by_month[ym] = {"date": ym, "open": o, "high": h, "low": l, "close": c}
        else:
            by_month[ym]["high"] = max(by_month[ym]["high"], h)
            by_month[ym]["low"] = min(by_month[ym]["low"], l)
            by_month[ym]["close"] = c

    candles = [by_month[k] for k in sorted(by_month.keys())]
    if not candles:
        return None

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


# -------------------------
# Alpha Vantage
# -------------------------
def _av_throttle():
    global _last_av_call_ts
    now = time.time()
    wait = (_last_av_call_ts + AV_MIN_INTERVAL_SEC) - now
    if wait > 0:
        time.sleep(wait)
    _last_av_call_ts = time.time()


def _av_get(function_name: str, ticker: str, ttl_seconds: int) -> Tuple[Optional[Dict[str, Any]], str]:
    if not ALPHAVANTAGE_API_KEY:
        return None, "missing_key"

    ticker = (ticker or "").upper().strip()
    cache_key = _ck(f"av:{function_name}:{ticker}")
    cached = get_json(cache_key)
    if isinstance(cached, dict) and cached:
        return cached, "cache"

    _av_throttle()

    url = "https://www.alphavantage.co/query"
    params = {"function": function_name, "symbol": ticker, "apikey": ALPHAVANTAGE_API_KEY}
    data = _safe_get_json(url, params=params, timeout=25)

    if not isinstance(data, dict) or not data:
        return None, "empty"

    if "Note" in data:
        return None, "note:" + str(data.get("Note"))[:160]
    if "Information" in data:
        return None, "info:" + str(data.get("Information"))[:160]
    if "Error Message" in data:
        return None, "error:" + str(data.get("Error Message"))[:160]

    set_json(cache_key, data, ttl_seconds=ttl_seconds)
    return data, "ok"


def _av_overview_parsed(ticker: str) -> Tuple[Dict[str, Any], str]:
    data, status = _av_get("OVERVIEW", ticker, ttl_seconds=24 * 3600)
    if not data:
        return {}, status

    out = {
        "market_cap": _num(data.get("MarketCapitalization"), 0.0),
        "pe_trailing": _num(data.get("PERatio"), 0.0),
        "pe_forward": _num(data.get("ForwardPE"), 0.0),
        "price_to_book": _num(data.get("PriceToBookRatio"), 0.0),
        "dividend_yield": _pct_from_frac(_num(data.get("DividendYield"), 0.0)),
        "debt_to_equity": _num(data.get("DebtToEquity"), 0.0),
        "revenue_growth_quarterly_yoy": _num(data.get("QuarterlyRevenueGrowthYOY"), 0.0),
        "eps_growth_quarterly_yoy": _num(data.get("QuarterlyEarningsGrowthYOY"), 0.0),
    }
    return out, status


def _av_balance_sheet_debt_to_equity(ticker: str) -> Tuple[Optional[float], str]:
    data, status = _av_get("BALANCE_SHEET", ticker, ttl_seconds=24 * 3600)
    if not data:
        return None, status

    rows = data.get("quarterlyReports")
    if not isinstance(rows, list) or not rows:
        return None, "bad_shape"

    rows = [r for r in rows if isinstance(r, dict)]
    rows.sort(key=lambda r: (r.get("fiscalDateEnding") or ""), reverse=True)
    r = rows[0] if rows else {}

    equity = _num(r.get("totalShareholderEquity"), 0.0)
    if equity <= 0:
        return None, "no_equity"

    debt_candidates = [
        _num(r.get("shortLongTermDebtTotal"), 0.0),
        _num(r.get("shortTermDebt"), 0.0) + _num(r.get("longTermDebt"), 0.0),
        _num(r.get("currentLongTermDebt"), 0.0) + _num(r.get("longTermDebt"), 0.0),
    ]
    total_debt = max(debt_candidates) if debt_candidates else 0.0
    if total_debt <= 0:
        return 0.0, "ok"

    return total_debt / equity, "ok"


def _av_eps_history_5q(ticker: str) -> Tuple[List[Dict[str, Any]], str]:
    data, status = _av_get("EARNINGS", ticker, ttl_seconds=12 * 3600)
    if not data:
        return [], status

    rows = data.get("quarterlyEarnings")
    if not isinstance(rows, list):
        return [], "bad_shape"

    rows = [r for r in rows if isinstance(r, dict)]
    rows.sort(key=lambda r: (r.get("fiscalDateEnding") or ""), reverse=True)

    out: List[Dict[str, Any]] = []
    for r in rows[:5]:
        d = (r.get("fiscalDateEnding") or "Unknown")[:10]
        eps = _num(r.get("reportedEPS"), 0.0)
        out.append({"date": d, "eps": eps})
    return out, "ok"


def _av_income_growths(ticker: str) -> Tuple[Dict[str, Any], str]:
    data, status = _av_get("INCOME_STATEMENT", ticker, ttl_seconds=24 * 3600)
    if not data:
        return {}, status

    rows = data.get("quarterlyReports")
    if not isinstance(rows, list):
        return {}, "bad_shape"

    rows = [r for r in rows if isinstance(r, dict)]
    rows.sort(key=lambda r: (r.get("fiscalDateEnding") or ""), reverse=True)

    out = {
        "revenue_growth_quarterly_yoy": None,
        "revenue_growth_annual_yoy": None,
    }

    if len(rows) >= 5:
        rev0 = _num(rows[0].get("totalRevenue"), 0.0)
        rev4 = _num(rows[4].get("totalRevenue"), 0.0)
        if rev4 != 0:
            out["revenue_growth_quarterly_yoy"] = (rev0 - rev4) / abs(rev4)

    if len(rows) >= 8:
        rev_ttm_1 = sum(_num(rows[i].get("totalRevenue"), 0.0) for i in range(4))
        rev_ttm_2 = sum(_num(rows[i].get("totalRevenue"), 0.0) for i in range(4, 8))
        if rev_ttm_2 != 0:
            out["revenue_growth_annual_yoy"] = (rev_ttm_1 - rev_ttm_2) / abs(rev_ttm_2)

    return out, "ok"


def _av_earnings_growths(ticker: str) -> Tuple[Dict[str, Any], str]:
    data, status = _av_get("EARNINGS", ticker, ttl_seconds=12 * 3600)
    if not data:
        return {}, status

    rows = data.get("quarterlyEarnings")
    if not isinstance(rows, list):
        return {}, "bad_shape"

    rows = [r for r in rows if isinstance(r, dict)]
    rows.sort(key=lambda r: (r.get("fiscalDateEnding") or ""), reverse=True)

    out = {
        "eps_growth_quarterly_yoy": None,
        "eps_growth_annual_yoy": None,
    }

    eps_vals = [_num(r.get("reportedEPS"), 0.0) for r in rows]

    if len(eps_vals) >= 5:
        eps0 = eps_vals[0]
        eps4 = eps_vals[4]
        if eps4 != 0:
            out["eps_growth_quarterly_yoy"] = (eps0 - eps4) / abs(eps4)

    if len(eps_vals) >= 8:
        eps_ttm_1 = sum(eps_vals[i] for i in range(4))
        eps_ttm_2 = sum(eps_vals[i] for i in range(4, 8))
        if eps_ttm_2 != 0:
            out["eps_growth_annual_yoy"] = (eps_ttm_1 - eps_ttm_2) / abs(eps_ttm_2)

    return out, "ok"


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
        "av_key_present": bool(ALPHAVANTAGE_API_KEY),
        "yahoo_meta_ok": False,
        "yahoo_quote_ok": False,
        "av_overview_status": None,
        "av_balance_status": None,
        "av_income_status": None,
        "av_earnings_status": None,
        "chart_source": None,
    }

    meta = _yahoo_meta_quote(ticker)
    quote = _yahoo_quote_snapshot(ticker)

    debug_info["yahoo_meta_ok"] = bool(meta)
    debug_info["yahoo_quote_ok"] = bool(quote)

    ov, ov_status = _av_overview_parsed(ticker)
    debug_info["av_overview_status"] = ov_status

    if not meta and not quote and not ov:
        last_good = get_json(_ck(f"analysis:lastgood:{ticker}"))
        if last_good:
            last_good["stale"] = True
            if debug:
                last_good["debug"] = {"served": "lastgood", **debug_info}
            return last_good
        return None

    debt_to_equity = _num(ov.get("debt_to_equity"), 0.0)
    bal_status = "skipped"
    if not _has_pos(debt_to_equity):
        de_fallback, bal_status = _av_balance_sheet_debt_to_equity(ticker)
        if de_fallback is not None:
            debt_to_equity = _num(de_fallback, 0.0)
    debug_info["av_balance_status"] = bal_status

    income_growths, income_status = _av_income_growths(ticker)
    debug_info["av_income_status"] = income_status

    eps_hist, earn_status = _av_eps_history_5q(ticker)
    eps_growths, earn_growth_status = _av_earnings_growths(ticker)
    debug_info["av_earnings_status"] = earn_status if earn_status == earn_growth_status else f"{earn_status}|{earn_growth_status}"

    if not eps_hist and str(earn_status).startswith(("info:", "note:")):
        last_good = get_json(_ck(f"analysis:lastgood:{ticker}"))
        if isinstance(last_good, dict):
            try:
                eps_hist = (last_good.get("fundamentals") or {}).get("epshistory5q") or []
            except Exception:
                eps_hist = []

    rev_qtr = income_growths.get("revenue_growth_quarterly_yoy")
    if rev_qtr is None:
        rev_qtr = ov.get("revenue_growth_quarterly_yoy")

    eps_qtr = eps_growths.get("eps_growth_quarterly_yoy")
    if eps_qtr is None:
        eps_qtr = ov.get("eps_growth_quarterly_yoy")

    fundamentals: Dict[str, Any] = {
        "symbol": ticker,
        "price": _num((meta or {}).get("price") or (quote or {}).get("price"), 0.0),
        "high_52w": _num((meta or {}).get("high_52w") or (quote or {}).get("high_52w"), 0.0),
        "low_52w": _num((meta or {}).get("low_52w") or (quote or {}).get("low_52w"), 0.0),

        "market_cap": _num(ov.get("market_cap") if _has_pos(ov.get("market_cap")) else (quote or {}).get("market_cap"), 0.0),
        "pe_trailing": _num(ov.get("pe_trailing") if _has_pos(ov.get("pe_trailing")) else (quote or {}).get("pe_trailing"), 0.0),
        "pe_forward": _num(ov.get("pe_forward") if _has_pos(ov.get("pe_forward")) else (quote or {}).get("pe_forward"), 0.0),
        "price_to_book": _num(ov.get("price_to_book") if _has_pos(ov.get("price_to_book")) else (quote or {}).get("price_to_book"), 0.0),
        "dividend_yield": _num(ov.get("dividend_yield") if _has_pos(ov.get("dividend_yield")) else (quote or {}).get("dividend_yield"), 0.0),

        "debt_to_equity": _num(debt_to_equity, 0.0),
        "revenue_growth_quarterly_yoy": rev_qtr,
        "eps_growth_quarterly_yoy": eps_qtr,
        "revenue_growth_annual_yoy": income_growths.get("revenue_growth_annual_yoy"),
        "eps_growth_annual_yoy": eps_growths.get("eps_growth_annual_yoy"),
        "eps_history_5q": eps_hist,
    }

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

    score = scorer.evaluate(_for_scoring(fundamentals))

    fundamentals_clean = dict(fundamentals)
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
            "revenuegrowthannualyoy": fundamentals_clean.get("revenue_growth_annual_yoy"),
            "revenuegrowthquarterlyyoy": fundamentals_clean.get("revenue_growth_quarterly_yoy"),
            "epsgrowthannualyoy": fundamentals_clean.get("eps_growth_annual_yoy"),
            "epsgrowthquarterlyyoy": fundamentals_clean.get("eps_growth_quarterly_yoy"),
            "epshistory5q": fundamentals_clean.get("eps_history_5q", []),
        }
    )

    out = {"ticker": ticker, "fundamentals": fundamentals_clean, "chart": chart, "score": score}
    if debug:
        out["debug"] = debug_info

    set_json(main_key, out, ttl_seconds=5 * 60)
    set_json(_ck(f"analysis:lastgood:{ticker}"), out, ttl_seconds=7 * 24 * 3600)
    return out
