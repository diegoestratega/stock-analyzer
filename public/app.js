async function analyzeTicker() {
  const tickerEl = document.getElementById('ticker');
  const resultDiv = document.getElementById('result');
  const ticker = (tickerEl.value || '').trim().toUpperCase();

  if (!ticker) {
    resultDiv.textContent = 'Enter a ticker (example: AAPL).';
    return;
  }

  resultDiv.textContent = 'Analyzing...';

  try {
    const resp = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker })
    });

    const data = await resp.json();

    if (!resp.ok) {
      resultDiv.textContent = `Error (${resp.status}): ${data.detail || 'Unknown error'}`;
      return;
    }

    resultDiv.innerHTML = `
      <h2>${data.ticker} — Score: ${data.score}/100</h2>
      <pre>${JSON.stringify(data, null, 2)}</pre>
    `;
  } catch (e) {
    resultDiv.textContent = 'Network error calling /api/analyze.';
  }
}

document.getElementById('analyzeBtn').addEventListener('click', (e) => {
  e.preventDefault();
  analyzeTicker();
});

document.getElementById('ticker').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') analyzeTicker();
});
