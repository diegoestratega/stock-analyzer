from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from fmp_client import FMPClient
from scoring import StockScorer

app = FastAPI(title="Stock Fundamentals Analyzer")

# Initialize safely so /api/health still works even if something is misconfigured
try:
    fmp = FMPClient()
except Exception as e:
    fmp = None
    fmp_init_error = str(e)

try:
    scorer = StockScorer()
except Exception as e:
    scorer = None
    scorer_init_error = str(e)

@app.get("/api/health")
def health():
    return {"status": "ok"}

def get_5y_monthly_chart(ticker: str) -> dict:
    # Lazy import so missing yfinance/pandas doesn't crash the whole function on /api/health
    try:
        import yfinance as yf
    except Exception:
        return {"candles": [], "global_high": None, "global_low": None}

    t = yf.Ticker(ticker)
    hist = t.history(period="5y", interval="1mo")
    if hist is None or hist.empty:
        return {"candles": [], "global_high": None, "global_low": None}

    candles = []
    gh = None
    gl = None

    for idx, row in hist.iterrows():
        o = float(row.get("Open", 0) or 0)
        h = float(row.get("High", 0) or 0)
        l = float(row.get("Low", 0) or 0)
        c = float(row.get("Close", 0) or 0)

        if h > 0 and (gh is None or h > gh["price"]):
            gh = {"price": h, "date": idx.strftime("%Y-%m")}
        if l > 0 and (gl is None or l < gl["price"]):
            gl = {"price": l, "date": idx.strftime("%Y-%m")}

        candles.append({
            "date": idx.strftime("%Y-%m"),
            "open": o, "high": h, "low": l, "close": c
        })

    return {"candles": candles, "global_high": gh, "global_low": gl}

@app.get("/api/analyze/{ticker}")
def analyze(ticker: str):
    ticker = ticker.upper().strip()

    if fmp is None:
        raise HTTPException(status_code=500, detail=f"FMP client failed to init: {fmp_init_error}")

    if scorer is None:
        raise HTTPException(status_code=500, detail=f"Scorer failed to init: {scorer_init_error}")

    fundamentals = fmp.getfundamentals(ticker)
    if not fundamentals or float(fundamentals.get("price", 0) or 0) <= 0:
        raise HTTPException(status_code=404, detail=f"Could not find data for {ticker}")

    score = scorer.evaluate(fundamentals)
    chart = get_5y_monthly_chart(ticker)

    return {"ticker": ticker, "fundamentals": fundamentals, "score": score, "chart": chart}

# UI LAST
app.mount("/", StaticFiles(directory="static", html=True), name="ui")
