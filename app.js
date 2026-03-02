async function analyzeTicker() {
    const tickerInput = document.getElementById('ticker').value.trim().toUpperCase();
    const resultDiv = document.getElementById('result');
    
    if (!tickerInput) return;
    
    resultDiv.innerHTML = "Analyzing...";

    try {
        // Now using a GET request with query parameters to bypass Vercel's POST body stripping
        const targetUrl = `/api/index?ticker=${encodeURIComponent(tickerInput)}`;
        
        const response = await fetch(targetUrl, {
            method: 'GET',
            headers: { 
                'Content-Type': 'application/json' 
            }
        });

        const data = await response.json();

        if (data.detail) {
            resultDiv.innerHTML = `<p style="color: red;">Error: ${data.detail}</p>`;
            return;
        }

        resultDiv.innerHTML = `
            <h2>${data.ticker} - Total Score: ${data.score}/100</h2>
            <div style="display: flex; gap: 20px; margin-bottom: 20px;">
                <div>
                    <h3>Metrics</h3>
                    <ul>
                        <li>Forward P/E: ${data.metrics.forward_pe || 'N/A'}</li>
                        <li>Dividend Yield: ${data.metrics.dividend_yield ? (data.metrics.dividend_yield * 100).toFixed(2) + '%' : 'N/A'}</li>
                        <li>Price to Book: ${data.metrics.price_to_book || 'N/A'}</li>
                        <li>Debt to Equity: ${data.metrics.debt_to_equity || 'N/A'}</li>
                        <li>Revenue Growth: ${data.metrics.revenue_growth ? (data.metrics.revenue_growth * 100).toFixed(2) + '%' : 'N/A'}</li>
                    </ul>
                </div>
            </div>
            <h3>Score Breakdown</h3>
            <pre style="background: #1e1e1e; color: #00ff00; padding: 15px; border-radius: 5px; overflow-x: auto;">
${JSON.stringify(data.score_breakdown, null, 2)}
            </pre>
        `;
    } catch (error) {
        console.error("Fetch error:", error);
        resultDiv.innerHTML = `<p style="color: red;">Network Error: Failed to reach the API.</p>`;
    }
}

document.getElementById('analyzeBtn').addEventListener('click', (e) => {
    e.preventDefault();
    analyzeTicker();
});
