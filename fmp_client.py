import os
import requests
import yfinance as yf

# Optional: don't crash if python-dotenv isn't installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

FMP_API_KEY = os.getenv("FMP_API_KEY")
BASE_URL = "https://financialmodelingprep.com/stable"


class FMPClient:
    def __init__(self):
        self.api_key = FMP_API_KEY

    def _fetch_fmp_json(self, endpoint: str, params: dict | None = None) -> list:
        if not self.api_key:
            return []
        if params is None:
            params = {}
        params["apikey"] = self.api_key

        try:
            r = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=20)
            if r.status_code != 200:
                return []
            data = r.json()
            return data if isinstance(data, list) else [data]
        except Exception:
            return []

    def getfundamentals(self, ticker: str) -> dict:
        ticker = (ticker or "").upper().strip()
        stock = yf.Ticker(ticker)

        info = {}
        try:
            info = stock.info or {}
        except Exception:
            info = {}

        marketcap = info.get("marketCap", 0) or 0
        price = info.get("currentPrice", info.get("regularMarketPrice", 0)) or 0
        petrailing = info.get("trailingPE", 0) or 0
        peforward = info.get("forwardPE", 0) or 0
        pricetobook = info.get("priceToBook", 0) or 0
        high52w = info.get("fiftyTwoWeekHigh", 0) or 0
        low52w = info.get("fiftyTwoWeekLow", 0) or 0

        div_rate = info.get("dividendRate", info.get("trailingAnnualDividendRate", 0)) or 0
        dividendyield = ((div_rate / price) * 100) if (price and price > 0 and div_rate) else 0

        # Debt/Equity: try FMP TTM first (if key exists), then compute from yfinance balance sheet
        debttoequity = 0
        ttm_metrics = self._fetch_fmp_json(f"key-metrics-ttm/{ticker}")
        if ttm_metrics:
            debttoequity = ttm_metrics[0].get("debtToEquityRatioTTM", 0) or 0

        if not debttoequity:
            try:
                bs = stock.balance_sheet
                if bs is not None and not bs.empty:
                    if "Total Debt" in bs.index:
                        total_debt = float(bs.loc["Total Debt"].iloc[0] or 0)
                    else:
                        st = float(bs.loc["Current Debt"].iloc[0] or 0) if "Current Debt" in bs.index else 0
                        lt = float(bs.loc["Long Term Debt"].iloc[0] or 0) if "Long Term Debt" in bs.index else 0
                        total_debt = st + lt

                    if "Stockholders Equity" in bs.index:
                        total_equity = float(bs.loc["Stockholders Equity"].iloc[0] or 0)
                    elif "Total Equity Gross Minority Interest" in bs.index:
                        total_equity = float(bs.loc["Total Equity Gross Minority Interest"].iloc[0] or 0)
                    else:
                        total_equity = 0

                    if total_equity > 0:
                        debttoequity = total_debt / total_equity
            except Exception:
                pass

        # Growth (YoY): compute from yfinance quarterly_income_stmt if possible
        revenuegrowthannualyoy = 0
        epsgrowthannualyoy = 0
        revenuegrowthquarterlyyoy = 0
        epsgrowthquarterlyyoy = 0

        q_is = None
        try:
            q_is = stock.quarterly_income_stmt
        except Exception:
            q_is = None

        try:
            if q_is is not None and not q_is.empty:
                if "Total Revenue" in q_is.index:
                    revs = q_is.loc["Total Revenue"].dropna().values
                    if len(revs) >= 8 and revs[4:8].sum() != 0:
                        ttm1 = float(revs[0:4].sum())
                        ttm2 = float(revs[4:8].sum())
                        revenuegrowthannualyoy = ((ttm1 - ttm2) / ttm2) * 100
                    if len(revs) >= 5 and float(revs[4]) != 0:
                        revenuegrowthquarterlyyoy = ((float(revs[0]) - float(revs[4])) / float(revs[4])) * 100

                eps_key = "Diluted EPS" if "Diluted EPS" in q_is.index else ("Basic EPS" if "Basic EPS" in q_is.index else None)
                if eps_key:
                    eps_vals = q_is.loc[eps_key].dropna().values
                    if len(eps_vals) >= 8 and float(sum(eps_vals[4:8])) != 0:
                        ttm1 = float(sum(eps_vals[0:4]))
                        ttm2 = float(sum(eps_vals[4:8]))
                        epsgrowthannualyoy = ((ttm1 - ttm2) / abs(ttm2)) * 100
                    if len(eps_vals) >= 5 and float(eps_vals[4]) != 0:
                        epsgrowthquarterlyyoy = ((float(eps_vals[0]) - float(eps_vals[4])) / abs(float(eps_vals[4]))) * 100
        except Exception:
            pass

        # EPS history (5Q): try FMP income-statement if key exists; else fallback to yfinance EPS series
        epshistory5q = []
        try:
            quarterly_is = self._fetch_fmp_json(
                "income-statement",
                {"symbol": ticker, "period": "quarter", "limit": 5},
            )
            for q in quarterly_is[:5]:
                epshistory5q.append(
                    {
                        "date": (q.get("date", "Unknown") or "Unknown")[:10],
                        "eps": q.get("eps", q.get("epsDiluted", 0)) or 0,
                    }
                )
        except Exception:
            pass

        if not epshistory5q:
            try:
                if q_is is not None and not q_is.empty:
                    eps_key = "Diluted EPS" if "Diluted EPS" in q_is.index else ("Basic EPS" if "Basic EPS" in q_is.index else None)
                    if eps_key:
                        series = q_is.loc[eps_key].dropna()
                        for dt, val in series.items():
                            datestr = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)
                            epshistory5q.append({"date": datestr[:10], "eps": float(val)})
                            if len(epshistory5q) == 5:
                                break
            except Exception:
                pass

        return {
            "symbol": ticker,
            "marketcap": marketcap,
            "price": price,
            "high52w": high52w,
            "low52w": low52w,
            "petrailing": round(float(petrailing), 2) if petrailing else 0,
            "peforward": round(float(peforward), 2) if peforward else 0,
            "dividendyield": round(float(dividendyield), 2) if dividendyield else 0,
            "pricetobook": round(float(pricetobook), 2) if pricetobook else 0,
            "debttoequity": round(float(debttoequity), 2) if debttoequity else 0,
            "revenuegrowthannualyoy": round(float(revenuegrowthannualyoy), 2),
            "revenuegrowthquarterlyyoy": round(float(revenuegrowthquarterlyyoy), 2),
            "epsgrowthannualyoy": round(float(epsgrowthannualyoy), 2),
            "epsgrowthquarterlyyoy": round(float(epsgrowthquarterlyyoy), 2),
            "epshistory5q": epshistory5q,
        }
