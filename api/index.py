from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Allow browser calls from your Vercel frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/analyze/{ticker}")
async def analyze(ticker: str):
    # TODO: put your existing scoring logic here
    # For now, return a simple JSON so we can verify routing works
    return {
        "ticker": ticker,
        "score": 75,
        "score_breakdown": {},
        "metrics": {}
    }
