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
      // Vercel: FastAPI in api/index.py is reachable under /api/*
      const response = await fetch(`/api/analyze/${ticker}`);
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

    const fmtMoney = (num) => {
      if (num >= 1e12) return `$${(num / 1e12).toFixed(2)}T`;
      if (num >= 1e9) return `$${(num / 1e9).toFixed(2)}B`;
      if (num >= 1e6) return `$${(num / 1e6).toFixed(2)}M`;
      return `$${Number(num || 0).toLocaleString()}`;
    };
    const fmtPct = (num) => `${Number(num || 0).toFixed(2)}%`;

    document.getElementById("stock-symbol").textContent = funds.symbol || data.ticker;
    document.getElementById("stock-price").textContent = funds.price ? `$${funds.price.toFixed(2)}` : "—";
    document.getElementById("stockanalysis-link").href =
      `https://stockanalysis.com/stocks/${(funds.symbol || data.ticker).toLowerCase()}/financials/?p=quarterly`;

    document.getElementById("val-mcap").textContent = fmtMoney(funds.market_cap || 0);
    document.getElementById("val-div").textContent = (funds.dividend_yield || 0) > 0 ? fmtPct(funds.dividend_yield) : "None";
    document.getElementById("val-pe-trail").textContent = (funds.pe_trailing || 0) > 0 ? funds.pe_trailing.toFixed(2) : "N/A";
    document.getElementById("val-pe-fwd").textContent = (funds.pe_forward || 0) > 0 ? funds.pe_forward.toFixed(2) : "N/A";
    document.getElementById("val-de").textContent = (funds.debt_to_equity || 0).toFixed(2);
    document.getElementById("val-pb").textContent = (funds.price_to_book || 0) > 0 ? funds.price_to_book.toFixed(2) : "N/A";

    const el1yHigh = document.getElementById("val-1y-high");
    if (el1yHigh) el1yHigh.textContent = (funds.high_52w || 0) > 0 ? `$${funds.high_52w.toFixed(2)}` : "N/A";
    const el1yLow = document.getElementById("val-1y-low");
    if (el1yLow) el1yLow.textContent = (funds.low_52w || 0) > 0 ? `$${funds.low_52w.toFixed(2)}` : "N/A";

    const revgAnn = document.getElementById("val-revg-ann");
    revgAnn.textContent = fmtPct(funds.revenue_growth_annual_yoy);
    revgAnn.style.color = (funds.revenue_growth_annual_yoy || 0) >= 0 ? "var(--success)" : "var(--danger)";

    const revgQtr = document.getElementById("val-revg-qtr");
    revgQtr.textContent = fmtPct(funds.revenue_growth_quarterly_yoy);
    revgQtr.style.color = (funds.revenue_growth_quarterly_yoy || 0) >= 0 ? "var(--success)" : "var(--danger)";

    const epsgAnn = document.getElementById("val-epsg-ann");
    epsgAnn.textContent = fmtPct(funds.eps_growth_annual_yoy);
    epsgAnn.style.color = (funds.eps_growth_annual_yoy || 0) >= 0 ? "var(--success)" : "var(--danger)";

    const epsgQtr = document.getElementById("val-epsg-qtr");
    epsgQtr.textContent = fmtPct(funds.eps_growth_quarterly_yoy);
    epsgQtr.style.color = (funds.eps_growth_quarterly_yoy || 0) >= 0 ? "var(--success)" : "var(--danger)";

    const tbody = document.querySelector("#eps-table tbody");
    tbody.innerHTML = "";
    (funds.eps_history_5q || []).forEach((q) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${q.date}</td><td>${Number(q.eps || 0).toFixed(2)}</td>`;
      tbody.appendChild(tr);
    });

    // Score header (matches your screenshot: "AAPL Score: 30 (Sell)")
    const scoreNum = document.getElementById("score-number");
    const scoreRating = document.getElementById("score-rating");
    const s100 = Number(score.score_100 || 0);

    scoreNum.textContent = `${funds.symbol || data.ticker} Score: ${s100.toFixed(0)}`;
    let color = "var(--danger)";
    if (s100 >= 70) color = "var(--success)";
    else if (s100 >= 45) color = "var(--warning)";
    scoreNum.style.color = color;

    scoreRating.textContent = `(${score.rating || "—"})`;
    scoreRating.style.color = color;

    const breakdownList = document.getElementById("score-breakdown-list");
    breakdownList.innerHTML = "";
    (score.breakdown || []).forEach((item) => {
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

    if (!chartData || !chartData.candles || chartData.candles.length === 0) {
      container.innerHTML = `<p style="padding:20px;color:#94a3b8">No chart data available.</p>`;
      return;
    }

    const formattedData = chartData.candles.map((c) => ({
      time: c.date + "-01",
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close
    }));

    const FIXED_HEIGHT = 550;
    const rect = container.getBoundingClientRect();
    const startWidth = rect.width > 0 ? rect.width : 900;

    tvChart = LightweightCharts.createChart(container, {
      autoSize: false,
      width: startWidth,
      height: FIXED_HEIGHT,
      layout: { background: { type: "solid", color: "transparent" }, textColor: "#94a3b8" },
      grid: { vertLines: { color: "#334155" }, horzLines: { color: "#334155" } },
      crosshair: { mode: 0 },
      rightPriceScale: { borderColor: "#334155" },
      timeScale: { borderColor: "#334155", timeVisible: false }
    });

    const series = tvChart.addSeries(LightweightCharts.CandlestickSeries, {
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444"
    });

    series.setData(formattedData);

    if (chartData.global_high && chartData.global_high.price) {
      series.createPriceLine({
        price: chartData.global_high.price,
        color: "#22c55e",
        lineWidth: 2,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "5Y High"
      });
    }

    if (chartData.global_low && chartData.global_low.price) {
      series.createPriceLine({
        price: chartData.global_low.price,
        color: "#ef4444",
        lineWidth: 2,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "5Y Low"
      });
    }

    resizeObserver = new ResizeObserver((entries) => {
      if (!entries.length) return;
      const w = entries[0].contentRect.width;
      if (w > 0 && tvChart) tvChart.applyOptions({ width: w });
    });

    resizeObserver.observe(container);
    tvChart.timeScale().fitContent();
  }
});
