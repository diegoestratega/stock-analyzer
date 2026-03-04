from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from market_data import get_analysis

app = FastAPI(title="Stock Fundamentals Analyzer")

@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.get("/api/analyze/{ticker}")
def analyze(ticker: str):
    ticker = (ticker or "").upper().strip()
    if not ticker.isalnum():
        raise HTTPException(status_code=400, detail="Invalid ticker")

    data = get_analysis(ticker)
    if not data:
        raise HTTPException(status_code=404, detail=f"Could not find data for {ticker}")
    return data

# UI (serves /, /app.js, etc. from ./static)
app.mount("/", StaticFiles(directory="static", html=True), name="ui")
