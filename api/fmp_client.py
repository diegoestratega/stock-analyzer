import os
import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()
FMP_API_KEY = os.getenv("FMP_API_KEY")
BASE_URL = "https://financialmodelingprep.com/stable"

class FMPClient:
    def __init__(self):
        self.api_key = FMP_API_KEY

    def fetch_fmp_json(self, endpoint, params=None):
        if not self.api_key: return []
        if params is None: params = {}
        params["apikey"] = self.api_key
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            response = requests.get(f"{BASE_URL}/{endpoint}", params=params, headers=headers)
            if response.status_code != 200: return []
            data = response.json()
            return data if isinstance(data, list) else [data]
        except Exception:
            return []

    def calculate_score(self, price, low_52w, market_cap, pe_trailing, pe_forward, debt_to_equity, rev_growth, eps_growth):
        score_details = {}
        total_score = 0
        
        def scale(val, min_val, max_val, lower_is_better=False):
            if val is None: return 0
            
            if lower_is_better:
                if val <= min_val: return 100
                if val >= max_val: return 0
                return 100 - ((val - min_val) / (max_val - min_val) * 100)
            else:
                if val >= max_val: return 100
                if val <= min_val: return 0
                return ((val - min_val) / (max_val - min_val) * 100)

        pct_from_low = ((price - low_52w) / low_52w * 100) if low_52w and low_52w > 0 else 100
        score_price = scale(pct_from_low, 5, 20, lower_is_better=True)
        total_score += score_price * 0.20
        score_details['price_1y_low'] = round(score_price, 2)

        mc_billions = market_cap / 1_000_000_000 if market_cap else 0
        score_mc = scale(mc_billions, 5, 15, lower_is_better=False)
        total_score += score_mc * 0.20
        score_details['market_cap'] = round(score_mc, 2)

        if not pe_trailing or pe_trailing <= 0:
            score_pe = 0
        else:
            score_pe = scale(pe_trailing, 15, 25, lower_is_better=True)
        total_score += score_pe * 0.20
        score_details['pe_trailing'] = round(score_pe, 2)

        if not pe_forward or pe_forward <= 0:
            score_fpe = 0
        else:
            score_fpe = scale(pe_forward, 15, 25, lower_is_better=True)
        total_score += score_fpe * 0.10
        score_details['pe_forward'] = round(score_fpe, 2)

        score_de = scale(debt_to_equity, 0.5, 1.0, lower_is_better=True)
        total_score += score_de * 0.20
        score_details['debt_equity'] = round(score_de, 2)

        score_rev_g = 100 if rev_growth and rev_growth > 0 else 0
        total_score += score_rev_g * 0.05
        score_details['rev_growth_qtr'] = score_rev_g

        score_eps_g = 100 if eps_growth and eps_growth > 0 else 0
        total_score += score_eps_g * 0.05
        score_details['eps_growth_qtr'] = score_eps_g

        final_grade = round(total_score / 10, 1)
        
        return {
            "score": final_grade,  
            "score_details": score_details 
        }

    def get_fundamentals(self, ticker: str):
        ticker = ticker.upper()
        print(f"Fetching data for {ticker}...")
        stock = yf.Ticker(ticker)
        info = stock.info if stock.info else {}

        # 1. Core Metrics & 52-Week Data
        market_cap = info.get("marketCap", 0)
        price = info.get("currentPrice", info.get("regularMarketPrice", 0))
        pe_trailing = info.get("trailingPE", 0)
        pe_forward = info.get("forwardPE", 0) # Correctly fetched from Yahoo Finance
        pb_ratio = info.get("priceToBook", 0)
        
        high_52w = info.get("fiftyTwoWeekHigh", 0)
        low_52w = info.get("fiftyTwoWeekLow", 0)
        
        div_rate = info.get("dividendRate", info.get("trailingAnnualDividendRate", 0))
        dividend_yield = ((div_rate / price) * 100) if (price and price > 0 and div_rate) else 0

        # --- BULLETPROOF DEBT TO EQUITY ---
        debt_to_equity = 0
        ttm_metrics = self.fetch_fmp_json(f"key-metrics-ttm/{ticker}")
        if ttm_metrics and len(ttm_metrics) > 0:
            debt_to_equity = ttm_metrics[0].get("debtToEquityRatioTTM", 0)
        
        if not debt_to_equity or debt_to_equity == 0:
            try:
                bs = stock.balance_sheet
                if bs is not None and not bs.empty:
                    total_debt = 0
                    if 'Total Debt' in bs.index:
                        total_debt = bs.loc['Total Debt'].iloc[0]
                    else:
                        st_debt = bs.loc['Current Debt'].iloc[0] if 'Current Debt' in bs.index else 0
                        lt_debt = bs.loc['Long Term Debt'].iloc[0] if 'Long Term Debt' in bs.index else 0
                        total_debt = st_debt + lt_debt
                    
                    total_equity = 0
                    if 'Stockholders Equity' in bs.index:
                        total_equity = bs.loc['Stockholders Equity'].iloc[0]
                    elif 'Total Equity Gross Minority Interest' in bs.index:
                        total_equity = bs.loc['Total Equity Gross Minority Interest'].iloc[0]
                    
                    if total_equity > 0:
                        debt_to_equity = total_debt / total_equity
            except Exception as e:
                print(f"Balance sheet extraction failed for D/E: {e}")

        # 2. TTM Growth dynamically calculated from Yahoo Finance (Bypassing FMP limits)
        rev_growth_a_yoy = 0
        eps_growth_a_yoy = 0
        
        try:
            q_is = stock.quarterly_income_stmt
            if q_is is not None and not q_is.empty:
                # Calculate Revenue TTM Growth
                if 'Total Revenue' in q_is.index:
                    revs = q_is.loc['Total Revenue'].dropna().values
                    if len(revs) >= 8: # If yfinance gives us 8 quarters
                        ttm1_rev = sum(revs[0:4])
                        ttm2_rev = sum(revs[4:8])
                        if ttm2_rev > 0:
                            rev_growth_a_yoy = ((ttm1_rev - ttm2_rev) / ttm2_rev) * 100
                    else: # Fallback to true Annual if < 8 quarters
                        ann_is = stock.income_stmt
                        if ann_is is not None and not ann_is.empty and 'Total Revenue' in ann_is.index:
                            revs_a = ann_is.loc['Total Revenue'].dropna().values
                            if len(revs_a) >= 2 and revs_a[1] > 0:
                                rev_growth_a_yoy = ((revs_a[0] - revs_a[1]) / revs_a[1]) * 100

                # Calculate EPS TTM Growth
                eps_key = 'Diluted EPS' if 'Diluted EPS' in q_is.index else ('Basic EPS' if 'Basic EPS' in q_is.index else None)
                if eps_key:
                    eps_vals = q_is.loc[eps_key].dropna().values
                    if len(eps_vals) >= 8:
                        ttm1_eps = sum(eps_vals[0:4])
                        ttm2_eps = sum(eps_vals[4:8])
                        if ttm2_eps != 0:
                            eps_growth_a_yoy = ((ttm1_eps - ttm2_eps) / abs(ttm2_eps)) * 100
                    else: # Fallback to true Annual if < 8 quarters
                        ann_is = stock.income_stmt
                        if ann_is is not None and not ann_is.empty and eps_key in ann_is.index:
                            eps_a = ann_is.loc[eps_key].dropna().values
                            if len(eps_a) >= 2 and eps_a[1] != 0:
                                eps_growth_a_yoy = ((eps_a[0] - eps_a[1]) / abs(eps_a[1])) * 100
        except Exception as e:
            print(f"Error calculating TTM from yfinance: {e}")

        # 3. Quarterly EPS History FMP
        quarterly_is = self.fetch_fmp_json("income-statement", {"symbol": ticker, "period": "quarter", "limit": 5})
        eps_history = []
        
        if quarterly_is and len(quarterly_is) > 0:
            for q in quarterly_is:
                eps_history.append({
                    "date": q.get("date", "Unknown")[:10],
                    "eps": q.get("eps", q.get("epsDiluted", 0))
                })

        # 4. Accurate Quarterly YoY Growth using yfinance
        rev_growth_q_yoy = 0
        eps_growth_q_yoy = 0
        
        try:
            # We already fetched q_is in step 2, but doing a strict re-check for safety
            if q_is is not None and not q_is.empty:
                if 'Total Revenue' in q_is.index:
                    revs = q_is.loc['Total Revenue'].dropna().values
                    if len(revs) >= 5 and revs[4] != 0:
                        rev_growth_q_yoy = ((revs[0] - revs[4]) / revs[4]) * 100
                
                eps_key = 'Diluted EPS' if 'Diluted EPS' in q_is.index else ('Basic EPS' if 'Basic EPS' in q_is.index else None)
                if eps_key:
                    eps_vals = q_is.loc[eps_key].dropna().values
                    if len(eps_vals) >= 5 and eps_vals[4] != 0:
                        eps_growth_q_yoy = ((eps_vals[0] - eps_vals[4]) / abs(eps_vals[4])) * 100
                        
                if not eps_history and eps_key:
                    eps_series = q_is.loc[eps_key].dropna()
                    for date, val in eps_series.items():
                        date_str = date.strftime('%Y-%m-%d') if hasattr(date, 'strftime') else str(date)
                        eps_history.append({"date": date_str[:10], "eps": float(val)})
                        if len(eps_history) == 5: break
        except Exception as e:
            print(f"Error parsing yfinance quarterly data: {e}")

        # 5. Calculate Final Score
        grading = self.calculate_score(
            price=price,
            low_52w=low_52w,
            market_cap=market_cap,
            pe_trailing=pe_trailing,
            pe_forward=pe_forward,
            debt_to_equity=debt_to_equity,
            rev_growth=rev_growth_q_yoy,
            eps_growth=eps_growth_q_yoy
        )

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
            "revenue_growth_annual_yoy": round(rev_growth_a_yoy, 2),
            "revenue_growth_quarterly_yoy": round(rev_growth_q_yoy, 2),
            "eps_growth_annual_yoy": round(eps_growth_a_yoy, 2),
            "eps_growth_quarterly_yoy": round(eps_growth_q_yoy, 2),
            "eps_history_5q": eps_history,
            "grading": grading  
        }
