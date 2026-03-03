document.addEventListener("DOMContentLoaded", () => {
  const input = document.getElementById("ticker-input");
  const btn = document.getElementById("analyze-btn");
  const dashboard = document.getElementById("dashboard");
  const errorMsg = document.getElementById("error-message");

  let tvChart = null;

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
      const response = await fetch(`/api/analyze/${ticker}`);
      const data = await response.json();

      if (!response.ok) throw new Error(data.detail || "API error");

      populateDashboard(data);
      dashboard.classList.remove("hidden");

      setTimeout(() => drawChart(data.chart), 50);
    } catch (err) {
      errorMsg.textContent = err.message || String(err);
      errorMsg.classList.remove("hidden");
    } finally {
      btn.textContent = "Analyze";
      btn.disabled = false;
    }
  }

  function formatMoney(num) {
    const n = Number(num || 0);
    if (!isFinite(n)) return "—";
    if (n >= 1e12) return (n / 1e12).toFixed(2) + "T";
    if (n >= 1e9) return (n / 1e9).toFixed(2) + "B";
    if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
    return n.toLocaleString();
  }

  function formatPct(num) {
    const n = Number(num);
    if (!isFinite(n)) return "—";
    return n.toFixed(2) + "%";
  }

  function populateDashboard(data) {
    const f = data.fundamentals || {};
    const s = data.score || {};

    document.getElementById("stock-symbol").textContent = f.symbol || data.ticker || "—";
    document.getElementById("stock-price").textContent = isFinite(Number(f.price)) ? Number(f.price).toFixed(2) : "—";

    const sym = (f.symbol || data.ticker || "").toLowerCase();
    if (sym) {
      document.getElementById("stockanalysis-link").href =
        `https://stockanalysis.com/stocks/${sym}/financials/?p=quarterly`;
    }

    document.getElementById("val-mcap").textContent = formatMoney(f.marketcap);
    document.getElementById("val-div").textContent =
      isFinite(Number(f.dividendyield)) && Number(f.dividendyield) > 0 ? formatPct(f.dividendyield) : "—";
    document.getElementById("val-pe-trail").textContent =
      isFinite(Number(f.petrailing)) && Number(f.petrailing) > 0 ? Number(f.petrailing).toFixed(2) : "—";
    document.getElementById("val-pe-fwd").textContent =
      isFinite(Number(f.peforward)) && Number(f.peforward) > 0 ? Number(f.peforward).toFixed(2) : "—";
    document.getElementById("val-de").textContent =
      isFinite(Number(f.debttoequity)) ? Number(f.debttoequity).toFixed(2) : "—";
    document.getElementById("val-pb").textContent =
      isFinite(Number(f.pricetobook)) && Number(f.pricetobook) > 0 ? Number(f.pricetobook).toFixed(2) : "—";

    document.getElementById("val-revg-ann").textContent = formatPct(f.revenuegrowthannualyoy);
    document.getElementById("val-revg-qtr").textContent = formatPct(f.revenuegrowthquarterlyyoy);
    document.getElementById("val-epsg-ann").textContent = formatPct(f.epsgrowthannualyoy);
    document.getElementById("val-epsg-qtr").textContent = formatPct(f.epsgrowthquarterlyyoy);

    document.getElementById("val-1y-high").textContent =
      isFinite(Number(f.high52w)) && Number(f.high52w) > 0 ? Number(f.high52w).toFixed(2) : "—";
    document.getElementById("val-1y-low").textContent =
      isFinite(Number(f.low52w)) && Number(f.low52w) > 0 ? Number(f.low52w).toFixed(2) : "—";

    const scoreNumber =
      (s.finalgrade ?? s.score_100 ?? s.totalscore);
    document.getElementById("score-number").textContent =
      isFinite(Number(scoreNumber)) ? Number(scoreNumber).toFixed(1) : "—";
    document.getElementById("score-rating").textContent = s.rating || "—";

    const tbody = document.querySelector("#eps-table tbody");
    tbody.innerHTML = "";
    const eps = Array.isArray(f.epshistory5q) ? f.epshistory5q : [];
    eps.forEach((q) => {
      const tr = document.createElement("tr");
      const epsVal = isFinite(Number(q.eps)) ? Number(q.eps).toFixed(2) : "—";
      tr.innerHTML = `<td>${q.date || "—"}</td><td>${epsVal}</td>`;
      tbody.appendChild(tr);
    });

    const breakdownList = document.getElementById("score-breakdown-list");
    breakdownList.innerHTML = "";
    const breakdown = Array.isArray(s.breakdown) ? s.breakdown : [];
    breakdown.forEach((item) => {
      const li = document.createElement("li");
      li.textContent = item;
      breakdownList.appendChild(li);
    });
  }

  function drawChart(chartData) {
    const container = document.getElementById("tv-chart");
    container.innerHTML = "";

    if (tvChart) {
      try { tvChart.remove(); } catch {}
      tvChart = null;
    }

    const candles = chartData && Array.isArray(chartData.candles) ? chartData.candles : [];
    if (!candles.length) {
      container.innerHTML = `<div style="padding:14px;color:#94a3b8">No chart data available</div>`;
      return;
    }

    const formatted = candles.map(c => ({
      time: `${c.date}-01`,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));

    const width = container.getBoundingClientRect().width || 900;

    tvChart = LightweightCharts.createChart(container, {
      width,
      height: 480,
      layout: { background: { type: "solid", color: "transparent" }, textColor: "#94a3b8" },
      grid: { vertLines: { color: "#334155" }, horzLines: { color: "#334155" } },
      rightPriceScale: { borderColor: "#334155" },
      timeScale: { borderColor: "#334155" },
    });

    const series = tvChart.addCandlestickSeries({
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    });

    series.setData(formatted);
    tvChart.timeScale().fitContent();

    window.addEventListener("resize", () => {
      if (!tvChart) return;
      const w = container.getBoundingClientRect().width || 900;
      tvChart.applyOptions({ width: w });
    });
  }
});
