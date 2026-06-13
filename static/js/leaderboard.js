// Leaderboard page logic — reads from /static/data/leaderboard.json (static archive).

let allBots = [];

document.addEventListener('DOMContentLoaded', loadLeaderboard);

async function loadLeaderboard() {
    const container = document.getElementById('leaderboardContent');
    try {
        const response = await fetch('/static/data/leaderboard.json');
        const data = await response.json();

        if (!data.leaderboard || data.leaderboard.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <h3>No rankings</h3>
                    <p>This archive ships with placeholder data.</p>
                </div>
            `;
            return;
        }

        allBots = data.leaderboard;
        applyFilters();
    } catch (error) {
        console.error('Error loading leaderboard:', error);
        container.innerHTML = '<div class="alert alert-error show">Failed to load leaderboard</div>';
    }
}

function applyFilters() {
    const minHands = parseInt(document.getElementById('filterMinHands').value) || 0;
    const sortBy = document.getElementById('filterSort').value;

    let filtered = allBots.filter(b => b.hands_played >= minHands);

    filtered.sort((a, b) => {
        if (sortBy === 'elo') return b.elo - a.elo;
        if (sortBy === 'mbb') return (b.mbb_per_hand || -999) - (a.mbb_per_hand || -999);
        if (sortBy === 'win_rate') return b.win_rate - a.win_rate;
        if (sortBy === 'hands_played') return b.hands_played - a.hands_played;
        return 0;
    });

    renderTable(filtered);

    if (document.getElementById('scatterContainer').style.display !== 'none') {
        drawScatterChart(filtered);
    }
}

function renderTable(bots) {
    const container = document.getElementById('leaderboardContent');

    if (bots.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <h3>No bots match the filter</h3>
                <p>Try lowering the minimum hands requirement.</p>
            </div>
        `;
        return;
    }

    const rows = bots.map((bot, i) => {
        const mbb = bot.mbb_per_hand !== null ? bot.mbb_per_hand : '--';
        const mbbClass = bot.mbb_per_hand > 0 ? 'stats-cell-won' : bot.mbb_per_hand < 0 ? 'stats-cell-lost' : '';
        const calibrated = bot.calibrated ? '' : '<span class="lb-calibrating" title="Needs 5,000+ hands">calibrating</span>';
        const style = getStyleLabel(bot.vpip, bot.pfr);
        const styleClass = getStyleClass(style);

        return `<tr class="lb-row" onclick="openBotProfile('${bot.name}')">
            <td class="lb-rank">${i + 1}</td>
            <td>
                <div class="lb-bot-name">${bot.name}</div>
                <div class="lb-bot-creator">by ${bot.creator || 'unknown'} ${calibrated}</div>
            </td>
            <td class="lb-elo">${Math.round(bot.elo)}</td>
            <td class="${mbbClass}">${mbb}</td>
            <td>${bot.hands_played.toLocaleString()}</td>
            <td>${bot.win_rate}%</td>
            <td>${bot.tournaments_won}W / ${bot.tournaments_played}P</td>
            <td><span class="lb-style-tag ${styleClass}">${style || '--'}</span></td>
        </tr>`;
    }).join('');

    container.innerHTML = `
        <table class="stats-table lb-table">
            <thead>
                <tr>
                    <th>#</th>
                    <th>Bot</th>
                    <th>Elo</th>
                    <th>mbb/hand</th>
                    <th>Hands</th>
                    <th>Win Rate</th>
                    <th>Tournaments</th>
                    <th>Style</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>
    `;
}

// --- Style helpers ---

function getStyleLabel(vpip, pfr) {
    if (!vpip && !pfr) return '';
    if (vpip > 40 && pfr > 25) return 'LAG';
    if (vpip > 40) return 'LP';
    if (pfr > 20) return 'TAG';
    return 'TP';
}

function getStyleFullName(label) {
    const map = { 'LAG': 'Loose-Aggressive', 'LP': 'Loose-Passive', 'TAG': 'Tight-Aggressive', 'TP': 'Tight-Passive' };
    return map[label] || label;
}

function getStyleClass(label) {
    const map = { 'LAG': 'style-lag', 'LP': 'style-lp', 'TAG': 'style-tag', 'TP': 'style-tp' };
    return map[label] || '';
}

// --- Bot profile modal ---

let _botStatsCache = null;
async function _loadBotStats() {
    if (_botStatsCache) return _botStatsCache;
    const res = await fetch('/static/data/bots.json');
    const data = await res.json();
    _botStatsCache = data.bots || {};
    return _botStatsCache;
}

async function openBotProfile(botName) {
    const modal = document.getElementById('botProfileModal');
    const content = document.getElementById('botProfileContent');
    modal.style.display = 'flex';
    content.innerHTML = '<div style="text-align: center; padding: 40px;"><span class="loading-spinner"></span> Loading...</div>';

    try {
        const bots = await _loadBotStats();
        const s = bots[botName];
        if (!s) {
            content.innerHTML = `<div class="alert alert-error show">No stats for ${botName}</div>`;
            return;
        }
        const style = getStyleLabel(s.vpip, s.pfr);
        const mbb = s.mbb_per_hand !== null ? s.mbb_per_hand : '--';
        const mbbClass = s.mbb_per_hand > 0 ? 'stats-cell-won' : s.mbb_per_hand < 0 ? 'stats-cell-lost' : '';
        const netClass = s.net_chips >= 0 ? 'stats-cell-won' : 'stats-cell-lost';
        content.innerHTML = renderBotProfileHTML(s, style, mbb, mbbClass, netClass);
        if (s.elo_history && s.elo_history.length > 1) {
            drawEloChart(document.getElementById('eloChartCanvas'), s.elo_history);
        }
    } catch (error) {
        content.innerHTML = '<div class="alert alert-error show">Failed to load bot stats</div>';
    }
}

function closeBotProfile(event) {
    if (event && event.target !== document.getElementById('botProfileModal')) return;
    document.getElementById('botProfileModal').style.display = 'none';
}

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeBotProfile();
});

// --- VPIP vs PFR Scatter Chart ---

function toggleScatterChart() {
    const container = document.getElementById('scatterContainer');
    const label = document.getElementById('chartToggleLabel');
    if (container.style.display === 'none') {
        container.style.display = '';
        label.textContent = 'Hide Style Chart';
        const minHands = parseInt(document.getElementById('filterMinHands').value) || 0;
        drawScatterChart(allBots.filter(b => b.hands_played >= minHands));
    } else {
        container.style.display = 'none';
        label.textContent = 'Show Style Chart';
    }
}

function drawScatterChart(bots) {
    const canvas = document.getElementById('scatterChart');
    const ctx = canvas.getContext('2d');

    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const w = rect.width;
    const h = rect.height;
    ctx.clearRect(0, 0, w, h);

    const pad = { top: 30, right: 30, bottom: 50, left: 60 };
    const cw = w - pad.left - pad.right;
    const ch = h - pad.top - pad.bottom;

    const maxVpip = 100, maxPfr = 100;
    const midX = pad.left + cw * (40 / maxVpip);
    const midY = pad.top + ch * (1 - 25 / maxPfr);

    ctx.fillStyle = 'rgba(74, 144, 226, 0.06)';
    ctx.fillRect(pad.left, pad.top, midX - pad.left, midY - pad.top);
    ctx.fillStyle = 'rgba(226, 74, 74, 0.06)';
    ctx.fillRect(midX, pad.top, pad.left + cw - midX, midY - pad.top);
    ctx.fillStyle = 'rgba(100, 100, 100, 0.06)';
    ctx.fillRect(pad.left, midY, midX - pad.left, pad.top + ch - midY);
    ctx.fillStyle = 'rgba(240, 173, 78, 0.06)';
    ctx.fillRect(midX, midY, pad.left + cw - midX, pad.top + ch - midY);

    ctx.font = '11px Arial';
    ctx.fillStyle = '#555';
    ctx.textAlign = 'center';
    ctx.fillText('Tight-Aggressive', (pad.left + midX) / 2, pad.top + 16);
    ctx.fillText('Loose-Aggressive', (midX + pad.left + cw) / 2, pad.top + 16);
    ctx.fillText('Tight-Passive', (pad.left + midX) / 2, pad.top + ch - 6);
    ctx.fillText('Loose-Passive', (midX + pad.left + cw) / 2, pad.top + ch - 6);

    ctx.strokeStyle = '#333';
    ctx.lineWidth = 0.5;
    for (let v = 0; v <= maxVpip; v += 20) {
        const x = pad.left + (v / maxVpip) * cw;
        ctx.beginPath(); ctx.moveTo(x, pad.top); ctx.lineTo(x, pad.top + ch); ctx.stroke();
    }
    for (let p = 0; p <= maxPfr; p += 20) {
        const y = pad.top + ch - (p / maxPfr) * ch;
        ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + cw, y); ctx.stroke();
    }

    ctx.strokeStyle = '#555';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(midX, pad.top); ctx.lineTo(midX, pad.top + ch); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(pad.left, midY); ctx.lineTo(pad.left + cw, midY); ctx.stroke();
    ctx.setLineDash([]);

    ctx.strokeStyle = '#666';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.left, pad.top);
    ctx.lineTo(pad.left, pad.top + ch);
    ctx.lineTo(pad.left + cw, pad.top + ch);
    ctx.stroke();

    ctx.fillStyle = '#888';
    ctx.font = '12px Arial';
    ctx.textAlign = 'center';
    ctx.fillText('VPIP %', pad.left + cw / 2, h - 8);
    for (let v = 0; v <= maxVpip; v += 20) {
        const x = pad.left + (v / maxVpip) * cw;
        ctx.fillText(v, x, pad.top + ch + 18);
    }

    ctx.save();
    ctx.translate(14, pad.top + ch / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText('PFR %', 0, 0);
    ctx.restore();

    ctx.textAlign = 'right';
    for (let p = 0; p <= maxPfr; p += 20) {
        const y = pad.top + ch - (p / maxPfr) * ch;
        ctx.fillText(p, pad.left - 8, y + 4);
    }

    const colors = ['#4a90e2', '#e24a4a', '#5ac', '#f90', '#9c3', '#c6c', '#fc3', '#6cf', '#f6c', '#c9f'];
    const validBots = bots.filter(b => b.vpip > 0 || b.pfr > 0);

    validBots.forEach((bot, i) => {
        const x = pad.left + (bot.vpip / maxVpip) * cw;
        const y = pad.top + ch - (bot.pfr / maxPfr) * ch;
        const color = colors[i % colors.length];

        ctx.beginPath();
        ctx.arc(x, y, 6, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 1.5;
        ctx.stroke();

        ctx.fillStyle = '#e0e0e0';
        ctx.font = '10px Arial';
        ctx.textAlign = 'left';
        ctx.fillText(bot.name, x + 9, y + 4);
    });
}
