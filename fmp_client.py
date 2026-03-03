import os
import requests
import time
from datetime import datetime, date
from urllib.parse import urlencode

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

FMP_API_KEY = os.getenv("FMP_API_KEY")
FMP_BASE = "https://financialmodelingprep.com/stable"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
]

class FMPClient:
    def __init__(self):
        self.api_key = FMP_API_KEY
        self._cache = {}
        self._ua_idx = 0

    def _get_ua(self):
        """Rotate user agents"""
        ua = USER_AGENTS[self._ua_idx % len(USER_AGENTS)]
        self._ua_idx += 1
        return ua

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

    def _get_json(self, url: str, timeout=20):
        """Retry logic for HTTP requests with exponential backoff"""
        headers = {"User-Agent": self._get_ua()}
        for attempt in range(3):
            try:
                r = requests.get(url, headers=headers, timeout=timeout)
                if r.status_code == 200:
                    return r.json()
                elif r.status_code == 429:
                    wait = (2 ** attempt) + 1  # 1s, 3s, 7s
                    time.sleep(wait)
                    continue
                else:
                    return None
            except Exception as e:
                if attempt < 2:
                    time.sleep(1)
                continue
        return None

    def _get_yahoo_stock(self, ticker: str):
        """Fetch stock data from yfinance with smart retry"""
        try:
            import yfinance as yf
            stock = yf.Ticker(ticker)
            info = stock.info
            return info if info else {}
        except Exception:
            return {}

    def getfundamentals(self, ticker: str) -> dict:
        ticker = (ticker or "").upper().strip()
        
        cache_key = f"fund:{ticker}"
        cached = self._cache_get(cache_key, ttl_sec=300)  # 5 min cache
        if cached is not None:
            return cached

        print(f"[INFO] Fetching fundamentals for {ticker} (Yahoo primary)")

        # PRIMARY: Yahoo Finance
        info = self._get_yahoo_stock(ticker)
        
        if not info:
            print(f"[WARN] Yahoo failed for {ticker}, trying FMP")
            info = {}

        # Extract from Yahoo with graceful fallback
        price = info.get("currentPrice", info.get("regularMarketPrice", 0)) or 0
        marketcap = info.get("marketCap", 0) or 0
        high52w = info.get("fiftyTwoWeekHigh", 0) or 0
        low52w = info.get("fiftyTwoWeekLow", 0) or 0
        petrailing = info.get("trailingPE", 0) or 0
        peforward = info.get("forwardPE", 0) or 0
        
        divrate = info.get("dividendRate", info.get("trailingAnnualDividendRate", 0)) or 0
        dividendyield = ((divrate / price) * 100) if price and divrate else 0
        
        # Debt/Equity from Yahoo balance sheet
        debttoequity = 0
        try:
            bs = info.get("balancesheet")
            if bs is not None and not bs.empty:
                total_debt = 0
                if "Total Debt" in bs.index:
                    total_debt = float(bs.loc["Total Debt"].iloc[0])
                else:
                    st_debt = float(bs.loc["Current Debt"].iloc[0]) if "Current Debt" in bs.index else 0
                    lt_debt = float(bs.loc["Long Term Debt"].iloc[0]) if "Long Term Debt" in bs.index else 0
                    total_debt = st_debt + lt_debt

                total_equity = 0
                if "Stockholders Equity" in bs.index:
                    total_equity = float(bs.loc["Stockholders Equity"].iloc[0])
                elif "Total Equity Gross Minority Interest" in bs.index:
                    total_equity = float(bs.loc["Total Equity Gross Minority Interest"].iloc[0])

                if total_equity > 0:
                    debttoequity = total_debt / total_equity
        except Exception as e:
            print(f"[WARN] D/E extraction failed: {e}")

        # Growth + EPS from Yahoo quarterly income
        revenuegrowthannualyoy = 0
        epsgrowthannualyoy = 0
        revenuegrowthquarterlyyoy = 0
        epsgrowthquarterlyyoy = 0
        epshistory5q = []

        try:
            qis = info.get("quarterly_income_stmt")
            if qis is not None and not qis.empty:
                def get_dt(x):
                    try:
                        return datetime.strptime(str(x)[:10], "%Y-%m-%d")
                    except Exception:
                        return datetime.min

                qis_sorted = qis.sort_index(key=lambda x: [get_dt(d) for d in x], ascending=False)

                # EPS history
                for idx, col in enumerate(qis_sorted.columns[:5]):
                    eps_val = 0
                    if "Diluted EPS" in qis_sorted.index:
                        eps_val = float(qis_sorted.loc["Diluted EPS", col])
                    elif "Basic EPS" in qis_sorted.index:
                        eps_val = float(qis_sorted.loc["Basic EPS", col])
                    epshistory5q.append({"date": str(col)[:10], "eps": eps_val})

                # Quarterly YoY: Q0 vs Q4
                if qis_sorted.shape[1] >= 5:
                    rev0 = float(qis_sorted.loc["Total Revenue", qis_sorted.columns[0]]) if "Total Revenue" in qis_sorted.index else 0
                    rev4 = float(qis_sorted.loc["Total Revenue", qis_sorted.columns[4]]) if "Total Revenue" in qis_sorted.index else 0
                    if rev4:
                        revenuegrowthquarterlyyoy = ((rev0 - rev4) / rev4) * 100

                    eps0 = 0
                    eps4 = 0
                    if "Diluted EPS" in qis_sorted.index:
                        eps0 = float(qis_sorted.loc["Diluted EPS", qis_sorted.columns[0]])
                        eps4 = float(qis_sorted.loc["Diluted EPS", qis_sorted.columns[4]])
                    elif "Basic EPS" in qis_sorted.index:
                        eps0 = float(qis_sorted.loc["Basic EPS", qis_sorted.columns[0]])
                        eps4 = float(qis_sorted.loc["Basic EPS", qis_sorted.columns[4]])
                    if eps4:
                        epsgrowthquarterlyyoy = ((eps0 - eps4) / abs(eps4)) * 100

                # Annual TTM: Q0..Q3 vs Q4..Q7
                if qis_sorted.shape[1] >= 8 and "Total Revenue" in qis_sorted.index:
                    rev_ttm1 = sum(float(qis_sorted.iloc[qis_sorted.index.get_loc("Total Revenue"), i]) for i in range(0, 4))
                    rev_ttm2 = sum(float(qis_sorted.iloc[qis_sorted.index.get_loc("Total Revenue"), i]) for i in range(4, 8))
                    if rev_ttm2:
                        revenuegrowthannualyoy = ((rev_ttm1 - rev_ttm2) / rev_ttm2) * 100

        except Exception as e:
            print(f"[WARN] Growth calc failed: {e}")

        fundamentals = {
            "symbol": ticker,
            "marketcap": marketcap,
            "price": price,
            "high52w": high52w,
            "low52w": low52w,
            "petrailing": round(float(petrailing), 2) if petrailing else 0,
            "peforward": round(float(peforward), 2) if peforward else 0,
            "dividendyield": round(float(dividendyield), 2) if dividendyield else 0,
            "pricetobook": info.get("priceToBook", 0) or 0,
            "debttoequity": round(float(debttoequity), 2) if debttoequity else 0,
            "revenuegrowthannualyoy": round(float(revenuegrowthannualyoy), 2),
            "revenuegrowthquarterlyyoy": round(float(revenuegrowthquarterlyyoy), 2),
            "epsgrowthannualyoy": round(float(epsgrowthannualyoy), 2),
            "epsgrowthquarterlyyoy": round(float(epsgrowthquarterlyyoy), 2),
            "epshistory5q": epshistory5q,
        }

        if not fundamentals.get("price"):
            print(f"[ERROR] No price data for {ticker}")
            return {}

        self._cache_set(cache_key, fundamentals)
        return fundamentals

    def get5y_monthly_chart(self, ticker: str) -> dict:
        ticker = (ticker or "").upper().strip()

        cache_key = f"chart:{ticker}"
        cached = self._cache_get(cache_key, ttl_sec=7200)  # 2 hour cache
        if cached is not None:
            return cached

        print(f"[INFO] Fetching chart for {ticker}")

        try:
            import yfinance as yf
            stock = yf.Ticker(ticker)
            hist = stock.history(period="5y")

            if hist.empty:
                print(f"[WARN] No history for {ticker}")
                return {"candles": [], "globalhigh": None, "globallow": None}

            # Aggregate to monthly
            hist["Year_Month"] = hist.index.to_period("M")
            monthly = hist.groupby("Year_Month").agg({
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
            }).reset_index()

            candles = []
            for _, row in monthly.iterrows():
                candles.append({
                    "date": str(row["Year_Month"]),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                })

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
            }
            self._cache_set(cache_key, out)
            return out

        except Exception as e:
            print(f"[ERROR] Chart fetch failed: {e}")
            return {"candles": [], "globalhigh": None, "globallow": None}
