document.addEventListener("DOMContentLoaded", () => {
  const input = document.getElementById("ticker-input");
  const btn = document.getElementById("analyze-btn");
  const dashboard = document.getElementById("dashboard");
  const errorMsg = document.getElementById("error-message");

  let chart = null;
  let resizeObserver = null;

  const get = (obj, a, b, dflt = 0) =>
    (obj && obj[a] !== undefined ? obj[a] : (obj && obj[b] !== undefined ? obj[b] : dflt));

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
      const response = await fetch(`/api/analyze/${ticker}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Failed to fetch data");

      populateDashboard(data);
      dashboard.classList.remove("hidden");
      setTimeout(() => drawChart(data.chart), 50);
    } catch (err) {
      errorMsg.textContent = err.message;
      errorMsg.classList.remove("hidden");
    } finally {
      btn.textContent = "Analyze";
      btn.disabled = false;
    }
  }

  function populateDashboard(data) {
    const f = data.fundamentals || {};
    const s = data.score || {};

    const symbol = f.symbol || data.ticker || "";
    const price = get(f, "price", "price", 0);

    document.getElementById("stock-symbol").textContent = symbol;
    document.getElementById("stock-price").textContent = price ? `$${Number(price).toFixed(2)}` : "—";

    const sa = document.getElementById("stockanalysis-link");
    if (sa) sa.href = `https://stockanalysis.com/stocks/${String(symbol).toLowerCase()}/financials/?p=quarterly`;

    const formatMoney = (num) => {
      num = Number(num || 0);
      if (num >= 1e12) return `$${(num / 1e12).toFixed(2)}T`;
      if (num >= 1e9) return `$${(num / 1e9).toFixed(2)}B`;
      if (num >= 1e6) return `$${(num / 1e6).toFixed(2)}M`;
      return `$${num.toLocaleString()}`;
    };
    const formatPct = (num) => `${Number(num || 0).toFixed(2)}%`;

    // Key metrics (support both snake_case and legacy names)
    const mcap = get(f, "market_cap", "marketcap", 0);
    const peT = get(f, "pe_trailing", "petrailing", 0);
    const peF = get(f, "pe_forward", "peforward", 0);
    const pb = get(f, "price_to_book", "pricetobook", 0);
    const de = get(f, "debt_to_equity", "debttoequity", 0);
    const div = get(f, "dividend_yield", "dividendyield", 0);

    document.getElementById("val-mcap").textContent = mcap ? formatMoney(mcap) : "—";
    document.getElementById("val-pe-trail").textContent = peT > 0 ? Number(peT).toFixed(2) : "N/A";
    document.getElementById("val-pe-fwd").textContent = peF > 0 ? Number(peF).toFixed(2) : "N/A";
    document.getElementById("val-pb").textContent = pb > 0 ? Number(pb).toFixed(2) : "N/A";
    document.getElementById("val-de").textContent = Number(de || 0).toFixed(2);
    document.getElementById("val-div").textContent = div > 0 ? formatPct(div) : "None";

    const hi1y = get(f, "high_52w", "high52w", 0);
    const lo1y = get(f, "low_52w", "low52w", 0);

    const el1yHigh = document.getElementById("val-1y-high");
    if (el1yHigh) el1yHigh.textContent = hi1y > 0 ? `$${Number(hi1y).toFixed(2)}` : "N/A";
    const el1yLow = document.getElementById("val-1y-low");
    if (el1yLow) el1yLow.textContent = lo1y > 0 ? `$${Number(lo1y).toFixed(2)}` : "N/A";

    // Growth
    const revAnn = get(f, "revenue_growth_annual_yoy", "revenuegrowthannualyoy", 0);
    const revQ = get(f, "revenue_growth_quarterly_yoy", "revenuegrowthquarterlyyoy", 0);
    const epsAnn = get(f, "eps_growth_annual_yoy", "epsgrowthannualyoy", 0);
    const epsQ = get(f, "eps_growth_quarterly_yoy", "epsgrowthquarterlyyoy", 0);

    const revgAnnEl = document.getElementById("val-revg-ann");
    revgAnnEl.textContent = formatPct(revAnn);
    revgAnnEl.style.color = revAnn >= 0 ? "var(--success)" : "var(--danger)";

    const revgQEl = document.getElementById("val-revg-qtr");
    revgQEl.textContent = formatPct(revQ);
    revgQEl.style.color = revQ >= 0 ? "var(--success)" : "var(--danger)";

    const epsgAnnEl = document.getElementById("val-epsg-ann");
    epsgAnnEl.textContent = formatPct(epsAnn);
    epsgAnnEl.style.color = epsAnn >= 0 ? "var(--success)" : "var(--danger)";

    const epsgQEl = document.getElementById("val-epsg-qtr");
    epsgQEl.textContent = formatPct(epsQ);
    epsgQEl.style.color = epsQ >= 0 ? "var(--success)" : "var(--danger)";

    // EPS table
    const epsHist = f.eps_history_5q || f.epshistory5q || [];
    const tbody = document.querySelector("#eps-table tbody");
    if (tbody) {
      tbody.innerHTML = "";
      epsHist.forEach((q) => {
        const tr = document.createElement("tr");
        const dt = (q.date || "").toString();
        const eps = Number(q.eps || 0);
        tr.innerHTML = `<td>${dt}</td><td>${eps.toFixed(2)}</td>`;
        tbody.appendChild(tr);
      });
    }

    // Score (support both key names)
    const total = s.total_score ?? s.totalscore ?? 0;
    const grade = s.final_grade ?? s.finalgrade ?? total;

    const scoreNum = document.getElementById("score-number");
    const scoreRating = document.getElementById("score-rating");

    if (scoreNum) {
      scoreNum.textContent = `${Number(grade).toFixed(1)} ${s.rating || ""}`.trim();
      let color = "var(--danger)";
      if (grade >= 80) color = "var(--success)";
      else if (grade >= 45) color = "var(--warning)";
      scoreNum.style.color = color;
      if (scoreRating) {
        scoreRating.textContent = s.rating || "";
        scoreRating.style.color = color;
      }
    }

    const breakdownList = document.getElementById("score-breakdown-list");
    if (breakdownList) {
      breakdownList.innerHTML = "";
      (s.breakdown || []).forEach((item) => {
        const li = document.createElement("li");
        li.textContent = item;
        breakdownList.appendChild(li);
      });
    }
  }

  function drawChart(chartData) {
    const container = document.getElementById("tv-chart");
    if (!container) return;

    container.innerHTML = "";

    if (resizeObserver) {
      resizeObserver.disconnect();
      resizeObserver = null;
    }

    if (!chartData || !chartData.candles || chartData.candles.length === 0) {
      container.innerHTML = `<p style="padding:20px;color:#94a3b8">No chart data available.</p>`;
      return;
    }

    const formatted = chartData.candles.map((c) => ({
      time: `${c.date}-01`,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));

    const rect = container.getBoundingClientRect();
    const width = rect.width > 0 ? rect.width : 800;
    const height = 450;

    chart = LightweightCharts.createChart(container, {
      width,
      height,
      layout: { background: { type: "solid", color: "transparent" }, textColor: "#94a3b8" },
      grid: { vertLines: { color: "#334155" }, horzLines: { color: "#334155" } },
      crosshair: { mode: 0 },
      rightPriceScale: { borderColor: "#334155" },
      timeScale: { borderColor: "#334155" },
    });

    const series = chart.addCandlestickSeries({
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    });

    series.setData(formatted);

    const gh = chartData.global_high || chartData.globalhigh;
    const gl = chartData.global_low || chartData.globallow;

    if (gh && gh.price) {
      series.createPriceLine({ price: gh.price, color: "#22c55e", lineWidth: 2, lineStyle: 2, axisLabelVisible: true, title: "5Y High" });
    }
    if (gl && gl.price) {
      series.createPriceLine({ price: gl.price, color: "#ef4444", lineWidth: 2, lineStyle: 2, axisLabelVisible: true, title: "5Y Low" });
    }

    resizeObserver = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect?.width || 0;
      if (w > 0) chart.applyOptions({ width: w });
    });
    resizeObserver.observe(container);

    chart.timeScale().fitContent();
  }
});
