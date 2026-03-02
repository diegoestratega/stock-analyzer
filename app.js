document.addEventListener("DOMContentLoaded", () => {
    const input = document.getElementById("ticker-input");
    const btn = document.getElementById("analyze-btn");
    const dashboard = document.getElementById("dashboard");
    const errorMsg = document.getElementById("error-message");
    
    let tvChart = null; 
    let resizeObserver = null; 

    btn.addEventListener("click", () => {
        const ticker = input.value.trim().toUpperCase();
        if (ticker) fetchAnalysis(ticker);
    });

    input.addEventListener("keypress", (e) => {
        if (e.key === "Enter") {
            const ticker = input.value.trim().toUpperCase();
            if (ticker) fetchAnalysis(ticker);
        }
    });

    async function fetchAnalysis(ticker) {
        errorMsg.classList.add("hidden");
        dashboard.classList.add("hidden");
        btn.textContent = "Analyzing...";
        btn.disabled = true;

        try {
            let response;
            try {
                response = await fetch(`http://127.0.0.1:8000/api/analyze/${ticker}`);
            } catch (networkError) {
                throw new Error("Backend server is not running! Ensure python backend/main.py is running in your terminal.");
            }

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || "Failed to fetch data from API");
            }

            populateDashboard(data);
            dashboard.classList.remove("hidden");
            
            setTimeout(() => {
                drawChart(data.chart);
            }, 50);
            
        } catch (error) {
            errorMsg.textContent = error.message;
            errorMsg.classList.remove("hidden");
        } finally {
            btn.textContent = "Analyze";
            btn.disabled = false;
        }
    }

    function populateDashboard(data) {
        const funds = data.fundamentals;
        const score = data.score;

        document.getElementById("stock-symbol").textContent = funds.symbol;
        document.getElementById("stock-price").textContent = `$${funds.price.toFixed(2)}`;
        
        document.getElementById("stockanalysis-link").href = `https://stockanalysis.com/stocks/${funds.symbol.toLowerCase()}/financials/?p=quarterly`;

        const formatMoney = (num) => {
            if (num >= 1e12) return `$${(num / 1e12).toFixed(2)}T`;
            if (num >= 1e9) return `$${(num / 1e9).toFixed(2)}B`;
            if (num >= 1e6) return `$${(num / 1e6).toFixed(2)}M`;
            return `$${num.toLocaleString()}`;
        };
        const formatPct = (num) => `${num.toFixed(2)}%`;

        // Populate Key Metrics
        document.getElementById("val-mcap").textContent = formatMoney(funds.market_cap);
        document.getElementById("val-pe-trail").textContent = funds.pe_trailing > 0 ? funds.pe_trailing.toFixed(2) : "N/A";
        document.getElementById("val-pe-fwd").textContent = funds.pe_forward > 0 ? funds.pe_forward.toFixed(2) : "N/A";
        document.getElementById("val-pb").textContent = funds.price_to_book > 0 ? funds.price_to_book.toFixed(2) : "N/A";
        document.getElementById("val-de").textContent = funds.debt_to_equity.toFixed(2);
        document.getElementById("val-div").textContent = funds.dividend_yield > 0 ? formatPct(funds.dividend_yield) : "None";
        
        // Populate 1Y High and Low safely (if HTML elements exist)
        const el1yHigh = document.getElementById("val-1y-high");
        if (el1yHigh) el1yHigh.textContent = funds.high_52w > 0 ? `$${funds.high_52w.toFixed(2)}` : "N/A";
        
        const el1yLow = document.getElementById("val-1y-low");
        if (el1yLow) el1yLow.textContent = funds.low_52w > 0 ? `$${funds.low_52w.toFixed(2)}` : "N/A";

        // Growth metrics
        const revgAnn = document.getElementById("val-revg-ann");
        revgAnn.textContent = formatPct(funds.revenue_growth_annual_yoy);
        revgAnn.style.color = funds.revenue_growth_annual_yoy >= 0 ? "var(--success)" : "var(--danger)";

        const revgQtr = document.getElementById("val-revg-qtr");
        revgQtr.textContent = formatPct(funds.revenue_growth_quarterly_yoy);
        revgQtr.style.color = funds.revenue_growth_quarterly_yoy >= 0 ? "var(--success)" : "var(--danger)";

        const epsgAnn = document.getElementById("val-epsg-ann");
        epsgAnn.textContent = formatPct(funds.eps_growth_annual_yoy);
        epsgAnn.style.color = funds.eps_growth_annual_yoy >= 0 ? "var(--success)" : "var(--danger)";

        const epsgQtr = document.getElementById("val-epsg-qtr");
        epsgQtr.textContent = formatPct(funds.eps_growth_quarterly_yoy);
        epsgQtr.style.color = funds.eps_growth_quarterly_yoy >= 0 ? "var(--success)" : "var(--danger)";

        const tbody = document.querySelector("#eps-table tbody");
        tbody.innerHTML = "";
        funds.eps_history_5q.forEach(q => {
            const tr = document.createElement("tr");
            tr.innerHTML = `<td>${q.date}</td><td>$${q.eps.toFixed(2)}</td>`;
            tbody.appendChild(tr);
        });

        // 1-10 Weighted Scoring System Updates
        const scoreNum = document.getElementById("score-number");
        // Using total_score formatted to 1 decimal place (e.g. 7.5 / 10)
        scoreNum.textContent = score.total_score + " / 10"; 
        
        if (score.total_score >= 7.0) scoreNum.style.color = "var(--success)";
        else if (score.total_score >= 4.0) scoreNum.style.color = "var(--warning)";
        else scoreNum.style.color = "var(--danger)";

        document.getElementById("score-rating").textContent = score.rating;
        document.getElementById("score-rating").style.color = scoreNum.style.color;

        const breakdownList = document.getElementById("score-breakdown-list");
        breakdownList.innerHTML = "";
        score.breakdown.forEach(item => {
            const li = document.createElement("li");
            li.textContent = item;
            breakdownList.appendChild(li);
        });
    }

    function drawChart(chartData) {
        const container = document.getElementById("tv-chart");
        container.innerHTML = ""; 
        
        if (resizeObserver) {
            resizeObserver.disconnect();
            resizeObserver = null;
        }

        if (!chartData.candles || chartData.candles.length === 0) {
            container.innerHTML = "<p style='padding:20px; color:#94a3b8'>No chart data available.</p>";
            return;
        }

        document.getElementById("val-5y-high").textContent = `$${chartData.global_high.price.toFixed(2)}`;
        document.getElementById("val-5y-low").textContent = `$${chartData.global_low.price.toFixed(2)}`;

        const formattedData = chartData.candles.map(c => {
            return {
                time: c.date + "-01", 
                open: c.open,
                high: c.high,
                low: c.low,
                close: c.close
            };
        });

        const FIXED_HEIGHT = 450;
        const rect = container.getBoundingClientRect();
        const startWidth = rect.width > 0 ? rect.width : 800;

        tvChart = LightweightCharts.createChart(container, {
            autoSize: false, 
            width: startWidth,
            height: FIXED_HEIGHT,
            layout: {
                background: { type: 'solid', color: 'transparent' },
                textColor: '#94a3b8',
            },
            grid: {
                vertLines: { color: '#334155' },
                horzLines: { color: '#334155' },
            },
            crosshair: { mode: 0 },
            rightPriceScale: { borderColor: '#334155' },
            timeScale: { borderColor: '#334155', timeVisible: false },
        });

        const candlestickSeries = tvChart.addSeries(LightweightCharts.CandlestickSeries, {
            upColor: '#22c55e',
            downColor: '#ef4444',
            borderVisible: false,
            wickUpColor: '#22c55e',
            wickDownColor: '#ef4444',
            autoscaleInfoProvider: () => null, 
        });

        candlestickSeries.setData(formattedData);

        candlestickSeries.applyOptions({
            autoscaleInfoProvider: () => ({
                priceRange: {
                    minValue: chartData.global_low.price * 0.90, 
                    maxValue: chartData.global_high.price * 1.10, 
                },
            }),
        });

        candlestickSeries.createPriceLine({
            price: chartData.global_high.price,
            color: '#22c55e',
            lineWidth: 2,
            lineStyle: 2,
            axisLabelVisible: true,
            title: '5Y High',
        });

        candlestickSeries.createPriceLine({
            price: chartData.global_low.price,
            color: '#ef4444',
            lineWidth: 2,
            lineStyle: 2,
            axisLabelVisible: true,
            title: '5Y Low',
        });

        resizeObserver = new ResizeObserver(entries => {
            if (entries.length === 0 || entries[0].target !== container) { return; }
            const newWidth = entries[0].contentRect.width;
            if (newWidth > 0 && tvChart) {
                tvChart.applyOptions({ width: newWidth }); 
            }
        });
        resizeObserver.observe(container);

        tvChart.timeScale().fitContent();
    }
});
