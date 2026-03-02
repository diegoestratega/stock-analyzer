class StockScorer:
    def __init__(self):
        pass

    def get_symbol(self, score_out_of_100):
        # Updated to exactly match your requested thresholds
        if score_out_of_100 >= 70:
            return "🟢"
        elif score_out_of_100 >= 20:
            return "🟡"
        else:
            return "🔴"

    def evaluate(self, fundamentals: dict) -> dict:
        """
        Evaluates stock based on linear scoring models.
        Outputs a 0-100 final grade and breakdown metrics scaled 0-100.
        """
        score_details = {}
        total_score = 0
        
        # Helper for linear interpolation
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

        price = fundamentals.get("price", 0)
        low_52w = fundamentals.get("low_52w", 0)
        market_cap = fundamentals.get("market_cap", 0)
        pe_trailing = fundamentals.get("pe_trailing", 0)
        pe_forward = fundamentals.get("pe_forward", 0)
        debt_to_equity = fundamentals.get("debt_to_equity", 0)
        rev_growth = fundamentals.get("revenue_growth_quarterly_yoy", 0)
        eps_growth = fundamentals.get("eps_growth_quarterly_yoy", 0)

        # --- RANKED HIGHEST TO LOWEST WEIGHT ---

        # 1. Price near 1Y Low (25%)
        pct_from_low = ((price - low_52w) / low_52w * 100) if low_52w and low_52w > 0 else 100
        score_price = scale(pct_from_low, 5, 20, lower_is_better=True)
        total_score += score_price * 0.25
        sym = self.get_symbol(score_price)
        score_details['Price vs 1Y Low (25%)'] = f"{sym} {round(score_price, 1)} pts"

        # 2. Market Cap (20%)
        mc_billions = market_cap / 1_000_000_000 if market_cap else 0
        score_mc = scale(mc_billions, 5, 15, lower_is_better=False)
        total_score += score_mc * 0.20
        sym = self.get_symbol(score_mc)
        score_details['Market Cap (20%)'] = f"{sym} {round(score_mc, 1)} pts"

        # 3. P/E Trailing (20%)
        if not pe_trailing or pe_trailing <= 0:
            score_pe = 0
        else:
            score_pe = scale(pe_trailing, 15, 25, lower_is_better=True)
        total_score += score_pe * 0.20
        sym = self.get_symbol(score_pe)
        score_details['Trailing P/E (20%)'] = f"{sym} {round(score_pe, 1)} pts"

        # 4. Debt/Equity (20%)
        score_de = scale(debt_to_equity, 0.5, 1.0, lower_is_better=True)
        total_score += score_de * 0.20
        sym = self.get_symbol(score_de)
        score_details['Debt/Equity (20%)'] = f"{sym} {round(score_de, 1)} pts"

        # 5. Forward P/E (5%)
        if not pe_forward or pe_forward <= 0:
            score_fpe = 0
        else:
            score_fpe = scale(pe_forward, 15, 25, lower_is_better=True)
        total_score += score_fpe * 0.05
        sym = self.get_symbol(score_fpe)
        score_details['Forward P/E (5%)'] = f"{sym} {round(score_fpe, 1)} pts"

        # 6. Rev Growth Qtr (5%)
        score_rev_g = 100 if rev_growth and rev_growth > 0 else 0
        total_score += score_rev_g * 0.05
        sym = self.get_symbol(score_rev_g)
        score_details['Rev Growth Qtr (5%)'] = f"{sym} {round(score_rev_g, 1)} pts"

        # 7. EPS Growth Qtr (5%)
        score_eps_g = 100 if eps_growth and eps_growth > 0 else 0
        total_score += score_eps_g * 0.05
        sym = self.get_symbol(score_eps_g)
        score_details['EPS Growth Qtr (5%)'] = f"{sym} {round(score_eps_g, 1)} pts"

        # Final grade out of 100
        final_grade = round(total_score, 1)

        if final_grade >= 80.0: rating_text = "Strong Buy"
        elif final_grade >= 65.0: rating_text = "Buy"
        elif final_grade >= 45.0: rating_text = "Hold"
        elif final_grade >= 30.0: rating_text = "Sell"
        else: rating_text = "Strong Sell"

        # Because dictionaries in modern Python maintain insertion order,
        # this will automatically render them from highest to lowest weight.
        breakdown_list = [f"{k}: {v}" for k, v in score_details.items()]

        return {
            "total_score": final_grade, 
            "rating": rating_text,
            "breakdown": breakdown_list
        }
