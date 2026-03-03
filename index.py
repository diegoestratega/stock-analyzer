from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from fmp_client import FMPClient
from scoring import StockScorer

app = FastAPI(title="Stock Fundamentals Analyzer")

fmp = FMPClient()
scorer = StockScorer()

@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.get("/api/analyze/{ticker}")
def analyze(ticker: str):
    ticker = ticker.upper().strip()

    try:
        fundamentals = fmp.getfundamentals(ticker)
        if not fundamentals or float(fundamentals.get("price", 0) or 0) <= 0:
            raise HTTPException(status_code=404, detail=f"Could not find data for {ticker}")

        chart = fmp.get5y_monthly_chart(ticker)
        score = scorer.evaluate(fundamentals)

        return {"ticker": ticker, "fundamentals": fundamentals, "chart": chart, "score": score}

    except RuntimeError as e:
        # This catches "Missing FMP_API_KEY" and "FMP rate limited (429)..."
        raise HTTPException(status_code=503, detail=str(e))

# UI LAST
app.mount("/", StaticFiles(directory="static", html=True), name="ui")
