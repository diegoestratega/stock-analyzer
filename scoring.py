class StockScorer:
    def __init__(self):
        pass

    def _symbol(self, score_out_of_100: float) -> str:
        if score_out_of_100 >= 70:
            return "🟢"
        if score_out_of_100 >= 20:
            return "🟡"
        return "🔴"

    def evaluate(self, fundamentals: dict) -> dict:
        score_details = {}
        total_score = 0.0

        def scale(val, min_val, max_val, lower_is_better=False):
            if val is None:
                return 0.0
            try:
                val = float(val)
            except Exception:
                return 0.0

            if lower_is_better:
                if val <= min_val:
                    return 100.0
                if val >= max_val:
                    return 0.0
                return 100.0 - ((val - min_val) / (max_val - min_val) * 100.0)
            else:
                if val >= max_val:
                    return 100.0
                if val <= min_val:
                    return 0.0
                return ((val - min_val) / (max_val - min_val) * 100.0)

        price = fundamentals.get("price", 0) or 0
        low52w = fundamentals.get("low52w", 0) or 0
        marketcap = fundamentals.get("marketcap", 0) or 0
        petrailing = fundamentals.get("petrailing", 0) or 0
        peforward = fundamentals.get("peforward", 0) or 0
        debttoequity = fundamentals.get("debttoequity", 0) or 0
        revgrowth = fundamentals.get("revenuegrowthquarterlyyoy", 0) or 0
        epsgrowth = fundamentals.get("epsgrowthquarterlyyoy", 0) or 0

        pct_from_low = ((price - low52w) / low52w * 100.0) if low52w and low52w > 0 else 100.0
        score_price = scale(pct_from_low, 5, 20, lower_is_better=True)
        total_score += score_price * 0.25
        score_details[f"Price vs 1Y Low (25) {self._symbol(score_price)}"] = round(score_price, 1)

        mc_billions = (marketcap / 1_000_000_000.0) if marketcap else 0.0
        score_mc = scale(mc_billions, 5, 15, lower_is_better=False)
        total_score += score_mc * 0.20
        score_details[f"Market Cap (20) {self._symbol(score_mc)}"] = round(score_mc, 1)

        if not petrailing or float(petrailing) <= 0:
            score_pe = 0.0
        else:
            score_pe = scale(petrailing, 15, 25, lower_is_better=True)
        total_score += score_pe * 0.20
        score_details[f"Trailing PE (20) {self._symbol(score_pe)}"] = round(score_pe, 1)

        score_de = scale(debttoequity, 0.5, 1.0, lower_is_better=True)
        total_score += score_de * 0.20
        score_details[f"Debt/Equity (20) {self._symbol(score_de)}"] = round(score_de, 1)

        if not peforward or float(peforward) <= 0:
            score_fpe = 0.0
        else:
            score_fpe = scale(peforward, 15, 25, lower_is_better=True)
        total_score += score_fpe * 0.05
        score_details[f"Forward PE (5) {self._symbol(score_fpe)}"] = round(score_fpe, 1)

        score_revg = 100.0 if revgrowth and float(revgrowth) > 0 else 0.0
        total_score += score_revg * 0.05
        score_details[f"Rev Growth Qtr (5) {self._symbol(score_revg)}"] = round(score_revg, 1)

        score_epsg = 100.0 if epsgrowth and float(epsgrowth) > 0 else 0.0
        total_score += score_epsg * 0.05
        score_details[f"EPS Growth Qtr (5) {self._symbol(score_epsg)}"] = round(score_epsg, 1)

        finalgrade = round(total_score, 1)

        if finalgrade >= 80.0:
            rating = "Strong Buy"
        elif finalgrade >= 65.0:
            rating = "Buy"
        elif finalgrade >= 45.0:
            rating = "Hold"
        elif finalgrade >= 30.0:
            rating = "Sell"
        else:
            rating = "Strong Sell"

        breakdown = [f"{k}: {v}" for k, v in score_details.items()]

        return {
            "finalgrade": finalgrade,
            "rating": rating,
            "breakdown": breakdown,
        }
