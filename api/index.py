import os
import json
import requests
import yfinance as yf
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler
from dotenv import load_dotenv

# --- 1. CONFIG & SETUP ---
load_dotenv()
FMP_API_KEY = os.getenv("FMP_API_KEY")
BASE_URL = "https://financialmodelingprep.com/api/v3/"

# --- 2. FMP CLIENT LOGIC ---
class FMPClient:
    def __init__(self):
        self.api_key = FMP_API_KEY

    def fetch_fmp_json(self, endpoint, params=None):
        if not self.api_key:
            return []
        if params is None:
            params = {}
        params['apikey'] = self.api_key
        headers = {'User-Agent': 'Mozilla/5.0'}
        try:
            response = requests.get(f"{BASE_URL}{endpoint}", params=params, headers=headers)
            if response.status_code != 200:
                return []
            data = response.json()
            return data if isinstance(data, list) else [data]
        except Exception:
            return []

    def get_fundamentals(self, ticker: str):
        ticker = ticker.upper()
        stock = yf.Ticker(ticker)
        info = stock.info if stock.info else {}

        market_cap = info.get('marketCap', 0)
        price = info.get('currentPrice', info.get('regularMarketPrice', 0))
        pe_trailing = info.get('trailingPE', 0)
        pe_forward = info.get('forwardPE', 0)
        pb_ratio = info.get('priceToBook', 0)
        high_52w = info.get('fiftyTwoWeekHigh', 0)
        low_52w = info.get('fiftyTwoWeekLow', 0)
        div_rate = info.get('dividendRate', info.get('trailingAnnualDividendRate', 0))
        
        dividend_yield = (div_rate / price) * 100 if price and price > 0 and div_rate else 0

        # Debt to Equity
        debt_to_equity = 0
        ttm_metrics = self.fetch_fmp_json(f"key-metrics-ttm/{ticker}")
        if ttm_metrics and len(ttm_metrics) > 0:
            debt_to_equity = ttm_metrics[0].get('debtToEquityRatioTTM', 0)
            
        if not debt_to_equity or debt_to_equity == 0:
            try:
                bs = stock.balance_sheet
                if bs is not None and not bs.empty:
                    total_debt = bs.loc['Total Debt'].iloc[0] if 'Total Debt' in bs.index else 0
                    total_equity = bs.loc['Stockholders Equity'].iloc[0] if 'Stockholders Equity' in bs.index else 0
                    if total_equity > 0:
                        debt_to_equity = total_debt / total_equity
            except Exception:
                pass

        # Growth
        rev_growth_qyoy = 0
        eps_growth_qyoy = 0
        try:
            qis = stock.quarterly_income_stmt
            if qis is not None and not qis.empty:
                if 'Total Revenue' in qis.index:
                    revs = qis.loc['Total Revenue'].dropna().values
                    if len(revs) >= 5 and revs[4] != 0:
                        rev_growth_qyoy = ((revs[0] - revs[4]) / revs[4]) * 100

                eps_key = 'Diluted EPS' if 'Diluted EPS' in qis.index else 'Basic EPS' if 'Basic EPS' in qis.index else None
                if eps_key:
                    eps_vals = qis.loc[eps_key].dropna().values
                    if len(eps_vals) >= 5 and eps_vals[4] != 0:
                        eps_growth_qyoy = ((eps_vals[0] - eps_vals[4]) / abs(eps_vals[4])) * 100
        except Exception:
            pass

        return {
            "symbol": ticker,
            "market_cap": market_cap,
            "price": price,
            "high_52w": high_52w,
            "low_52w": low_52w,
            "pe_trailing": round(pe_trailing, 2) if pe_trailing else 0,
            "pe_forward": round(pe_forward, 2) if pe_forward else 0,
            "dividend_yield": round(dividend_yield, 2) if dividend_yield else 0,
            "price_to_book": round(pb_ratio, 2) if pb_ratio else 0,
            "debt_to_equity": round(debt_to_equity, 2) if debt_to_equity else 0,
            "revenue_growth_quarterly_yoy": round(rev_growth_qyoy, 2),
            "eps_growth_quarterly_yoy": round(eps_growth_qyoy, 2)
        }


# --- 3. SCORING LOGIC ---
class StockScorer:
    def scale(self, val, min_val, max_val, lower_is_better=False):
        if val is None: return 0
        if lower_is_better:
            if val <= min_val: return 100
            if val >= max_val: return 0
            return (100 - (((val - min_val) / (max_val - min_val)) * 100))
        else:
            if val >= max_val: return 100
            if val <= min_val: return 0
            return (((val - min_val) / (max_val - min_val)) * 100)

    def evaluate(self, fundamentals: dict):
        score_details = {}
        total_score = 0

        price = fundamentals.get('price', 0)
        low_52w = fundamentals.get('low_52w', 0)
        market_cap = fundamentals.get('market_cap', 0)
        pe_trailing = fundamentals.get('pe_trailing', 0)
        pe_forward = fundamentals.get('pe_forward', 0)
        debt_to_equity = fundamentals.get('debt_to_equity', 0)
        rev_growth = fundamentals.get('revenue_growth_quarterly_yoy', 0)
        eps_growth = fundamentals.get('eps_growth_quarterly_yoy', 0)

        pct_from_low = ((price - low_52w) / low_52w * 100) if low_52w and low_52w > 0 else 100
        score_price = self.scale(pct_from_low, 5, 20, lower_is_better=True)
        total_score += (score_price * 0.25)
        score_details['price_vs_low'] = {"awarded": score_price * 0.25, "weight": 25}

        mc_billions = market_cap / 1000000000 if market_cap else 0
        score_mc = self.scale(mc_billions, 5, 15, lower_is_better=False)
        total_score += (score_mc * 0.20)
        score_details['market_cap'] = {"awarded": score_mc * 0.20, "weight": 20}

        score_pe = self.scale(pe_trailing, 15, 25, lower_is_better=True) if pe_trailing else 0
        total_score += (score_pe * 0.20)
        score_details['pe_trailing'] = {"awarded": score_pe * 0.20, "weight": 20}

        score_de = self.scale(debt_to_equity, 0.5, 1.0, lower_is_better=True)
        total_score += (score_de * 0.20)
        score_details['debt_equity'] = {"awarded": score_de * 0.20, "weight": 20}

        score_fpe = self.scale(pe_forward, 15, 25, lower_is_better=True) if pe_forward else 0
        total_score += (score_fpe * 0.05)
        score_details['forward_pe'] = {"awarded": score_fpe * 0.05, "weight": 5}

        score_revg = 100 if rev_growth and rev_growth > 0 else 0
        total_score += (score_revg * 0.05)
        score_details['revenue_growth'] = {"awarded": score_revg * 0.05, "weight": 5}

        score_epsg = 100 if eps_growth and eps_growth > 0 else 0
        total_score += (score_epsg * 0.05)
        score_details['eps_growth'] = {"awarded": score_epsg * 0.05, "weight": 5}

        return round(total_score, 1), score_details


# --- 4. VERCEL NATIVE HANDLER ---
class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Setup response headers immediately
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        ticker = None
        
        # Parse the raw URL path directly
        raw_path = self.path
        
        # Method 1: Check for explicit query parameter (e.g. /api/index?ticker=AAPL)
        if '?' in raw_path:
            query_string = raw_path.split('?')[1]
            query_params = parse_qs(query_string)
            if 'ticker' in query_params:
                ticker = query_params['ticker'][0].strip().upper()

        # Method 2: Check if it was appended as a path folder (e.g. /api/index/AAPL)
        if not ticker:
            path_parts = [p for p in raw_path.split('?')[0].split('/') if p]
            if path_parts and path_parts[-1].upper() not in ['API', 'INDEX', 'INDEX.PY']:
                ticker = path_parts[-1].strip().upper()

        # If we still don't have a ticker, fail gracefully.
        if not ticker:
            error_msg = {"detail": f"No ticker provided. Raw path received: {raw_path}"}
            self.wfile.write(json.dumps(error_msg).encode('utf-8'))
            return
            
        fmp = FMPClient()
        scorer = StockScorer()

        if not fmp.api_key:
            self.wfile.write(json.dumps({"detail": "API Client failed to initialize. Check Vercel Env Vars."}).encode('utf-8'))
            return

        # Fetch and score
        fundamentals = fmp.get_fundamentals(ticker)
        if not fundamentals or fundamentals.get('price', 0) == 0:
            self.wfile.write(json.dumps({"detail": f"Could not find fundamental data for {ticker}"}).encode('utf-8'))
            return

        total_score, score_breakdown = scorer.evaluate(fundamentals)

        # Return JSON
        response_data = {
            "ticker": ticker,
            "score": total_score,
            "score_breakdown": score_breakdown,
            "metrics": {
                "forward_pe": fundamentals.get("pe_forward"),
                "dividend_yield": fundamentals.get("dividend_yield") / 100 if fundamentals.get("dividend_yield") else None,
                "revenue_growth": fundamentals.get("revenue_growth_quarterly_yoy") / 100 if fundamentals.get("revenue_growth_quarterly_yoy") else None,
                "price_to_book": fundamentals.get("price_to_book"),
                "debt_to_equity": fundamentals.get("debt_to_equity")
            }
        }
        
        self.wfile.write(json.dumps(response_data).encode('utf-8'))
        return
