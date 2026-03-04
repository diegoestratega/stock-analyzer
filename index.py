from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from market_data import get_analysis

app = FastAPI(title="Stock Fundamentals Analyzer")

@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.get("/api/analyze/{ticker}")
def analyze(ticker: str, debug: int = 0):
    ticker = (ticker or "").upper().strip()
    if not ticker.isalnum():
        raise HTTPException(status_code=400, detail="Invalid ticker")

    data = get_analysis(ticker, debug=bool(debug))
    if not data:
        # 503 is correct here (upstream blocked/rate-limited)
        raise HTTPException(
            status_code=503,
            detail="Upstream data unavailable (Yahoo/FMP). Try again in 30–60s."
        )
    return data

# UI
app.mount("/", StaticFiles(directory="static", html=True), name="ui")
