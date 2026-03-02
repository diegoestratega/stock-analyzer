from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Import our custom modules
from fmp_client import FMPClient
from chart_client import ChartClient
from scoring import StockScorer

app = FastAPI(title="Stock Fundamentals Analyzer")

# Enable CORS so the frontend HTML/JS can fetch data from this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all local origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize clients
try:
    fmp = FMPClient()
except Exception as e:
    print(f"⚠️ FMP Client Init Error: {e}")
    fmp = None

chart_client = ChartClient()
scorer = StockScorer()

@app.get("/")
def read_root():
    return {"status": "Backend is running! Use /api/analyze/{ticker} to get data."}

@app.get("/api/analyze/{ticker}")
def analyze_stock(ticker: str):
    """
    Main endpoint. Gathers fundamentals, chart data, and calculates the score.
    """
    ticker = ticker.upper()
    
    if not fmp:
        raise HTTPException(status_code=500, detail="FMP API Key missing or client failed to initialize.")

    print(f"\n--- Analyzing {ticker} ---")
    
    # 1. Fetch Fundamentals
    print("Fetching fundamentals...")
    fundamentals = fmp.get_fundamentals(ticker)
    
    if not fundamentals or fundamentals.get("price") == 0:
        raise HTTPException(status_code=404, detail=f"Could not find fundamental data for ticker: {ticker}")

    # 2. Fetch Chart Data (5-year monthly)
    print("Fetching chart data...")
    chart_data = chart_client.get_5y_monthly_chart(ticker)

    # 3. Calculate Score
    print("Calculating score...")
    score_data = scorer.evaluate(fundamentals)

    # 4. Package and Return everything
    print("✅ Analysis complete.")
    return {
        "ticker": ticker,
        "fundamentals": fundamentals,
        "chart": chart_data,
        "score": score_data
    }

if __name__ == "__main__":
    print("Starting local server on http://localhost:8000 ...")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
