document.addEventListener('DOMContentLoaded', () => {
    const analyzeBtn = document.getElementById('analyzeBtn');
    const tickerInput = document.getElementById('tickerInput');
    const resultsDiv = document.getElementById('results');
    const loadingDiv = document.getElementById('loading');
    const errorDiv = document.getElementById('error');

    tickerInput.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') analyzeStock();
    });

    analyzeBtn.addEventListener('click', analyzeStock);

    async function analyzeStock() {
        const ticker = tickerInput.value.trim().toUpperCase();
        if (!ticker) {
            showError('Please enter a valid stock ticker symbol.');
            return;
        }

        resultsDiv.style.display = 'none';
        errorDiv.style.display = 'none';
        loadingDiv.style.display = 'block';
        
        try {
            // Updated to securely hit the new /api/analyze endpoint
            const response = await fetch(`/api/analyze`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ticker: ticker })
            });
            
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Failed to fetch data from the server.');
            }

            const data = await response.json();
            displayResults(data, ticker);

        } catch (err) {
            console.error(err);
            showError(`Error analyzing ${ticker}: ${err.message}`);
        } finally {
            loadingDiv.style.display = 'none';
        }
    }

    function displayResults(data, ticker) {
        document.getElementById('stockTitle').textContent = `${ticker} Fundamental Analysis`;
        
        const metricsHtml = `
            <div class="metrics-grid">
                <div class="metric-card">
                    <h4>Forward P/E</h4>
                    <span class="metric-value">${data.metrics.forward_pe || 'N/A'}</span>
                </div>
                <div class="metric-card">
                    <h4>Dividend Yield</h4>
                    <span class="metric-value">${data.metrics.dividend_yield ? (data.metrics.dividend_yield * 100).toFixed(2) + '%' : 'N/A'}</span>
                </div>
                <div class="metric-card">
                    <h4>Revenue Growth</h4>
                    <span class="metric-value">${data.metrics.revenue_growth ? (data.metrics.revenue_growth * 100).toFixed(2) + '%' : 'N/A'}</span>
                </div>
                <div class="metric-card">
                    <h4>Price / Book</h4>
                    <span class="metric-value">${data.metrics.price_to_book || 'N/A'}</span>
                </div>
                <div class="metric-card">
                    <h4>Debt / Equity</h4>
                    <span class="metric-value">${data.metrics.debt_to_equity || 'N/A'}</span>
                </div>
            </div>
        `;
        document.getElementById('metricsContainer').innerHTML = metricsHtml;
        renderTradingViewChart(ticker);
        renderScoreBreakdown(data.score, data.score_breakdown);
        resultsDiv.style.display = 'block';
    }

    function renderTradingViewChart(ticker) {
        const container = document.getElementById('chartContainer');
        container.innerHTML = `<div id="tradingview_chart" style="height: 400px;"></div>`;
        
        if (window.TradingView) {
            new window.TradingView.widget({
                "autosize": true,
                "symbol": ticker,
                "interval": "W",
                "timezone": "Etc/UTC",
                "theme": "dark",
                "style": "1",
                "locale": "en",
                "enable_publishing": false,
                "hide_top_toolbar": false,
                "hide_legend": false,
                "save_image": false,
                "container_id": "tradingview_chart",
                "studies": [
                    "PriceVolumeTrend@tv-basicstudies"
                ]
            });
        }
    }

    function renderScoreBreakdown(totalScore, breakdown) {
        const scoreContainer = document.getElementById('scoreContainer');
        
        let mainColor = '#f44336'; 
        let recommendation = 'Strong Sell';
        if (totalScore >= 70) { mainColor = '#4caf50'; recommendation = 'Strong Buy'; } 
        else if (totalScore >= 20) { mainColor = '#ffeb3b'; recommendation = 'Hold / Neutral'; }

        let breakdownHtml = `
            <div class="score-header" style="color: ${mainColor}">
                <h2>Score: ${totalScore.toFixed(0)} / 100</h2>
                <h3>(${recommendation})</h3>
            </div>
            <div class="score-list">
        `;

        const sortedBreakdown = Object.entries(breakdown).sort((a, b) => b[1].weight - a[1].weight);

        sortedBreakdown.forEach(([key, value]) => {
            let ballColor = '#f44336';
            const percentage = (value.awarded / value.weight) * 100;
            if (percentage >= 70) ballColor = '#4caf50';
            else if (percentage >= 20) ballColor = '#ffeb3b';

            breakdownHtml += `
                <div class="score-item">
                    <span class="score-indicator" style="background-color: ${ballColor}"></span>
                    <span class="score-label">${formatLabel(key)}:</span>
                    <span class="score-value">${value.awarded.toFixed(1)} / ${value.weight} pts</span>
                </div>
            `;
        });

        breakdownHtml += `</div>`;
        scoreContainer.innerHTML = breakdownHtml;
    }

    function formatLabel(key) {
        const labels = {
            'price_vs_low': 'Price vs 1Y Low',
            'market_cap': 'Market Cap',
            'pe_trailing': 'Trailing P/E',
            'forward_pe': 'Forward P/E',
            'dividend_yield': 'Dividend Yield',
            'revenue_growth': 'Revenue Growth',
            'eps_growth': 'EPS Consistency',
            'debt_equity': 'Debt / Equity Ratio'
        };
        return labels[key] || key.replace(/_/g, ' ');
    }

    function showError(message) {
        errorDiv.textContent = message;
        errorDiv.style.display = 'block';
        loadingDiv.style.display = 'none';
        resultsDiv.style.display = 'none';
    }
});
