import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
import yfinance as yf
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

FMP_API_KEY = os.getenv("FMP_API_KEY", "").strip()
BASE_URL = "https://financialmodelingprep.com/api/v3/"


class AnalyzeRequest(BaseModel):
    ticker: str


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _fetch_fmp_json(endpoint: str, params: Optional[dict] = None) -> List[dict]:
    if not FMP_API_KEY:
        return []
    if params is None:
        params = {}
    params["apikey"] = FMP_API_KEY
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(f"{BASE_URL}{endpoint}", params=params, headers=headers, timeout=20)
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


def _scale(val: Optional[float], min_val: float, max_val: float, lower_is_better: bool = False) -> float:
    if val is None:
        return 0.0
    try:
        val = float(val)
    except Exception:
        return 0.0

    if lower_is_better:
        if val <= min_val:
            return 100.0
        if val >= max_val:
            return 0.0
        return 100.0 - (((val - min_val) / (max_val - min_val)) * 100.0)

    if val >= max_val:
        return 100.0
    if val <= min_val:
        return 0.0
    return (((val - min_val) / (max_val - min_val)) * 100.0)


def _rating_from_score(score_100: float) -> str:
    if score_100 >= 80:
        return "Strong Buy"
    if score_100 >= 65:
        return "Buy"
    if score_100 >= 45:
        return "Hold"
    if score_100 >= 30:
        return "Sell"
    return "Strong Sell"


def _get_eps_history_5q(stock: yf.Ticker) -> List[Dict[str, Any]]:
    qis = stock.quarterly_income_stmt
    if qis is None or getattr(qis, "empty", True):
        return []

    eps_key = None
    for k in ["Diluted EPS", "Basic EPS"]:
        if k in qis.index:
            eps_key = k
            break
    if not eps_key:
        return []

    eps_series = qis.loc[eps_key].dropna()
    items = []
    # yfinance usually returns most recent first; keep first 5 available
    for dt, v in list(eps_series.items())[:5]:
        try:
            date_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)
            eps_val = float(v)
        except Exception:
            continue
        items.append({"date": date_str, "eps": eps_val})
    return items


def _growth_metrics(stock: yf.Ticker) -> Dict[str, float]:
    out = {
        "revenue_growth_annual_yoy": 0.0,
        "revenue_growth_quarterly_yoy": 0.0,
        "eps_growth_annual_yoy": 0.0,
        "eps_growth_quarterly_yoy": 0.0,
    }

    qis = stock.quarterly_income_stmt
    if qis is None or getattr(qis, "empty", True):
        return out

    # Revenue
    try:
        if "Total Revenue" in qis.index:
            revs = list(qis.loc["Total Revenue"].dropna().values)
            # Quarterly YoY (current quarter vs 4 quarters ago)
            if len(revs) >= 5 and revs[4] != 0:
                out["revenue_growth_quarterly_yoy"] = float(((revs[0] - revs[4]) / revs[4]) * 100.0)
            # Annual YoY (TTM vs prior TTM) using 8 quarters
            if len(revs) >= 8:
                ttm1 = sum(revs[0:4])
                ttm2 = sum(revs[4:8])
                if ttm2 != 0:
                    out["revenue_growth_annual_yoy"] = float(((ttm1 - ttm2) / ttm2) * 100.0)
    except Exception:
        pass

    # EPS
    try:
        eps_key = "Diluted EPS" if "Diluted EPS" in qis.index else ("Basic EPS" if "Basic EPS" in qis.index else None)
        if eps_key:
            epsv = list(qis.loc[eps_key].dropna().values)
            if len(epsv) >= 5 and epsv[4] != 0:
                out["eps_growth_quarterly_yoy"] = float(((epsv[0] - epsv[4]) / abs(epsv[4])) * 100.0)
            if len(epsv) >= 8:
                ttm1 = sum(epsv[0:4])
                ttm2 = sum(epsv[4:8])
                if ttm2 != 0:
                    out["eps_growth_annual_yoy"] = float(((ttm1 - ttm2) / abs(ttm2)) * 100.0)
    except Exception:
        pass

    return out


def _chart_5y_monthly(stock: yf.Ticker) -> Dict[str, Any]:
    # Monthly candles for ~5 years (yfinance returns a DataFrame)
    try:
        hist = stock.history(period="5y", interval="1mo", auto_adjust=False)
    except Exception:
        return {"candles": [], "global_high": None, "global_low": None}

    if hist is None or getattr(hist, "empty", True):
        return {"candles": [], "global_high": None, "global_low": None}

    candles = []
    global_high = None
    global_low = None

    for idx, row in hist.iterrows():
        try:
            dt = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
            date_ym = dt.strftime("%Y-%m")
            o = float(row["Open"])
            h = float(row["High"])
            l = float(row["Low"])
            c = float(row["Close"])
        except Exception:
            continue

        candles.append({"date": date_ym, "open": o, "high": h, "low": l, "close": c})

        if global_high is None or h > global_high["price"]:
            global_high = {"price": h, "date": date_ym}
        if global_low is None or l < global_low["price"]:
            global_low = {"price": l, "date": date_ym}

    return {"candles": candles, "global_high": global_high, "global_low": global_low}


def _compute_score(f: Dict[str, Any]) -> Dict[str, Any]:
    price = float(f.get("price", 0) or 0)
    low_52w = float(f.get("low_52w", 0) or 0)
    market_cap = float(f.get("market_cap", 0) or 0)
    pe_trailing = float(f.get("pe_trailing", 0) or 0)
    pe_forward = float(f.get("pe_forward", 0) or 0)
    debt_to_equity = float(f.get("debt_to_equity", 0) or 0)
    rev_growth_q = float(f.get("revenue_growth_quarterly_yoy", 0) or 0)
    eps_growth_q = float(f.get("eps_growth_quarterly_yoy", 0) or 0)

    breakdown = []
    total = 0.0

    # 1) Price vs 1Y low (25)
    pct_from_low = ((price - low_52w) / low_52w * 100.0) if low_52w else 100.0
    s = _scale(pct_from_low, 5, 20, lower_is_better=True)
    pts = round(s * 0.25, 2)
    total += pts
    breakdown.append(f"Price vs 1Y Low (25%): {pts:g} pts")

    # 2) Market cap (20)
    mc_b = market_cap / 1_000_000_000 if market_cap else 0.0
    s = _scale(mc_b, 5, 15, lower_is_better=False)
    pts = round(s * 0.20, 2)
    total += pts
    breakdown.append(f"Market Cap (20%): {pts:g} pts")

    # 3) Trailing P/E (20)
    s = _scale(pe_trailing, 15, 25, lower_is_better=True) if pe_trailing else 0.0
    pts = round(s * 0.20, 2)
    total += pts
    breakdown.append(f"Trailing P/E (20%): {pts:g} pts")

    # 4) Debt/Equity (20)
    s = _scale(debt_to_equity, 0.5, 1.0, lower_is_better=True)
    pts = round(s * 0.20, 2)
    total += pts
    breakdown.append(f"Debt/Equity (20%): {pts:g} pts")

    # 5) Forward P/E (5)
    s = _scale(pe_forward, 15, 25, lower_is_better=True) if pe_forward else 0.0
    pts = round(s * 0.05, 2)
    total += pts
    breakdown.append(f"Forward P/E (5%): {pts:g} pts")

    # 6) Rev growth qtr (5)
    s = 100.0 if rev_growth_q > 0 else 0.0
    pts = round(s * 0.05, 2)
    total += pts
    breakdown.append(f"Rev Growth Qtr (5%): {pts:g} pts")

    # 7) EPS growth qtr (5)
    s = 100.0 if eps_growth_q > 0 else 0.0
    pts = round(s * 0.05, 2)
    total += pts
    breakdown.append(f"EPS Growth Qtr (5%): {pts:g} pts")

    score_100 = round(total, 1)
    return {
        "score_100": score_100,
        "rating": _rating_from_score(score_100),
        "breakdown": breakdown,
    }


def _build_analysis(ticker: str) -> Dict[str, Any]:
    ticker = ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Missing ticker")

    stock = yf.Ticker(ticker)
    info = stock.info or {}

    market_cap = info.get("marketCap", 0) or 0
    price = info.get("currentPrice", info.get("regularMarketPrice", 0)) or 0
    pe_trailing = info.get("trailingPE", 0) or 0
    pe_forward = info.get("forwardPE", 0) or 0
    pb_ratio = info.get("priceToBook", 0) or 0
    high_52w = info.get("fiftyTwoWeekHigh", 0) or 0
    low_52w = info.get("fiftyTwoWeekLow", 0) or 0
    div_rate = info.get("dividendRate", info.get("trailingAnnualDividendRate", 0)) or 0

    if not price:
        raise HTTPException(status_code=404, detail=f"Could not find price for {ticker}")

    dividend_yield = (div_rate / price) * 100.0 if price and div_rate else 0.0

    # Debt-to-equity (prefer FMP, fallback to yfinance balance sheet)
    debt_to_equity = 0.0
    ttm_metrics = _fetch_fmp_json(f"key-metrics-ttm/{ticker}")
    if ttm_metrics:
        try:
            debt_to_equity = float(ttm_metrics[0].get("debtToEquityRatioTTM", 0) or 0)
        except Exception:
            debt_to_equity = 0.0

    if not debt_to_equity:
        try:
            bs = stock.balance_sheet
            if bs is not None and not bs.empty:
                total_debt = bs.loc["Total Debt"].iloc[0] if "Total Debt" in bs.index else 0
                total_equity = bs.loc["Stockholders Equity"].iloc[0] if "Stockholders Equity" in bs.index else 0
                if total_equity and float(total_equity) > 0:
                    debt_to_equity = float(total_debt) / float(total_equity)
        except Exception:
            pass

    growth = _growth_metrics(stock)
    eps_hist = _get_eps_history_5q(stock)
    chart = _chart_5y_monthly(stock)

    fundamentals = {
        "symbol": ticker,
        "market_cap": float(market_cap) if market_cap else 0.0,
        "price": float(price),
        "high_52w": float(high_52w) if high_52w else 0.0,
        "low_52w": float(low_52w) if low_52w else 0.0,
        "pe_trailing": float(pe_trailing) if pe_trailing else 0.0,
        "pe_forward": float(pe_forward) if pe_forward else 0.0,
        "dividend_yield": float(dividend_yield) if dividend_yield else 0.0,
        "price_to_book": float(pb_ratio) if pb_ratio else 0.0,
        "debt_to_equity": float(debt_to_equity) if debt_to_equity else 0.0,
        "revenue_growth_annual_yoy": float(growth["revenue_growth_annual_yoy"]),
        "revenue_growth_quarterly_yoy": float(growth["revenue_growth_quarterly_yoy"]),
        "eps_growth_annual_yoy": float(growth["eps_growth_annual_yoy"]),
        "eps_growth_quarterly_yoy": float(growth["eps_growth_quarterly_yoy"]),
        "eps_history_5q": eps_hist,
    }

    score = _compute_score(fundamentals)

    return {
        "ticker": ticker,
        "fundamentals": fundamentals,
        "score": score,
        "chart": chart,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/analyze/{ticker}")
def analyze_get(ticker: str):
    return _build_analysis(ticker)


@app.post("/analyze")
def analyze_post(req: AnalyzeRequest):
    return _build_analysis(req.ticker)
