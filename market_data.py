import os
import time
import random
import requests
from datetime import datetime, date
from typing import Optional, Dict, Any, List, Tuple

from cache_upstash import get_json, set_json
from scoring import StockScorer

CACHE_VERSION = "v14"

FMP_API_KEY = (os.getenv("FMP_API_KEY") or os.getenv("FMPAPIKEY") or "").strip()

FMP_V3_BASE = "https://financialmodelingprep.com/api/v3"

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
        "Referer": "https://financialmodelingprep.com/",
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


def _sleep_jitter(attempt: int, cap: float = 12.0):
    time.sleep(min(cap, (2**attempt) + 0.5 + random.random()))


def _redact_url(u: str) -> str:
    if not u:
        return u
    if "apikey=" in u:
        # naive but effective: cut after apikey=
        parts = u.split("apikey=")
        if len(parts) >= 2:
            tail = parts[1]
            if "&" in tail:
                tail = tail.split("&", 1)[1]
                return parts[0] + "apikey=REDACTED&" + tail
            return parts[0] + "apikey=REDACTED"
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

            # Log non-200 once (always)
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


# -------------------------
# FMP v3 helpers
# -------------------------
def _fmp_v3_get(path: str, params: Optional[Dict[str, Any]] = None, timeout: int = 22):
    if not FMP_API_KEY:
        return None
    p = dict(params or {})
    p["apikey"] = FMP_API_KEY
    url = f"{FMP_V3_BASE}/{path.lstrip('/')}"
    return _safe_get_json(url, params=p, timeout=timeout)


def _fmp_quote_v3(ticker: str) -> Optional[Dict[str, Any]]:
    cache_key = _ck(f"fmpv3:quote:{ticker}")
    cached = get_json(cache_key)
    if cached:
        return cached

    data = _fmp_v3_get(f"quote/{ticker}", params={})
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None

    q = data[0]
    price = _num(q.get("price"), 0.0)
    if not price:
        return None

    market_cap = _num(q.get("marketCap"), 0.0)
    pe_trailing = _num(q.get("pe"), 0.0)  # FMP’s trailing PE in quote payload
    high_52w = _num(q.get("yearHigh"), 0.0)
    low_52w = _num(q.get("yearLow"), 0.0)
    pb = _num(q.get("priceToBook"), 0.0)

    # Dividend yield: prefer dividendYield if present, else approx from lastDiv
    div_yield = _num(q.get("dividendYield"), 0.0)
    last_div = _num(q.get("lastDiv"), 0.0)
    if div_yield <= 0 and last_div > 0:
        div_yield = ((last_div * 4.0) / price) * 100.0

    out = {
        "symbol": ticker,
        "price": price,
        "market_cap": market_cap,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "pe_trailing": pe_trailing,
        "pe_forward": 0.0,  # not required for your current must-haves
        "dividend_yield": div_yield,
        "price_to_book": pb,
    }

    set_json(cache_key, out, ttl_seconds=5 * 60)
    return out


def _fmp_key_metrics_ttm_v3(ticker: str) -> Dict[str, Any]:
    cache_key = _ck(f"fmpv3:kmttm:{ticker}")
    cached = get_json(cache_key)
    if cached:
        return cached

    out = {"debt_to_equity": 0.0}
    data = _fmp_v3_get(f"key-metrics-ttm/{ticker}", params={})
    if isinstance(data, list) and data and isinstance(data[0], dict):
        out["debt_to_equity"] = _num(data[0].get("debtToEquityRatioTTM"), 0.0)

    set_json(cache_key, out, ttl_seconds=24 * 3600)
    return out


def _fmp_balance_sheet_v3(ticker: str) -> Dict[str, Any]:
    # Backup if key-metrics-ttm is missing
    cache_key = _ck(f"fmpv3:bs1:{ticker}")
    cached = get_json(cache_key)
    if cached:
        return cached

    out = {"__equity": 0.0, "__total_debt": 0.0}
    data = _fmp_v3_get(f"balance-sheet-statement/{ticker}", params={"limit": 1})
    if isinstance(data, list) and data and isinstance(data[0], dict):
        r = data[0]
        equity = _num(r.get("totalStockholdersEquity") or r.get("totalEquity") or 0.0, 0.0)
        total_debt = _num(r.get("totalDebt"), 0.0)
        if total_debt <= 0:
            total_debt = _num(r.get("shortTermDebt"), 0.0) + _num(r.get("longTermDebt"), 0.0)
        out["__equity"] = equity
        out["__total_debt"] = total_debt
        if equity > 0 and total_debt > 0:
            out["debt_to_equity"] = total_debt / equity

    set_json(cache_key, out, ttl_seconds=24 * 3600)
    return out


def _fmp_income_quarterly_v3(ticker: str, limit: int = 8) -> List[Dict[str, Any]]:
    cache_key = _ck(f"fmpv3:isq:{ticker}:{limit}")
    cached = get_json(cache_key)
    if isinstance(cached, list) and cached:
        return cached

    data = _fmp_v3_get(f"income-statement/{ticker}", params={"period": "quarter", "limit": limit})
    rows: List[Dict[str, Any]] = []
    if isinstance(data, list):
        rows = [r for r in data if isinstance(r, dict)]
        rows.sort(key=lambda r: (r.get("date") or ""), reverse=True)

    set_json(cache_key, rows, ttl_seconds=12 * 3600)
    return rows


def _enrich_growth_eps_from_fmp_v3(ticker: str) -> Dict[str, Any]:
    cache_key = _ck(f"fmpv3:enrich:{ticker}")
    cached = get_json(cache_key)
    if cached:
        return cached

    out = {
        "revenue_growth_annual_yoy": 0.0,
        "revenue_growth_quarterly_yoy": 0.0,
        "eps_growth_annual_yoy": 0.0,
        "eps_growth_quarterly_yoy": 0.0,
        "eps_history_5q": [],
        "__ok": False,
    }

    rows = _fmp_income_quarterly_v3(ticker, limit=8)
    if not rows:
        set_json(cache_key, out, ttl_seconds=6 * 3600)
        return out

    out["__ok"] = True

    # EPS history (last 5 quarters)
    eps_hist = []
    for r in rows[:5]:
        eps_val = r.get("eps")
        if eps_val is None:
            eps_val = r.get("epsDiluted", 0)
        eps_hist.append({"date": (r.get("date") or "Unknown")[:10], "eps": _num(eps_val, 0.0)})
    out["eps_history_5q"] = eps_hist

    # Quarterly YoY growth (q0 vs q4)
    if len(rows) >= 5:
        rev0 = _num(rows[0].get("revenue"), 0.0)
        rev4 = _num(rows[4].get("revenue"), 0.0)
        if rev4:
            out["revenue_growth_quarterly_yoy"] = ((rev0 - rev4) / rev4) * 100.0

        eps0 = _num(rows[0].get("eps") or rows[0].get("epsDiluted") or 0.0, 0.0)
        eps4 = _num(rows[4].get("eps") or rows[4].get("epsDiluted") or 0.0, 0.0)
        if eps4:
            out["eps_growth_quarterly_yoy"] = ((eps0 - eps4) / abs(eps4)) * 100.0

    # Annual YoY growth via TTM comparison (last 4 quarters vs prior 4 quarters)
    if len(rows) >= 8:
        rev_ttm1 = sum(_num(rows[i].get("revenue"), 0.0) for i in range(0, 4))
        rev_ttm2 = sum(_num(rows[i].get("revenue"), 0.0) for i in range(4, 8))
        if rev_ttm2:
            out["revenue_growth_annual_yoy"] = ((rev_ttm1 - rev_ttm2) / rev_ttm2) * 100.0

        eps_ttm1 = sum(_num(rows[i].get("eps") or rows[i].get("epsDiluted") or 0.0, 0.0) for i in range(0, 4))
        eps_ttm2 = sum(_num(rows[i].get("eps") or rows[i].get("epsDiluted") or 0.0, 0.0) for i in range(4, 8))
        if eps_ttm2:
            out["eps_growth_annual_yoy"] = ((eps_ttm1 - eps_ttm2) / abs(eps_ttm2)) * 100.0

    set_json(cache_key, out, ttl_seconds=12 * 3600)
    return out


def _fmp_price_history_5y_daily(ticker: str) -> List[Dict[str, Any]]:
    cache_key = _ck(f"fmpv3:hist5y:{ticker}")
    cached = get_json(cache_key)
    if isinstance(cached, list) and cached:
        return cached

    # daily bars; we’ll aggregate to monthly.
    data = _fmp_v3_get(f"historical-price-full/{ticker}", params={"serietype": "line"})
    rows = []
    if isinstance(data, dict):
        hist = data.get("historical")
        if isinstance(hist, list):
            rows = [r for r in hist if isinstance(r, dict)]

    # keep only last ~5 years to reduce cache size
    today = date.today()
    cutoff = date(today.year - 5, today.month, min(today.day, 28))

    def _d(s: str) -> Optional[date]:
        try:
            return datetime.strptime((s or "")[:10], "%Y-%m-%d").date()
        except Exception:
            return None

    filtered = []
    for r in rows:
        d0 = _d(r.get("date") or "")
        if d0 and d0 >= cutoff:
            filtered.append(r)

    set_json(cache_key, filtered, ttl_seconds=6 * 3600)
    return filtered


def _monthly_candles_from_daily_close(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    # With serietype=line we may only have "close". Make synthetic OHLC using close.
    by_month: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        ds = (r.get("date") or "")[:10]
        try:
            d = datetime.strptime(ds, "%Y-%m-%d")
        except Exception:
            continue
        key = f"{d.year:04d}-{d.month:02d}"
        c = _num(r.get("close"), 0.0)
        if c <= 0:
            continue
        if key not in by_month:
            by_month[key] = {"date": key, "open": c, "high": c, "low": c, "close": c}
        else:
            by_month[key]["high"] = max(by_month[key]["high"], c)
            by_month[key]["low"] = min(by_month[key]["low"], c)
            by_month[key]["close"] = c

    candles = list(by_month.values())
    candles.sort(key=lambda x: x["date"])

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
        "fmp_quote_ok": False,
        "fmp_km_ok": False,
        "fmp_bs_ok": False,
        "fmp_enrich_ok": False,
        "chart_source": None,
    }

    quote = _fmp_quote_v3(ticker)
    debug_info["fmp_quote_ok"] = bool(quote)
    if quote:
        debug_info["quote_source"] = "fmp_v3_quote"
    else:
        last_good = get_json(_ck(f"analysis:lastgood:{ticker}"))
        if last_good:
            last_good["stale"] = True
            if debug:
                last_good["debug"] = {"served": "lastgood", **debug_info}
            return last_good
        return None

    # D/E (prefer key-metrics-ttm; fallback to balance sheet compute)
    km = _fmp_key_metrics_ttm_v3(ticker)
    debug_info["fmp_km_ok"] = bool(_num(km.get("debt_to_equity"), 0.0) > 0)

    bs = _fmp_balance_sheet_v3(ticker)
    debug_info["fmp_bs_ok"] = bool(_num(bs.get("__equity"), 0.0) > 0)

    funds = dict(quote)
    funds = {**funds, **km}
    funds = {**funds, **{k: v for k, v in bs.items() if not str(k).startswith("__")}}

    # Enrich: EPS hist + growths
    enrich = _enrich_growth_eps_from_fmp_v3(ticker)
    debug_info["fmp_enrich_ok"] = bool(enrich.get("__ok"))

    funds = {**funds, **{k: v for k, v in enrich.items() if not str(k).startswith("__")}}

    # Compute P/B if missing and we have equity + market cap
    equity = _num(bs.get("__equity"), 0.0)
    mcap = _num(funds.get("market_cap"), 0.0)
    if equity > 0 and mcap > 0 and _num(funds.get("price_to_book"), 0.0) <= 0:
        funds["price_to_book"] = mcap / equity

    # Chart (FMP v3)
    chart_key = _ck(f"chart:{ticker}")
    chart = get_json(chart_key)
    if chart:
        debug_info["chart_source"] = "cache"
    else:
        daily = _fmp_price_history_5y_daily(ticker)
        chart = _monthly_candles_from_daily_close(daily) if daily else {
            "candles": [],
            "global_high": None,
            "global_low": None,
            "globalhigh": None,
            "globallow": None,
        }
        debug_info["chart_source"] = "fmp_v3" if daily else "none"
        set_json(chart_key, chart, ttl_seconds=6 * 3600)

    score = scorer.evaluate(_for_scoring(funds))

    # Frontend legacy keys
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
