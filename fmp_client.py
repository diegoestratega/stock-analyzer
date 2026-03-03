import os
import requests
from datetime import datetime, date

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

BASE_URL = "https://financialmodelingprep.com/stable"
FMP_API_KEY = os.getenv("FMP_API_KEY")


class FMPClient:
    def __init__(self):
        self.api_key = FMP_API_KEY
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "Mozilla/5.0"})
        self._cache = {}  # key -> (ts, value)

    def _cache_get(self, key: str, ttl_sec: int):
        item = self._cache.get(key)
        if not item:
            return None
        ts, val = item
        if (datetime.utcnow().timestamp() - ts) > ttl_sec:
            return None
        return val

    def _cache_set(self, key: str, val):
        self._cache[key] = (datetime.utcnow().timestamp(), val)

    def _get_json(self, endpoint: str, params: dict | None = None):
        if not self.api_key:
            raise RuntimeError("Missing FMP_API_KEY env var on Vercel")

        if params is None:
            params = {}
        params["apikey"] = self.api_key

        url = f"{BASE_URL}/{endpoint}"
        r = self.s.get(url, params=params, timeout=20)

        if r.status_code == 429:
            raise RuntimeError(f"FMP rate limited (429) for {endpoint}")
        if r.status_code != 200:
            return None

        try:
            return r.json()
        except Exception:
            return None

    def getfundamentals(self, ticker: str) -> dict:
        ticker = (ticker or "").upper().strip()

        cache_key = f"fund:{ticker}"
        cached = self._cache_get(cache_key, ttl_sec=30)
        if cached is not None:
            return cached

        # 1) Quote (price, market cap, 52w high/low, PE, lastDiv, etc.)
        # Doc endpoint: /stable/quote?symbol=AAPL [web:22]
        quote = self._get_json("quote", {"symbol": ticker})
        if not quote or not isinstance(quote, list) or len(quote) == 0:
            fundamentals = {}
            self._cache_set(cache_key, fundamentals)
            return fundamentals

        q = quote[0] if isinstance(quote[0], dict) else {}

        price = q.get("price", 0) or 0
        marketcap = q.get("marketCap", 0) or 0
        high52w = q.get("yearHigh", 0) or 0
        low52w = q.get("yearLow", 0) or 0
        petrailing = q.get("pe", 0) or 0
        peforward = q.get("forwardPE", 0) or 0  # may not exist; keep 0 if missing
        last_div = q.get("lastDiv", 0) or 0
        dividendyield = ((last_div / price) * 100) if price and last_div else 0

        # 2) Debt/Equity from key-metrics-ttm
        # Doc endpoint: /stable/key-metrics-ttm?symbol=AAPL [web:30]
        debttoequity = 0
        km = self._get_json("key-metrics-ttm", {"symbol": ticker})
        if km and isinstance(km, list) and len(km) > 0 and isinstance(km[0], dict):
            debttoequity = km[0].get("debtToEquityRatioTTM", 0) or 0

        # 3) Growth + EPS history from income-statement quarterly
        # Doc endpoint: /stable/income-statement?symbol=AAPL [web:46]
        revenuegrowthannualyoy = 0
        epsgrowthannualyoy = 0
        revenuegrowthquarterlyyoy = 0
        epsgrowthquarterlyyoy = 0
        epshistory5q = []

        inc = self._get_json("income-statement", {"symbol": ticker, "period": "quarter", "limit": 8})
        if inc and isinstance(inc, list):
            # Sort newest-first if needed (usually already newest-first)
            def get_dt(x):
                d = (x.get("date") or "")[:10]
                try:
                    return datetime.strptime(d, "%Y-%m-%d")
                except Exception:
                    return datetime.min

            inc_sorted = sorted([x for x in inc if isinstance(x, dict)], key=get_dt, reverse=True)

            # EPS history 5 quarters
            for row in inc_sorted[:5]:
                eps_val = row.get("eps", row.get("epsDiluted", 0)) or 0
                epshistory5q.append({"date": (row.get("date", "Unknown") or "Unknown")[:10], "eps": eps_val})

            # Quarterly YoY: Q0 vs Q4
            if len(inc_sorted) >= 5:
                rev0 = inc_sorted[0].get("revenue", 0) or 0
                rev4 = inc_sorted[4].get("revenue", 0) or 0
                if rev4:
                    revenuegrowthquarterlyyoy = ((rev0 - rev4) / rev4) * 100

                eps0 = inc_sorted[0].get("eps", inc_sorted[0].get("epsDiluted", 0)) or 0
                eps4 = inc_sorted[4].get("eps", inc_sorted[4].get("epsDiluted", 0)) or 0
                if eps4:
                    epsgrowthquarterlyyoy = ((eps0 - eps4) / abs(eps4)) * 100

            # Annual YoY (TTM): sum Q0..Q3 vs Q4..Q7
            if len(inc_sorted) >= 8:
                rev_ttm1 = sum((inc_sorted[i].get("revenue", 0) or 0) for i in range(0, 4))
                rev_ttm2 = sum((inc_sorted[i].get("revenue", 0) or 0) for i in range(4, 8))
                if rev_ttm2:
                    revenuegrowthannualyoy = ((rev_ttm1 - rev_ttm2) / rev_ttm2) * 100

                eps_ttm1 = sum((inc_sorted[i].get("eps", inc_sorted[i].get("epsDiluted", 0)) or 0) for i in range(0, 4))
                eps_ttm2 = sum((inc_sorted[i].get("eps", inc_sorted[i].get("epsDiluted", 0)) or 0) for i in range(4, 8))
                if eps_ttm2:
                    epsgrowthannualyoy = ((eps_ttm1 - eps_ttm2) / abs(eps_ttm2)) * 100

        fundamentals = {
            "symbol": ticker,
            "marketcap": marketcap,
            "price": price,
            "high52w": high52w,
            "low52w": low52w,
            "petrailing": round(float(petrailing), 2) if petrailing else 0,
            "peforward": round(float(peforward), 2) if peforward else 0,
            "dividendyield": round(float(dividendyield), 2) if dividendyield else 0,
            "pricetobook": 0,  # optional; keep 0 unless you add another endpoint
            "debttoequity": round(float(debttoequity), 2) if debttoequity else 0,
            "revenuegrowthannualyoy": round(float(revenuegrowthannualyoy), 2),
            "revenuegrowthquarterlyyoy": round(float(revenuegrowthquarterlyyoy), 2),
            "epsgrowthannualyoy": round(float(epsgrowthannualyoy), 2),
            "epsgrowthquarterlyyoy": round(float(epsgrowthquarterlyyoy), 2),
            "epshistory5q": epshistory5q,
        }

        self._cache_set(cache_key, fundamentals)
        return fundamentals

    def get5y_monthly_chart(self, ticker: str) -> dict:
        ticker = (ticker or "").upper().strip()

        cache_key = f"chart:{ticker}"
        cached = self._cache_get(cache_key, ttl_sec=300)
        if cached is not None:
            return cached

        # Light EOD endpoint: /stable/historical-price-eod/light?symbol=AAPL [web:53]
        raw = self._get_json("historical-price-eod/light", {"symbol": ticker})
        if raw is None:
            out = {"candles": [], "globalhigh": None, "globallow": None, "global_high": None, "global_low": None}
            self._cache_set(cache_key, out)
            return out

        # FMP may return a list or an object; normalize to list of rows
        rows = raw if isinstance(raw, list) else raw.get("historical", []) if isinstance(raw, dict) else []
        if not rows:
            out = {"candles": [], "globalhigh": None, "globallow": None, "global_high": None, "global_low": None}
            self._cache_set(cache_key, out)
            return out

        # Keep roughly last 5 years
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

        # Sort old->new so monthly aggregation is easy
        filtered.sort(key=lambda x: x[0])

        # Aggregate into monthly candles: open=first, high=max, low=min, close=last
        by_month = {}
        for d, r in filtered:
            key = f"{d.year:04d}-{d.month:02d}"
            o = float(r.get("open", 0) or 0)
            h = float(r.get("high", 0) or 0)
            l = float(r.get("low", 0) or 0)
            c = float(r.get("close", 0) or 0)

            if key not in by_month:
                by_month[key] = {"date": key, "open": o, "high": h, "low": l, "close": c}
            else:
                by_month[key]["high"] = max(by_month[key]["high"], h)
                by_month[key]["low"] = min(by_month[key]["low"], l if l else by_month[key]["low"])
                by_month[key]["close"] = c

        candles = list(by_month.values())

        globalhigh = None
        globallow = None
        for c in candles:
            if c["high"] and (globalhigh is None or c["high"] > globalhigh["price"]):
                globalhigh = {"price": c["high"], "date": c["date"]}
            if c["low"] and (globallow is None or c["low"] < globallow["price"]):
                globallow = {"price": c["low"], "date": c["date"]}

        out = {
            "candles": candles,
            "globalhigh": globalhigh,
            "globallow": globallow,
            "global_high": globalhigh,
            "global_low": globallow,
        }
        self._cache_set(cache_key, out)
        return out
