from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import yfinance as yf

from fmp_client import FMPClient
from scoring import StockScorer

app = FastAPI(title="Stock Fundamentals Analyzer")

# Same-origin on Vercel doesn't need CORS, but leaving this is harmless and avoids future headaches.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

fmp = FMPClient()
scorer = StockScorer()


def get_5y_monthly_chart(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    hist = t.history(period="5y", interval="1mo")
    if hist is None or hist.empty:
        return {"candles": [], "global_high": None, "global_low": None}

    candles = []
    global_high = {"price": None, "date": None}
    global_low = {"price": None, "date": None}

    for idx, row in hist.iterrows():
        o = float(row.get("Open", 0) or 0)
        h = float(row.get("High", 0) or 0)
        l = float(row.get("Low", 0) or 0)
        c = float(row.get("Close", 0) or 0)

        if h > 0 and (global_high["price"] is None or h > global_high["price"]):
            global_high = {"price": h, "date": idx.strftime("%Y-%m")}
        if l > 0 and (global_low["price"] is None or l < global_low["price"]):
            global_low = {"price": l, "date": idx.strftime("%Y-%m")}

        candles.append(
            {
                "date": idx.strftime("%Y-%m"),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
            }
        )

    return {"candles": candles, "global_high": global_high, "global_low": global_low}


# ---------- API FIRST ----------
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/analyze/{ticker}")
def analyze(ticker: str):
    ticker = ticker.upper().strip()

    fundamentals = fmp.getfundamentals(ticker)
    if not fundamentals or float(fundamentals.get("price", 0) or 0) <= 0:
        raise HTTPException(status_code=404, detail=f"Could not find data for {ticker}")

    score = scorer.evaluate(fundamentals)  # scoring.py expects keys like marketcap, low52w, etc.
    chart = get_5y_monthly_chart(ticker)

    return {
        "ticker": ticker,
        "fundamentals": fundamentals,
        "score": score,
        "chart": chart,
    }


# ---------- UI LAST ----------
# This makes:
#   / -> static/index.html
#   /app.js -> static/app.js
app.mount("/", StaticFiles(directory="static", html=True), name="ui")
