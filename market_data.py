import os
import time
import random
import requests
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List, Tuple

from cache_upstash import get_json, set_json
from scoring import StockScorer

CACHE_VERSION = "v15"

ALPHAVANTAGE_API_KEY = (os.getenv("ALPHAVANTAGE_API_KEY") or os.getenv("ALPHA_VANTAGE_API_KEY") or "").strip()

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

LOG_UPSTREAM = (os.getenv("LOG_UPSTREAM") or "0").strip() == "1"

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
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x).strip()
        if s in ("", "None", "null", "NaN", "-"):
            return dflt
        return float(s)
    except Exception:
        return dflt


def _pct_from_frac(v: float) -> float:
    v = _num(v, 0.0)
    if v <= 0:
        return 0.0
    return v * 100.0 if v < 1 else v


def _sleep_jitter(attempt: int, cap: float = 12.0):
    time.sleep(min(cap, (2**attempt) + 0.5 + random.random()))


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
                except Exception as e:
                    print("JSON_ERR", type(e).__name__, _redact_url(url))
                    try:
                        print("BODY200", (r.text or "")[:200])
                    except Exception:
                        pass
                    return None

            print("HTTP_ERR", r.status_code, r.request.method, _redact_url(r.url))
            try:
                print("BODY", (r.text or "")[:200])
            except Exception:
                pass

            if r.status_code in (429, 500, 502, 503, 504):
                _sleep_jitter(attempt)
                continue
            return None
        except Exception as e:
            print("REQ_ERR", type(e).__name__, _redact_url(url))
            _sleep_jitter(attempt)

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
# Yahoo chart (works for you)
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
        "market_cap": _num(meta.get("marketCap"), 0.0),  # may be missing; we use AV for this
        "high_52w": _num(meta.get("fiftyTwoWeekHigh"), 0.0),
        "low_52w": _num(meta.get("fiftyTwoWeekLow"), 0.0),
    }

    set_json(cache_key, out, ttl_seconds=60)
    return out


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

    candles: List[Dict[str, Any]] = []
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
        "globalhigh": global_high,  # backward compat
        "globallow": global_low,    # backward compat
    }


# -------------------------
# Alpha Vantage fundamentals
# -------------------------
def _av_get(function_name: str, ticker: str, ttl_seconds: int) -> Optional[Dict[str, Any]]:
    if not ALPHAVANTAGE_API_KEY:
        return None

    ticker = (ticker or "").upper().strip()
    cache_key = _ck(f"av:{function_name}:{ticker}")
    cached = get_json(cache_key)
    if cached:
        return cached

    url = "https://www.alphavantage.co/query"
    params = {"function": function_name, "symbol": ticker, "apikey": ALPHAVANTAGE_API_KEY}
    data = _safe_get_json(url, params=params, timeout=25)

    # Alpha Vantage returns rate-limit messages inside JSON sometimes.
    if isinstance(data, dict):
        if "Note" in data or "Information" in data:
            print("AV_LIMIT", function_name, ticker, str(data.get("Note") or data.get("Information"))[:140])
            return None

    if not isinstance(data, dict) or not data:
        return None

    set_json(cache_key, data, ttl_seconds=ttl_seconds)
    return data


def _av_overview_fields(ticker: str) -> Dict[str, Any]:
    data = _av_get("OVERVIEW", ticker, ttl_seconds=24 * 3600)
    if not data:
        return {}

    return {
        "market_cap": _num(data.get("MarketCapitalization"), 0.0),
        "pe_trailing": _num(data.get("PERatio"), 0.0),
        "price_to_book": _num(data.get("PriceToBookRatio"), 0.0),
        "dividend_yield": _pct_from_frac(_num(data.get("DividendYield"), 0.0)),
    }


def _av_income_quarterly(ticker: str) -> List[Dict[str, Any]]:
    data = _av_get("INCOME_STATEMENT", ticker, ttl_seconds=12 * 3600)
    if not data:
        return []
    rows = data.get("quarterlyReports")
    if not isinstance(rows, list):
        return []
    rows = [r for r in rows if isinstance(r, dict)]
    rows.sort(key=lambda r: (r.get("fiscalDateEnding") or ""), reverse=True)
    return rows


def _av_balance_quarterly(ticker: str) -> List[Dict[str, Any]]:
    data = _av_get("BALANCE_SHEET", ticker, ttl_seconds=24 * 3600)
    if not data:
        return []
    rows = data.get("quarterlyReports")
    if not isinstance(rows, list):
        return []
    rows = [r for r in rows if isinstance(r, dict)]
    rows.sort(key=lambda r: (r.get("fiscalDateEnding") or ""), reverse=True)
    return rows


def _av_earnings_quarterly(ticker: str) -> List[Dict[str, Any]]:
    data = _av_get("EARNINGS", ticker, ttl_seconds=12 * 3600)
    if not data:
        return []
    rows = data.get("quarterlyEarnings")
    if not isinstance(rows, list):
        return []
    rows = [r for r in rows if isinstance(r, dict)]
    rows.sort(key=lambda r: (r.get("fiscalDateEnding") or ""), reverse=True)
    return rows


def _compute_debt_to_equity(balance_rows: List[Dict[str, Any]]) -> float:
    if not balance_rows:
        return 0.0
    r0 = balance_rows[0] or {}

    equity = _num(r0.get("totalShareholderEquity"), 0.0)
    if equity <= 0:
        return 0.0

    debt = _num(r0.get("shortLongTermDebtTotal"), 0.0)
    if debt <= 0:
        debt = _num(r0.get("shortTermDebt"), 0.0) + _num(r0.get("longTermDebt"), 0.0)

    # fallback if debt fields are missing: use total liabilities
    if debt <= 0:
        debt = _num(r0.get("totalLiabilities"), 0.0)

    return (debt / equity) if debt > 0 else 0.0


def _compute_quarterly_yoy_growth(latest: float, year_ago: float) -> float:
    if year_ago == 0:
        return 0.0
    return ((latest - year_ago) / abs(year_ago)) * 100.0


def _enrich_growth_eps(ticker: str) -> Dict[str, Any]:
    out = {
        "revenue_growth_annual_yoy": 0.0,
        "revenue_growth_quarterly_yoy": 0.0,
        "eps_growth_annual_yoy": 0.0,
        "eps_growth_quarterly_yoy": 0.0,
        "eps_history_5q": [],
    }

    inc = _av_income_quarterly(ticker)
    earn = _av_earnings_quarterly(ticker)

    # Revenue growth quarterly YoY from income statement (q0 vs q4)
    if len(inc) >= 5:
        rev0 = _num(inc[0].get("totalRevenue"), 0.0)
        rev4 = _num(inc[4].get("totalRevenue"), 0.0)
        out["revenue_growth_quarterly_yoy"] = _compute_quarterly_yoy_growth(rev0, rev4)

    # EPS history + EPS growth quarterly YoY from earnings endpoint
    eps_hist: List[Dict[str, Any]] = []
    if earn:
        for r in earn[:5]:
            d = (r.get("fiscalDateEnding") or r.get("reportedDate") or "Unknown")[:10]
            eps = _num(r.get("reportedEPS"), 0.0)
            eps_hist.append({"date": d, "eps": eps})
        out["eps_history_5q"] = eps_hist

        if len(earn) >= 5:
            eps0 = _num(earn[0].get("reportedEPS"), 0.0)
            eps4 = _num(earn[4].get("reportedEPS"), 0.0)
            out["eps_growth_quarterly_yoy"] = _compute_quarterly_yoy_growth(eps0, eps4)

    # “Annual YoY” (TTM vs prior TTM) — optional; keep simple if we have 8 quarters
    if len(inc) >= 8:
        rev_ttm1 = sum(_num(inc[i].get("totalRevenue"), 0.0) for i in range(0, 4))
        rev_ttm2 = sum(_num(inc[i].get("totalRevenue"), 0.0) for i in range(4, 8))
        out["revenue_growth_annual_yoy"] = _compute_quarterly_yoy_growth(rev_ttm1, rev_ttm2)

    if len(earn) >= 8:
        eps_ttm1 = sum(_num(earn[i].get("reportedEPS"), 0.0) for i in range(0, 4))
        eps_ttm2 = sum(_num(earn[i].get("reportedEPS"), 0.0) for i in range(4, 8))
        out["eps_growth_annual_yoy"] = _compute_quarterly_yoy_growth(eps_ttm1, eps_ttm2)

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
        "av_key_present": bool(ALPHAVANTAGE_API_KEY),
        "yahoo_meta_ok": False,
        "av_overview_ok": False,
        "av_income_ok": False,
        "av_balance_ok": False,
        "av_earnings_ok": False,
        "chart_source": None,
    }

    # Price + 52w from Yahoo chart meta (this endpoint has been working for you)
    meta = _yahoo_meta_quote(ticker)
    debug_info["yahoo_meta_ok"] = bool(meta)

    # Alpha Vantage for fundamentals
    overview = _av_overview_fields(ticker)
    debug_info["av_overview_ok"] = bool(overview)

    bal = _av_balance_quarterly(ticker)
    debug_info["av_balance_ok"] = bool(bal)

    inc = _av_income_quarterly(ticker)
    debug_info["av_income_ok"] = bool(inc)

    earn = _av_earnings_quarterly(ticker)
    debug_info["av_earnings_ok"] = bool(earn)

    # If we have neither Yahoo meta nor AV overview, we can’t proceed
    if not meta and not overview:
        last_good = get_json(_ck(f"analysis:lastgood:{ticker}"))
        if last_good:
            last_good["stale"] = True
            if debug:
                last_good["debug"] = {"served": "lastgood", **debug_info}
            return last_good
        return None

    fundamentals: Dict[str, Any] = {
        "symbol": ticker,
        "price": _num((meta or {}).get("price"), 0.0),
        "high_52w": _num((meta or {}).get("high_52w"), 0.0),
        "low_52w": _num((meta or {}).get("low_52w"), 0.0),
        "market_cap": _num((overview or {}).get("market_cap"), 0.0),
        "pe_trailing": _num((overview or {}).get("pe_trailing"), 0.0),
        "pe_forward": 0.0,
        "price_to_book": _num((overview or {}).get("price_to_book"), 0.0),
        "dividend_yield": _num((overview or {}).get("dividend_yield"), 0.0),
    }

    fundamentals["debt_to_equity"] = _compute_debt_to_equity(bal) if bal else 0.0

    enrich = _enrich_growth_eps(ticker)
    fundamentals = _merge_fill_missing(fundamentals, enrich)

    # Chart cache 6h (Yahoo)
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

    # Backward compat keys expected by frontend
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
