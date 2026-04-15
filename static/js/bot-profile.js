// Shared bot profile rendering — used by tournament and leaderboard pages

function renderBotProfileHTML(s, style, mbb, mbbClass, netClass) {
    const prevHtml = s.previous_version ? renderVersionComparison(s, s.previous_version) : '';
    const chartHtml = s.elo_history && s.elo_history.length > 1
        ? '<div class="elo-chart-container"><canvas id="eloChartCanvas"></canvas></div>'
        : '';

    return `
        <div class="bot-profile">
            <div class="bot-profile-header">
                <h2>${s.name}</h2>
                <span class="bot-profile-creator">by ${s.creator || 'unknown'}</span>
                ${style ? `<span class="lb-style-tag ${getStyleClass(style)}" style="margin-left: 10px;">${getStyleFullName(style)}</span>` : ''}
            </div>
            <div class="bot-profile-grid">
                <div class="bot-stat-card">
                    <div class="bot-stat-value lb-elo">${Math.round(s.elo)}</div>
                    <div class="bot-stat-label">Elo Rating</div>
                </div>
                <div class="bot-stat-card">
                    <div class="bot-stat-value ${mbbClass}">${mbb}</div>
                    <div class="bot-stat-label">mbb/hand</div>
                </div>
                <div class="bot-stat-card">
                    <div class="bot-stat-value">${s.win_rate}%</div>
                    <div class="bot-stat-label">Win Rate</div>
                </div>
                <div class="bot-stat-card">
                    <div class="bot-stat-value">${s.hands_played.toLocaleString()}</div>
                    <div class="bot-stat-label">Hands Played</div>
                </div>
            </div>
            ${prevHtml}
            ${chartHtml}
            <div class="bot-profile-details">
                <table class="bot-detail-table">
                    <tr><td>Hands Won</td><td>${s.hands_won.toLocaleString()}</td></tr>
                    <tr><td>Tournaments</td><td>${s.tournaments_won}W / ${s.tournaments_played}P</td></tr>
                    <tr><td>Chips Won</td><td class="stats-cell-won">+${s.chips_won.toLocaleString()}</td></tr>
                    <tr><td>Chips Lost</td><td class="stats-cell-lost">-${s.chips_lost.toLocaleString()}</td></tr>
                    <tr><td>Net Chips</td><td class="${netClass}">${s.net_chips >= 0 ? '+' : ''}${s.net_chips.toLocaleString()}</td></tr>
                    <tr><td>VPIP</td><td>${s.vpip}%</td></tr>
                    <tr><td>PFR</td><td>${s.pfr}%</td></tr>
                    <tr><td>Calibrated</td><td>${s.calibrated ? 'Yes' : 'No (< 5,000 hands)'}</td></tr>
                </table>
            </div>
        </div>
    `;
}

function renderVersionComparison(current, prev) {
    const eloDiff = Math.round(current.elo) - Math.round(prev.elo);
    const eloSign = eloDiff >= 0 ? '+' : '';
    const eloClass = eloDiff > 0 ? 'stats-cell-won' : eloDiff < 0 ? 'stats-cell-lost' : '';

    const wrDiff = current.win_rate - prev.win_rate;
    const wrSign = wrDiff >= 0 ? '+' : '';
    const wrClass = wrDiff > 0 ? 'stats-cell-won' : wrDiff < 0 ? 'stats-cell-lost' : '';

    return `
        <div class="version-comparison">
            <div class="version-comparison-title">vs. Previous Version</div>
            <div class="version-comparison-grid">
                <div class="version-stat">
                    <span class="version-stat-label">Elo</span>
                    <span class="version-stat-old">${Math.round(prev.elo)}</span>
                    <span class="version-stat-arrow">&rarr;</span>
                    <span class="version-stat-new">${Math.round(current.elo)}</span>
                    <span class="version-stat-diff ${eloClass}">${eloSign}${eloDiff}</span>
                </div>
                <div class="version-stat">
                    <span class="version-stat-label">Win Rate</span>
                    <span class="version-stat-old">${prev.win_rate}%</span>
                    <span class="version-stat-arrow">&rarr;</span>
                    <span class="version-stat-new">${current.win_rate}%</span>
                    <span class="version-stat-diff ${wrClass}">${wrSign}${wrDiff.toFixed(1)}%</span>
                </div>
                <div class="version-stat">
                    <span class="version-stat-label">Tournaments</span>
                    <span class="version-stat-old">${prev.tournaments_won}W/${prev.tournaments_played}P</span>
                    <span class="version-stat-arrow">&rarr;</span>
                    <span class="version-stat-new">${current.tournaments_won}W/${current.tournaments_played}P</span>
                </div>
            </div>
        </div>
    `;
}

function drawEloChart(canvas, history) {
    if (!canvas || history.length < 2) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.parentElement.getBoundingClientRect();
    const w = rect.width;
    const h = 120;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    ctx.scale(dpr, dpr);

    const elos = history.map(p => p.elo);
    const minElo = Math.min(...elos) - 10;
    const maxElo = Math.max(...elos) + 10;
    const range = maxElo - minElo || 1;

    const pad = { top: 15, bottom: 20, left: 40, right: 10 };
    const plotW = w - pad.left - pad.right;
    const plotH = h - pad.top - pad.bottom;

    // Grid lines
    ctx.strokeStyle = '#333';
    ctx.lineWidth = 0.5;
    const gridSteps = 4;
    ctx.font = '10px sans-serif';
    ctx.fillStyle = '#888';
    ctx.textAlign = 'right';
    for (let i = 0; i <= gridSteps; i++) {
        const y = pad.top + (plotH * i / gridSteps);
        const val = Math.round(maxElo - (range * i / gridSteps));
        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(w - pad.right, y);
        ctx.stroke();
        ctx.fillText(val, pad.left - 5, y + 3);
    }

    // Line
    ctx.strokeStyle = '#4a90e2';
    ctx.lineWidth = 1.5;
    ctx.lineJoin = 'round';
    ctx.beginPath();
    for (let i = 0; i < elos.length; i++) {
        const x = pad.left + (i / (elos.length - 1)) * plotW;
        const y = pad.top + plotH - ((elos[i] - minElo) / range) * plotH;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    }
    ctx.stroke();

    // 1200 baseline
    if (minElo < 1200 && maxElo > 1200) {
        const baseY = pad.top + plotH - ((1200 - minElo) / range) * plotH;
        ctx.strokeStyle = '#555';
        ctx.lineWidth = 0.5;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(pad.left, baseY);
        ctx.lineTo(w - pad.right, baseY);
        ctx.stroke();
        ctx.setLineDash([]);
    }

    // Match count label
    ctx.fillStyle = '#666';
    ctx.textAlign = 'center';
    ctx.fillText(elos.length + ' matches', w / 2, h - 2);
}
