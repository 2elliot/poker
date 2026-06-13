// Tournament page logic for the static archive.
// Loads pre-recorded transcripts from /static/data/transcripts.json,
// picks one at random, and replays the events at the user-selected speed.

const MAX_PLAYERS = 10;
const DEFAULT_STARTING_CHIPS = 1000;

const state = {
    transcripts: [],
    botStats: {},  // name -> profile (from bots.json)
    leaderboard: [],  // for sidebar elo/creator
    currentTranscript: null,
    eventIdx: 0,
    replayTimer: null,

    speed: 4,
    tablePlayers: [],
    spectatorPlayers: [],
    communityCards: [],
    pot: 0,
    gamesPlayed: 0,
    handInProgress: false,
};

// ============================================================================
// INIT
// ============================================================================

async function init() {
    try {
        const [tRes, lRes, bRes] = await Promise.all([
            fetch('/static/data/transcripts.json'),
            fetch('/static/data/leaderboard.json'),
            fetch('/static/data/bots.json'),
        ]);
        const tData = await tRes.json();
        const lData = await lRes.json();
        const bData = await bRes.json();
        state.transcripts = tData.transcripts || [];
        state.leaderboard = lData.leaderboard || [];
        state.botStats = bData.bots || {};
    } catch (e) {
        logToConsole('Failed to load archive data: ' + e.message, 'event-error');
        return;
    }

    if (state.transcripts.length === 0) {
        logToConsole('No transcripts available.', 'event-error');
        return;
    }

    logToConsole('Archived poker tournament loaded', 'event-phase');
    document.getElementById('gameStatus').textContent = 'Replay Mode';

    let resizeTimer;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => positionSeats(), 100);
    });

    startNextTranscript();
    scheduleReplayTick();
}

function startNextTranscript() {
    // Pick a random transcript (different from the last if possible)
    const choices = state.transcripts;
    let pick = choices[Math.floor(Math.random() * choices.length)];
    if (state.currentTranscript && choices.length > 1) {
        let tries = 0;
        while (pick === state.currentTranscript && tries < 5) {
            pick = choices[Math.floor(Math.random() * choices.length)];
            tries++;
        }
    }
    state.currentTranscript = pick;
    state.eventIdx = 0;
    state.tablePlayers = [];
    state.communityCards = [];
    state.pot = 0;
    state.gamesPlayed = 0;
    state.handInProgress = false;
    renderTable();
}

function scheduleReplayTick() {
    if (state.replayTimer) clearInterval(state.replayTimer);
    const interval = Math.max(100, 800 / state.speed);
    state.replayTimer = setInterval(replayTick, interval);
}

function replayTick() {
    if (!state.currentTranscript) return;
    const events = state.currentTranscript.events;
    if (state.eventIdx >= events.length) {
        // Transcript exhausted — short pause then queue next one
        if (state.replayTimer) clearInterval(state.replayTimer);
        setTimeout(() => {
            logToConsole('--- Loading next recorded match ---', 'event-phase');
            startNextTranscript();
            scheduleReplayTick();
        }, 2500);
        return;
    }
    const evt = events[state.eventIdx++];
    handleEvent(evt);
}

function changeSpectatorSpeed(delta) {
    const speeds = [0.25, 0.5, 1, 2, 4, 8];
    let idx = speeds.indexOf(state.speed);
    if (idx === -1) idx = 4;
    idx = Math.max(0, Math.min(speeds.length - 1, idx + delta));
    state.speed = speeds[idx];
    document.getElementById('spectatorSpeedValue').textContent = state.speed + 'x';
    scheduleReplayTick();
}

// ============================================================================
// EVENT HANDLERS
// ============================================================================

function handleEvent(data) {
    const event = data.event;

    if (event === 'match_start') {
        state.tablePlayers = [];
        state.communityCards = [];
        state.pot = 0;
        state.spectatorPlayers = data.players || [];
        data.players.forEach((name, i) => {
            const chips = data.chips[name] || DEFAULT_STARTING_CHIPS;
            state.tablePlayers.push({
                id: name, botId: name, name, chips, bet: 0,
                cards: [], folded: false, allIn: false,
            });
        });
        logToConsole('=== NEW MATCH STARTED ===', 'event-phase');
        logToConsole(`Players: ${data.players.join(', ')}`, 'event-action');
        renderSidebar();
        renderTable();
        updateStatus();

    } else if (event === 'deal') {
        state.handInProgress = true;
        state.communityCards = [];
        const activePlayers = data.players || [];
        state.tablePlayers.forEach(p => {
            p.folded = false;
            p.allIn = false;
            p.cards = [];
            p.bet = 0;
            if (!activePlayers.includes(p.id)) p.chips = 0;
        });
        if (data.hole_cards) {
            for (const [pid, cards] of Object.entries(data.hole_cards)) {
                const player = findPlayer(pid);
                if (player) player.cards = cards.map(parseCardString);
            }
        }
        if (data.chips) {
            for (const [pid, chips] of Object.entries(data.chips)) {
                const player = findPlayer(pid);
                if (player) player.chips = chips;
            }
        }
        if (data.bets) {
            for (const [pid, bet] of Object.entries(data.bets)) {
                const player = findPlayer(pid);
                if (player) player.bet = bet;
            }
        }
        state.pot = data.pot || 0;

        const handNum = data.hand_number || '';
        logToConsole(`--- HAND #${handNum} ---`, 'event-phase');
        if (data.bets) {
            const blindPosts = Object.entries(data.bets)
                .filter(([, bet]) => bet > 0)
                .sort((a, b) => a[1] - b[1]);
            for (const [pid, bet] of blindPosts) {
                logToConsole(`${pid} posts blind: ${bet}`, 'event-action');
            }
        }
        const handInfo = document.getElementById('spectatorHandInfo');
        if (handInfo) handInfo.textContent = `Hand #${handNum}`;
        renderTable();
        updateStatus();

    } else if (event === 'action') {
        logToConsole(formatActionName(data.player, data.action, data.amount), 'event-action');
        const acting = findPlayer(data.player);
        if (data.action === 'fold' && acting) acting.folded = true;
        if (data.action === 'all_in' && acting) acting.allIn = true;
        if (data.chips) {
            for (const [pid, chips] of Object.entries(data.chips)) {
                const player = findPlayer(pid);
                if (player) {
                    player.chips = chips;
                    if (chips === 0 && !player.folded) player.allIn = true;
                }
            }
        }
        if (data.bets) {
            for (const [pid, bet] of Object.entries(data.bets)) {
                const player = findPlayer(pid);
                if (player) player.bet = bet;
            }
        }
        state.pot = data.pot || 0;
        renderTable();

    } else if (event === 'community') {
        state.communityCards = (data.cards || []).map(parseCardString);
        state.pot = data.pot || 0;
        logToConsole(`--- ${(data.phase || '').toUpperCase()}: ${formatCommunityCards(state.communityCards)} ---`, 'event-phase');
        renderTable();

    } else if (event === 'showdown') {
        state.communityCards = (data.community_cards || []).map(parseCardString);
        state.pot = 0;
        if (data.player_hands) {
            for (const [pid, cards] of Object.entries(data.player_hands)) {
                const player = findPlayer(pid);
                if (player) player.cards = cards.map(parseCardString);
            }
        }
        const winners = data.winners || [];
        logToConsole(`WINNERS: ${winners.join(', ')}`, 'event-winner');
        if (data.chips) {
            for (const [pid, chips] of Object.entries(data.chips)) {
                const player = findPlayer(pid);
                if (player) player.chips = chips;
            }
        }
        state.gamesPlayed++;
        state.handInProgress = false;
        state.tablePlayers.forEach(p => { p.bet = 0; });
        renderTable();
        updateStatus();

    } else if (event === 'match_end') {
        const winner = data.winner || 'unknown';
        logToConsole(`=== MATCH COMPLETE - Winner: ${winner} ===`, 'event-winner');
        if (data.results) {
            data.results.forEach(r => {
                logToConsole(`  ${r.position}. ${r.name} (${r.chips} chips)`, 'event-action');
            });
        }
        state.handInProgress = false;
        updateStatus();
    }
}

// ============================================================================
// SIDEBAR / TABLE / CONSOLE RENDERING
// ============================================================================

function renderSidebar() {
    document.querySelectorAll('.seat-highlighted').forEach(el => el.classList.remove('seat-highlighted'));
    const listEl = document.getElementById('livePlayerList');
    const players = state.spectatorPlayers || [];
    if (players.length === 0) {
        listEl.innerHTML = '<div style="padding: 20px; color: #666; text-align: center;">No active match</div>';
        return;
    }
    listEl.innerHTML = players.map(name => {
        const stats = state.botStats[name] || state.leaderboard.find(b => b.name === name) || {};
        const creator = stats.creator || 'demo';
        const elo = stats.elo ? Math.round(stats.elo) : '--';
        const seatIdx = state.tablePlayers.findIndex(p => p && p.name === name);
        return `
            <div class="bot-item spectator-bot-item"
                 onclick="openBotProfile('${name}')"
                 onmouseenter="highlightSeat(${seatIdx})"
                 onmouseleave="unhighlightSeat(${seatIdx})">
                <div class="spectator-bot-header">
                    <div class="bot-name">${name}</div>
                    <div class="spectator-bot-elo"><span class="spectator-bot-elo-label">Elo</span> ${elo}</div>
                </div>
                <div class="spectator-bot-creator">by ${creator}</div>
                <div class="spectator-bot-view-stats">View Stats &#8250;</div>
            </div>
        `;
    }).join('');
}

function highlightSeat(seatIdx) {
    if (seatIdx < 0) return;
    const seat = document.querySelector(`[data-seat="${seatIdx}"]`);
    if (seat) seat.classList.add('seat-highlighted');
}
function unhighlightSeat(seatIdx) {
    if (seatIdx < 0) return;
    const seat = document.querySelector(`[data-seat="${seatIdx}"]`);
    if (seat) seat.classList.remove('seat-highlighted');
}

function getStyleLabel(vpip, pfr) {
    if (!vpip && !pfr) return '';
    if (vpip > 40 && pfr > 25) return 'LAG';
    if (vpip > 40) return 'LP';
    if (pfr > 20) return 'TAG';
    return 'TP';
}
function getStyleClass(label) {
    const map = { 'LAG': 'style-lag', 'LP': 'style-lp', 'TAG': 'style-tag', 'TP': 'style-tp' };
    return map[label] || '';
}
function getStyleFullName(label) {
    const map = { 'LAG': 'Loose-Aggressive', 'LP': 'Loose-Passive', 'TAG': 'Tight-Aggressive', 'TP': 'Tight-Passive' };
    return map[label] || label;
}

// Bot profile modal — sources data from already-loaded bots.json
async function openBotProfile(botName) {
    const modal = document.getElementById('botProfileModal');
    if (!modal) return;
    const content = document.getElementById('botProfileContent');
    modal.style.display = 'flex';
    content.innerHTML = '<div style="text-align: center; padding: 40px;"><span class="loading-spinner"></span> Loading...</div>';

    const s = state.botStats[botName];
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
}

function closeBotProfile(event) {
    const modal = document.getElementById('botProfileModal');
    if (!modal) return;
    if (event && event.target !== modal) return;
    modal.style.display = 'none';
}

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeBotProfile();
});

function parseCardString(cardStr) {
    if (typeof cardStr === 'object') return cardStr;
    if (!cardStr || cardStr.length < 2) return { value: '?', suit: '?' };
    const suit = cardStr.slice(-1);
    const value = cardStr.slice(0, -1);
    return { value, suit };
}

function logToConsole(message, className = '') {
    const consoleContent = document.getElementById('consoleContent');
    if (!consoleContent) return;
    const line = document.createElement('div');
    line.className = `console-line ${className}`;
    const timestamp = new Date().toLocaleTimeString();
    line.textContent = `[${timestamp}] ${message}`;
    const nearBottom = consoleContent.scrollHeight - consoleContent.scrollTop - consoleContent.clientHeight < 40;
    consoleContent.appendChild(line);
    while (consoleContent.children.length > 500) {
        consoleContent.removeChild(consoleContent.firstChild);
    }
    if (nearBottom) consoleContent.scrollTop = consoleContent.scrollHeight;
}

function clearConsole() {
    document.getElementById('consoleContent').innerHTML = '';
}

function positionSeats() {
    const playerCount = state.tablePlayers.filter(p => p).length;
    if (playerCount === 0) return;
    const table = document.getElementById('pokerTable');
    if (!table) return;
    const tableW = table.offsetWidth;
    const tableH = table.offsetHeight;
    const sampleSeat = document.querySelector('.player-seat:not(.empty)');
    const seatW = sampleSeat ? sampleSeat.offsetWidth : 120;
    const seatH = sampleSeat ? sampleSeat.offsetHeight : 80;
    const padX = (seatW / 2 / tableW) * 100 + 2;
    const padY = (seatH / 2 / tableH) * 100 + 2;
    const rx = Math.min(52, 50 - padX + 4);
    const ry = Math.min(52, 50 - padY + 4);
    const cx = 50, cy = 50;
    for (let i = 0; i < MAX_PLAYERS; i++) {
        const seat = document.querySelector(`[data-seat="${i}"]`);
        if (!state.tablePlayers[i]) continue;
        const angle = (2 * Math.PI * i / playerCount) - Math.PI / 2;
        const x = cx + rx * Math.cos(angle);
        const y = cy + ry * Math.sin(angle);
        seat.style.left = x + '%';
        seat.style.top = y + '%';
        seat.style.transform = 'translate(-50%, -50%)';
    }
}

function renderTable() {
    const emptyMessage = document.getElementById('emptyMessage');
    const pokerTable = document.getElementById('pokerTable');
    if (state.tablePlayers.length === 0) {
        emptyMessage.style.display = 'block';
        pokerTable.style.display = 'none';
        return;
    }
    emptyMessage.style.display = 'none';
    pokerTable.style.display = 'block';
    positionSeats();
    for (let i = 0; i < MAX_PLAYERS; i++) {
        const seat = document.querySelector(`[data-seat="${i}"]`);
        const player = state.tablePlayers[i];
        if (player) {
            const isAllIn = player.allIn && player.chips <= 0;
            const isEliminated = player.chips <= 0 && !isAllIn;
            const isFolded = player.folded;
            seat.classList.remove('empty');
            seat.innerHTML = `
                <div class="player-info ${isEliminated ? 'eliminated' : ''} ${isAllIn ? 'all-in' : ''} ${isFolded ? 'folded' : ''}">
                    <div class="player-name">${player.name}</div>
                    <div class="player-chips">${isAllIn ? 'ALL-IN' : player.chips}</div>
                    ${player.bet > 0 ? `<div class="player-bet">Bet: ${player.bet}</div>` : ''}
                    ${isEliminated ? '<div class="player-status eliminated-tag">ELIMINATED</div>' : ''}
                    ${isAllIn ? '<div class="player-status allin-tag">ALL-IN</div>' : ''}
                    ${isFolded && !isEliminated ? '<div class="player-status folded-tag">FOLDED</div>' : ''}
                </div>
                <div class="player-cards">${renderPlayerCards(player)}</div>
            `;
        } else {
            seat.classList.add('empty');
            seat.innerHTML = '';
            seat.style.left = '';
            seat.style.top = '';
            seat.style.transform = '';
        }
    }
    const communityCardsEl = document.getElementById('communityCards');
    if (state.communityCards && state.communityCards.length > 0) {
        communityCardsEl.innerHTML = state.communityCards.map(card => renderCard(card)).join('');
    } else {
        communityCardsEl.innerHTML = '';
    }
    document.getElementById('potAmount').textContent = `${state.pot || 0}`;
}

function renderPlayerCards(player) {
    if (!player.cards || player.cards.length === 0) return '';
    return player.cards.map(card => renderCard(card)).join('');
}

function renderCard(card) {
    if (!card) return '';
    const suit = card.suit || card.s;
    const value = card.value || card.v || card.rank;
    const color = (suit === '♥' || suit === '♦') ? 'red' : 'black';
    return `<div class="card ${color}">${value}${suit}</div>`;
}

function formatActionName(player, action, amount) {
    const p = findPlayer(player);
    const name = p ? p.name : player;
    switch (action) {
        case 'fold': return `${name} folds`;
        case 'check': return `${name} checks`;
        case 'call': return `${name} calls`;
        case 'raise': return `${name} raises to ${amount}`;
        case 'all_in': return `${name} goes ALL-IN`;
        default: return `${name}: ${action}`;
    }
}

function formatCommunityCards(cards) {
    return cards.map(c => `${c.value}${c.suit}`).join(' ');
}

function findPlayer(pid) {
    return state.tablePlayers.find(p => p.id === pid);
}

function updateStatus() {
    document.getElementById('playerCount').textContent = state.tablePlayers.length;
    document.getElementById('gamesPlayed').textContent = state.gamesPlayed;
    document.getElementById('gameStatus').textContent = 'Replay Mode';
}

// ============================================================================
// RESIZE HANDLES
// ============================================================================

const sidebar = document.getElementById('sidebar');
const sidebarResize = document.getElementById('sidebarResize');
if (sidebarResize) sidebarResize.addEventListener('mousedown', initSidebarResize);

function initSidebarResize(e) {
    e.preventDefault();
    window.addEventListener('mousemove', resizeSidebar);
    window.addEventListener('mouseup', stopSidebarResize);
}
function resizeSidebar(e) {
    const newWidth = e.clientX;
    if (newWidth > 10 && newWidth < 500) sidebar.style.width = newWidth + 'px';
}
function stopSidebarResize() {
    window.removeEventListener('mousemove', resizeSidebar);
    window.removeEventListener('mouseup', stopSidebarResize);
}

const consoleEl = document.getElementById('console');
const consoleResize = document.getElementById('consoleResize');
if (consoleResize) consoleResize.addEventListener('mousedown', initConsoleResize);

function initConsoleResize(e) {
    e.preventDefault();
    window.addEventListener('mousemove', resizeConsole);
    window.addEventListener('mouseup', stopConsoleResize);
}
function resizeConsole(e) {
    const containerHeight = document.querySelector('.main-content').clientHeight;
    const newHeight = containerHeight - e.clientY;
    if (newHeight > 20 && newHeight < 500) consoleEl.style.height = newHeight + 'px';
}
function stopConsoleResize() {
    window.removeEventListener('mousemove', resizeConsole);
    window.removeEventListener('mouseup', stopConsoleResize);
}

init();
