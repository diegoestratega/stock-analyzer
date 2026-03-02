import yfinance as yf
import pandas as pd

class ChartClient:
    """
    Fetches historical price data using Yahoo Finance (yfinance).
    Returns monthly OHLC (Open, High, Low, Close) for candlestick charting,
    and identifies the absolute highest and lowest prices of the 5-year period.
    """
    def __init__(self):
        pass

    def get_5y_monthly_chart(self, ticker: str):
        """
        Fetches 5 years of monthly candlestick data.
        Returns a dictionary containing the candle data and the global high/low stats.
        """
        try:
            # Create the yfinance Ticker object
            stock = yf.Ticker(ticker)
            
            # Fetch 5 years of historical data, grouped by month ("1mo")
            hist = stock.history(period="5y", interval="1mo")
            
            # If the dataframe is empty, return an empty structure
            if hist.empty:
                print(f"Warning: No chart data found for {ticker}")
                return {"candles": [], "global_high": None, "global_low": None}

            candles = []
            
            # Variables to track the absolute high and low over the 5 years
            global_high_val = 0
            global_high_date = ""
            
            global_low_val = float('inf')
            global_low_date = ""
            
            # Iterate through the rows of the pandas DataFrame
            for date, row in hist.iterrows():
                # Skip invalid months
                if pd.isna(row['Open']) or pd.isna(row['High']) or pd.isna(row['Low']) or pd.isna(row['Close']):
                    continue
                
                date_str = date.strftime("%Y-%m")
                high = round(row['High'], 2)
                low = round(row['Low'], 2)
                
                # Append standard candlestick data
                candles.append({
                    "date": date_str,
                    "open": round(row['Open'], 2),
                    "high": high,
                    "low": low,
                    "close": round(row['Close'], 2)
                })
                
                # Check for Global High
                if high > global_high_val:
                    global_high_val = high
                    global_high_date = date_str
                    
                # Check for Global Low
                if low < global_low_val:
                    global_low_val = low
                    global_low_date = date_str
                    
            return {
                "candles": candles,
                "global_high": {
                    "price": global_high_val,
                    "date": global_high_date
                },
                "global_low": {
                    "price": global_low_val,
                    "date": global_low_date
                }
            }
            
        except Exception as e:
            print(f"Error fetching chart data for {ticker}: {e}")
            return {"candles": [], "global_high": None, "global_low": None}

# --- Quick Test ---
if __name__ == "__main__":
    client = ChartClient()
    print("Fetching 5-year monthly candlestick data for AAPL...")
    
    chart_data = client.get_5y_monthly_chart("AAPL")
    
    candles = chart_data.get("candles", [])
    
    if candles:
        print(f"\n✅ Successfully fetched {len(candles)} months of OHLC data.")
        
        print("\n--- 5-YEAR GLOBAL EXTREMES ---")
        g_high = chart_data['global_high']
        g_low = chart_data['global_low']
        print(f"🔼 5-Year High: ${g_high['price']} (Hit on: {g_high['date']})")
        print(f"🔽 5-Year Low:  ${g_low['price']} (Hit on: {g_low['date']})")

        print("\n--- SAMPLE CANDLE DATA (First 2 Months) ---")
        for c in candles[:2]:
            print(f"  {c['date']} -> O: ${c['open']} | H: ${c['high']} | L: ${c['low']} | C: ${c['close']}")
    else:
        print("❌ Failed to fetch chart data.")
