// API Configuration
const API_BASE_URL = window.location.hostname === 'localhost'
    ? 'http://localhost:5000/api'
    : '/api';

// Game State
const MIN_PLAYERS = 2;
const MAX_PLAYERS = 10;
const DEFAULT_STARTING_CHIPS = 1000;

const state = {
    // Mode: 'spectator' or 'custom'
    mode: 'spectator',

    // Custom table state
    availableBots: [],
    tablePlayers: [],
    isPlaying: false,
    speed: 1,
    gameInterval: null,
    tournamentInitialized: false,
    statistics: {},
    gamesPlayed: 0,
    chipHistory: {},
    eventSource: null,
    communityCards: [],
    pot: 0,
    stepping: false,
    handInProgress: false,

    // Spectator state
    spectatorSpeed: 4,
    spectatorLastSeq: 0,
    spectatorPollTimer: null,
    spectatorReplayTimer: null,
    spectatorEventQueue: [],      // events fetched but not yet rendered
    spectatorPlayers: [],         // player list for current match
    spectatorMatch: null,         // current match summary
};

// ============================================================================
// INITIALIZATION
// ============================================================================

async function init() {
    // Start in spectator mode
    setMode('spectator');
    setupLogStreaming();
    updateStatus();
    logToConsole('Poker tournament system initialized', 'event-phase');
}

// ============================================================================
// MODE SWITCHING
// ============================================================================

function setMode(mode) {
    state.mode = mode;

    // Update toggle buttons
    document.getElementById('spectatorModeBtn').classList.toggle('active', mode === 'spectator');
    document.getElementById('customModeBtn').classList.toggle('active', mode === 'custom');

    // Toggle sidebar content
    document.getElementById('spectatorSidebar').style.display = mode === 'spectator' ? '' : 'none';
    document.getElementById('customSidebar').style.display = mode === 'custom' ? '' : 'none';

    // Toggle controls
    document.getElementById('spectatorControls').style.display = mode === 'spectator' ? '' : 'none';
    document.getElementById('customControls').style.display = mode === 'custom' ? '' : 'none';

    if (mode === 'spectator') {
        // Stop custom game if running
        stopGameLoop();
        state.isPlaying = false;

        // Reset spectator table state
        state.tablePlayers = [];
        state.communityCards = [];
        state.pot = 0;
        state.gamesPlayed = 0;

        // Update empty message
        const emptyMsg = document.getElementById('emptyMessage');
        emptyMsg.querySelector('h3').textContent = 'Waiting for Live Match';
        emptyMsg.querySelector('p').textContent = 'A match will begin automatically when bots are available';

        // Start spectator polling
        startSpectatorPolling();
        renderTable();
        document.getElementById('gameStatus').textContent = 'Spectator Mode';
    } else {
        // Stop spectator
        stopSpectatorPolling();

        // Clear table state for a fresh custom session
        state.tablePlayers = [];
        state.isPlaying = false;
        state.tournamentInitialized = false;
        state.handInProgress = false;
        state.communityCards = [];
        state.pot = 0;
        stopGameLoop();

        // Update empty message
        const emptyMsg = document.getElementById('emptyMessage');
        emptyMsg.querySelector('h3').textContent = 'No Players at Table';
        emptyMsg.querySelector('p').textContent = 'Select bots from the left panel to add them to the table';

        // Load bots for custom mode
        loadAvailableBots();
        renderTable();
        document.getElementById('gameStatus').textContent = 'Ready';
    }
    updateStatus();
}

// ============================================================================
// CUSTOM TABLE SETTINGS
// ============================================================================

function getCustomSettings() {
    return {
        startingChips: parseInt(document.getElementById('settingChips').value) || DEFAULT_STARTING_CHIPS,
        smallBlind: parseInt(document.getElementById('settingSmallBlind').value) || 10,
        bigBlind: parseInt(document.getElementById('settingBigBlind').value) || 20,
        blindInterval: parseInt(document.getElementById('settingBlindInterval').value) || 10,
    };
}

function toggleSettings() {
    const body = document.getElementById('settingsBody');
    const toggle = document.getElementById('settingsToggle');
    if (body.style.display === 'none') {
        body.style.display = '';
        toggle.innerHTML = '&#9660;';
    } else {
        body.style.display = 'none';
        toggle.innerHTML = '&#9654;';
    }
}

// ============================================================================
// SPECTATOR MODE
// ============================================================================

function startSpectatorPolling() {
    stopSpectatorPolling();
    state.spectatorLastSeq = 0;
    state.spectatorEventQueue = [];
    pollLiveMatch();
    // Poll every 1 second for new events
    state.spectatorPollTimer = setInterval(pollLiveMatch, 1000);
    // Start replay timer
    startSpectatorReplay();
}

function stopSpectatorPolling() {
    if (state.spectatorPollTimer) {
        clearInterval(state.spectatorPollTimer);
        state.spectatorPollTimer = null;
    }
    if (state.spectatorReplayTimer) {
        clearInterval(state.spectatorReplayTimer);
        state.spectatorReplayTimer = null;
    }
}

async function pollLiveMatch() {
    if (state.mode !== 'spectator') return;

    try {
        const response = await fetch(`${API_BASE_URL}/live-match?since=${state.spectatorLastSeq}`);
        const data = await response.json();

        if (!data.success) return;

        // Update match info
        state.spectatorMatch = data.match;

        // Queue new events for replay
        if (data.events && data.events.length > 0) {
            state.spectatorEventQueue.push(...data.events);
            state.spectatorLastSeq = data.last_seq;
        }

        // Update sidebar
        renderSpectatorSidebar();

    } catch (error) {
        // Silently retry on next poll
    }
}

function startSpectatorReplay() {
    if (state.spectatorReplayTimer) clearInterval(state.spectatorReplayTimer);
    const interval = Math.max(100, 800 / state.spectatorSpeed);
    state.spectatorReplayTimer = setInterval(replayNextEvent, interval);
}

function replayNextEvent() {
    if (state.mode !== 'spectator') return;
    if (state.spectatorEventQueue.length === 0) return;

    const event = state.spectatorEventQueue.shift();
    handleSpectatorEvent(event);
}

function handleSpectatorEvent(data) {
    const event = data.event;

    if (event === 'match_start') {
        // New match starting — set up players on the table
        state.tablePlayers = [];
        state.communityCards = [];
        state.pot = 0;
        state.spectatorPlayers = data.players || [];

        data.players.forEach((name, i) => {
            const chips = data.chips[name] || DEFAULT_STARTING_CHIPS;
            state.tablePlayers.push({
                id: name,
                botId: name,
                name: name,
                chips: chips,
                bet: 0,
                cards: [],
                folded: false,
                allIn: false,
            });
            // Preserve stats across matches — only init if new bot
            if (!state.statistics[name]) {
                state.statistics[name] = {
                    gamesPlayed: 0, wins: 0, winRate: 0,
                    totalChipsWon: 0, totalChipsLost: 0
                };
            }
            if (!state.chipHistory[name]) {
                state.chipHistory[name] = [];
            }
            state.chipHistory[name].push({ game: state.gamesPlayed, chips: chips });
        });

        logToConsole('=== NEW MATCH STARTED ===', 'event-phase');
        logToConsole(`Players: ${data.players.join(', ')}`, 'event-action');
        renderTable();
        renderStatistics();
        updateStatus();

    } else if (event === 'deal') {
        state.handInProgress = true;
        state.communityCards = [];
        state.tablePlayers.forEach(p => { p.folded = false; p.allIn = false; p.cards = []; p.bet = 0; });

        // Set hole cards
        if (data.hole_cards) {
            for (const [pid, cards] of Object.entries(data.hole_cards)) {
                const player = findPlayer(pid);
                if (player) {
                    player.cards = cards.map(parseCardString);
                }
            }
        }

        // Sync chips and bets
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

        // Log blinds
        if (data.bets) {
            const blindPosts = Object.entries(data.bets)
                .filter(([, bet]) => bet > 0)
                .sort((a, b) => a[1] - b[1]);
            for (const [pid, bet] of blindPosts) {
                logToConsole(`${pid} posts blind: ${bet}`, 'event-action');
            }
        }

        // Update hand info display
        const handInfo = document.getElementById('spectatorHandInfo');
        if (handInfo) handInfo.textContent = `Hand #${handNum}`;

        renderTable();
        updateStatus();

    } else if (event === 'action') {
        const actionStr = formatActionName(data.player, data.action, data.amount);
        logToConsole(actionStr, 'event-action');

        // Mark folded or all-in
        const actingPlayer = findPlayer(data.player);
        if (data.action === 'fold') {
            if (actingPlayer) actingPlayer.folded = true;
        }
        if (data.action === 'all_in') {
            if (actingPlayer) actingPlayer.allIn = true;
        }

        // Sync chips/bets
        if (data.chips) {
            for (const [pid, chips] of Object.entries(data.chips)) {
                const player = findPlayer(pid);
                if (player) {
                    player.chips = chips;
                    // Also detect all-in by chips hitting 0 after a non-fold action
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

        // Show hands
        if (data.player_hands) {
            for (const [pid, cards] of Object.entries(data.player_hands)) {
                const player = findPlayer(pid);
                if (player) player.cards = cards.map(parseCardString);
            }
        }

        const winners = data.winners || [];
        logToConsole(`WINNERS: ${winners.join(', ')}`, 'event-winner');

        // Update chips
        if (data.chips) {
            for (const [pid, chips] of Object.entries(data.chips)) {
                const player = findPlayer(pid);
                if (player) {
                    const prevChips = player.chips;
                    player.chips = chips;

                    const stats = state.statistics[player.id];
                    if (stats) {
                        const delta = chips - prevChips;
                        if (delta > 0) {
                            stats.wins++;
                            stats.totalChipsWon += delta;
                        } else if (delta < 0) {
                            stats.totalChipsLost += Math.abs(delta);
                        }
                        stats.gamesPlayed++;
                        stats.winRate = stats.gamesPlayed > 0
                            ? (stats.wins / stats.gamesPlayed * 100).toFixed(1) : 0;
                        state.chipHistory[player.id].push({
                            game: state.gamesPlayed + 1,
                            chips: chips
                        });
                    }
                }
            }
        }

        state.gamesPlayed++;
        state.handInProgress = false;
        state.tablePlayers.forEach(p => { p.bet = 0; });

        renderTable();
        renderStatistics();
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

function renderSpectatorSidebar() {
    // Clear all seat highlights before re-rendering (old DOM elements won't fire mouseleave)
    document.querySelectorAll('.seat-highlighted').forEach(el => el.classList.remove('seat-highlighted'));

    const match = state.spectatorMatch;
    const listEl = document.getElementById('livePlayerList');

    if (!match) {
        listEl.innerHTML = '<div style="padding: 20px; color: #666; text-align: center;">No active match</div>';
        return;
    }

    const players = match.players || [];

    listEl.innerHTML = players.map(name => {
        const botInfo = state.availableBots.find(b => b.name === name);
        const creator = botInfo && botInfo.creator ? botInfo.creator : '';
        const elo = botInfo ? Math.round(botInfo.elo) : '--';
        const winRate = botInfo ? botInfo.win_rate + '%' : '--';
        const hands = botInfo ? botInfo.hands_played.toLocaleString() : '--';
        // Find seat index for hover highlight
        const seatIdx = state.tablePlayers.findIndex(p => p && p.name === name);
        return `
            <div class="bot-item spectator-bot-item"
                 onclick="openBotProfile('${name}')"
                 onmouseenter="highlightSeat(${seatIdx})"
                 onmouseleave="unhighlightSeat(${seatIdx})">
                <div class="spectator-bot-header">
                    <div class="bot-name">${name}</div>
                    <div class="spectator-bot-elo">${elo}</div>
                </div>
                ${creator ? `<div class="spectator-bot-creator">by ${creator}</div>` : ''}
                <div class="spectator-bot-stats">
                    <span>WR ${winRate}</span>
                    <span>${hands} hands</span>
                </div>
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

async function openBotProfile(botName) {
    const modal = document.getElementById('botProfileModal');
    if (!modal) return;
    const content = document.getElementById('botProfileContent');
    modal.style.display = 'flex';
    content.innerHTML = '<div style="text-align: center; padding: 40px;"><span class="loading-spinner"></span> Loading...</div>';

    try {
        const response = await fetch(`/api/bot-stats/${encodeURIComponent(botName)}`);
        const data = await response.json();

        if (!data.success) {
            content.innerHTML = `<div class="alert alert-error show">${data.error || 'Failed to load'}</div>`;
            return;
        }

        const s = data.stats;
        const style = getStyleLabel(s.vpip, s.pfr);
        const mbb = s.mbb_per_hand !== null ? s.mbb_per_hand : '--';
        const mbbClass = s.mbb_per_hand > 0 ? 'stats-cell-won' : s.mbb_per_hand < 0 ? 'stats-cell-lost' : '';
        const netClass = s.net_chips >= 0 ? 'stats-cell-won' : 'stats-cell-lost';

        content.innerHTML = `
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
    } catch (error) {
        content.innerHTML = '<div class="alert alert-error show">Failed to load bot stats</div>';
    }
}

function closeBotProfile(event) {
    const modal = document.getElementById('botProfileModal');
    if (!modal) return;
    if (event && event.target !== modal) return;
    modal.style.display = 'none';
}

// Close modal on escape
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeBotProfile();
});

function changeSpectatorSpeed(delta) {
    const speeds = [0.25, 0.5, 1, 2, 4, 8];
    let idx = speeds.indexOf(state.spectatorSpeed);
    if (idx === -1) idx = 2; // default to 1x
    idx = Math.max(0, Math.min(speeds.length - 1, idx + delta));
    state.spectatorSpeed = speeds[idx];
    document.getElementById('spectatorSpeedValue').textContent = state.spectatorSpeed + 'x';
    startSpectatorReplay();
}

// Parse a card string like "A♠" into {value, suit} for renderCard
function parseCardString(cardStr) {
    if (typeof cardStr === 'object') return cardStr; // already parsed
    if (!cardStr || cardStr.length < 2) return { value: '?', suit: '?' };
    // Card format: "10♠" or "A♠" — suit is always the last character
    const suit = cardStr.slice(-1);
    const value = cardStr.slice(0, -1);
    return { value, suit };
}

// ============================================================================
// CUSTOM TABLE MODE (existing functionality)
// ============================================================================

// Load available bots from backend
async function loadAvailableBots() {
    try {
        const response = await fetch(`${API_BASE_URL}/bots`);
        const data = await response.json();

        if (data.success) {
            state.availableBots = data.bots;
            renderBotList();
            logToConsole(`Loaded ${data.bots.length} bots from backend`, 'event-action');
        } else {
            logToConsole(`Error loading bots: ${data.error}`, 'event-error');
        }
    } catch (error) {
        logToConsole(`Failed to connect to backend: ${error.message}`, 'event-error');
    }
}

// Setup log streaming from backend
function setupLogStreaming() {
    if (state.eventSource) {
        state.eventSource.close();
    }

    state.eventSource = new EventSource(`${API_BASE_URL}/logs/stream`);

    state.eventSource.onmessage = (event) => {
        try {
            const logEntry = JSON.parse(event.data);
            if (logEntry.type !== 'heartbeat') {
                const className = getLogClassName(logEntry);
                logToConsole(logEntry.message, className);
            }
        } catch (e) {
            // Ignore parse errors
        }
    };

    state.eventSource.onerror = () => {
        // Silently reconnect
    };
}

// Map log levels to CSS classes
function getLogClassName(logEntry) {
    const message = logEntry.message.toLowerCase();
    if (message.includes('winner') || message.includes('wins')) return 'event-winner';
    if (message.includes('flop') || message.includes('turn') ||
        message.includes('river') || message.includes('showdown') ||
        message.includes('===')) return 'event-phase';
    if (message.includes('dealt') || message.includes('deal')) return 'event-deal';
    if (message.includes('fold') || message.includes('call') ||
        message.includes('raise') || message.includes('check') ||
        message.includes('all-in')) return 'event-action';
    if (logEntry.level === 'ERROR' || message.includes('error')) return 'event-error';
    return '';
}

// Render bot list
function renderBotList() {
    const botList = document.getElementById('botList');
    botList.innerHTML = state.availableBots.map(bot => `
        <div class="bot-item" onclick="addBotToTable('${bot.id}')">
            <div class="bot-name">${bot.name}</div>
            <div class="bot-type">${bot.creator ? 'by ' + bot.creator : bot.type}</div>
        </div>
    `).join('');
}

// Add bot to table
function addBotToTable(botId) {
    if (state.mode !== 'custom') return;
    if (state.isPlaying) {
        showToast('Cannot add bots while tournament is running', 'error');
        return;
    }
    if (state.tablePlayers.length >= MAX_PLAYERS) {
        showToast(`Maximum ${MAX_PLAYERS} players allowed`, 'error');
        return;
    }

    const bot = state.availableBots.find(b => b.id === botId);
    const existingCount = state.tablePlayers.filter(p => p.botId === botId).length;
    const playerId = existingCount > 0 ? `${botId}_${existingCount + 1}` : botId;
    const displayName = existingCount > 0 ? `${bot.name} #${existingCount + 1}` : bot.name;
    const chips = getCustomSettings().startingChips;

    const player = {
        id: playerId,
        botId: botId,
        name: displayName,
        chips: chips,
        bet: 0,
        cards: [],
        folded: false,
        allIn: false
    };

    state.tablePlayers.push(player);

    if (!state.statistics[playerId]) {
        state.statistics[playerId] = {
            gamesPlayed: 0,
            wins: 0,
            winRate: 0,
            totalChipsWon: 0,
            totalChipsLost: 0
        };
        state.chipHistory[playerId] = [{ game: 0, chips: chips }];
    }

    logToConsole(`${displayName} joined the table`, 'event-action');
    renderTable();
    updateStatus();
}

// Remove bot from table
function removeBotFromTable(botId) {
    if (state.isPlaying) {
        showToast('Cannot remove bots while tournament is running', 'error');
        return;
    }
    state.tablePlayers = state.tablePlayers.filter(p => p.botId !== botId);
    logToConsole('Bot removed from table', 'event-action');
    renderTable();
    updateStatus();
}

// Clear table
function clearTable() {
    if (state.isPlaying && !confirm('Game in progress. Are you sure?')) return;
    state.tablePlayers = [];
    state.isPlaying = false;
    state.tournamentInitialized = false;
    state.handInProgress = false;
    stopGameLoop();
    logToConsole('Table cleared', 'event-action');
    renderTable();
    updateStatus();
}

// ============================================================================
// SHARED UI FUNCTIONS
// ============================================================================

// Console functions
function logToConsole(message, className = '') {
    const consoleContent = document.getElementById('consoleContent');
    const line = document.createElement('div');
    line.className = `console-line ${className}`;
    const timestamp = new Date().toLocaleTimeString();
    line.textContent = `[${timestamp}] ${message}`;
    consoleContent.appendChild(line);
    // Keep console from growing unbounded
    while (consoleContent.children.length > 500) {
        consoleContent.removeChild(consoleContent.firstChild);
    }
    consoleContent.scrollTop = consoleContent.scrollHeight;
}

function clearConsole() {
    document.getElementById('consoleContent').innerHTML = '';
}

// Render table
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
                <div class="player-cards pos-${i}">
                    ${renderPlayerCards(player)}
                </div>
            `;
        } else {
            seat.classList.add('empty');
            seat.innerHTML = '';
        }
    }

    // Render community cards
    const communityCardsEl = document.getElementById('communityCards');
    if (state.communityCards && state.communityCards.length > 0) {
        communityCardsEl.innerHTML = state.communityCards.map(card => renderCard(card)).join('');
    } else {
        communityCardsEl.innerHTML = '';
    }

    document.getElementById('potAmount').textContent = `${state.pot || 0}`;
}

// Render player cards
function renderPlayerCards(player) {
    if (!player.cards || player.cards.length === 0) return '';
    return player.cards.map(card => renderCard(card)).join('');
}

// Render a card
function renderCard(card) {
    if (!card) return '';
    const suit = card.suit || card.s;
    const value = card.value || card.v || card.rank;
    const color = (suit === '\u2665' || suit === '\u2666') ? 'red' : 'black';
    return `<div class="card ${color}">${value}${suit}</div>`;
}

// Format action name for console
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

// Find player by ID
function findPlayer(pid) {
    const exact = state.tablePlayers.find(p => p.id === pid);
    if (exact) return exact;
    const fallback = pid.replace(/_(\d+)$/, '');
    return state.tablePlayers.find(p => p.id === fallback);
}

// ============================================================================
// CUSTOM TABLE: TOURNAMENT LOGIC
// ============================================================================

// Initialize tournament on backend
async function initializeTournament() {
    if (state.tablePlayers.length < MIN_PLAYERS) {
        showToast(`Need at least ${MIN_PLAYERS} players to start`, 'error');
        return false;
    }

    try {
        const settings = getCustomSettings();
        const response = await fetch(`${API_BASE_URL}/tournament/init`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                bots: state.tablePlayers.map(p => ({
                    id: p.botId,
                    name: p.name,
                    frontendId: p.id
                })),
                starting_chips: settings.startingChips,
                small_blind: settings.smallBlind,
                big_blind: settings.bigBlind,
                blind_increase_interval: settings.blindInterval
            })
        });

        const data = await response.json();

        if (data.success) {
            state.tournamentInitialized = true;
            logToConsole('Tournament initialized', 'event-phase');
            return true;
        } else {
            logToConsole(`Failed to initialize: ${data.error}`, 'event-error');
            return false;
        }
    } catch (error) {
        logToConsole(`Error initializing: ${error.message}`, 'event-error');
        return false;
    }
}

// Step through one action of the tournament
async function stepGame() {
    if (state.mode !== 'custom') return;
    if (state.stepping) return;
    state.stepping = true;

    try {
        if (!state.tournamentInitialized) {
            const initialized = await initializeTournament();
            if (!initialized) { state.stepping = false; return; }
        }

        const response = await fetch(`${API_BASE_URL}/tournament/step`, {
            method: 'POST'
        });

        const data = await response.json();

        if (data.success) {
            handleStepEvent(data);

            if (data.complete) {
                stopGameLoop();
                state.isPlaying = false;
                state.handInProgress = false;
                document.getElementById('playBtn').textContent = 'Play';
                logToConsole('=== TOURNAMENT COMPLETE ===', 'event-winner');
            }
        } else {
            logToConsole(`Error: ${data.error}`, 'event-error');
            if (data.error && data.error.includes('not initialized')) {
                logToConsole('Server lost tournament state. Click Reset then Play to restart.', 'event-error');
                stopGameLoop();
                state.isPlaying = false;
                state.tournamentInitialized = false;
                document.getElementById('playBtn').textContent = 'Play';
            }
        }
    } catch (error) {
        logToConsole(`Backend error: ${error.message}`, 'event-error');
        stopGameLoop();
        state.isPlaying = false;
        document.getElementById('playBtn').textContent = 'Play';
    } finally {
        state.stepping = false;
    }
}

// Handle a step event from the backend (custom mode)
function handleStepEvent(data) {
    const event = data.event;

    if (event === 'deal') {
        state.handInProgress = true;
        state.communityCards = [];
        state.tablePlayers.forEach(p => { p.folded = false; p.allIn = false; p.cards = []; p.bet = 0; });

        if (data.playerCards) {
            for (const [pid, cards] of Object.entries(data.playerCards)) {
                const player = findPlayer(pid);
                if (player) player.cards = cards;
            }
        }

        syncChipsAndBets(data);
        state.pot = data.pot || 0;
        logToConsole(`--- NEW HAND (${data.phase}) ---`, 'event-phase');

        if (data.playerBets) {
            const blinds = Object.entries(data.playerBets)
                .filter(([, bet]) => bet > 0)
                .sort((a, b) => a[1] - b[1]);
            for (const [pid, bet] of blinds) {
                const p = findPlayer(pid);
                const name = p ? p.name : pid.replace(/_(\d+)$/, ' #$1');
                logToConsole(`${name} posts blind: ${bet}`, 'event-action');
            }
        }

    } else if (event === 'action') {
        const actionStr = formatAction(data.player, data.action, data.amount);
        logToConsole(actionStr, 'event-action');

        if (data.action === 'fold') {
            const player = findPlayer(data.player);
            if (player) player.folded = true;
        }

        syncChipsAndBets(data);
        state.pot = data.pot || 0;

    } else if (event === 'community') {
        state.communityCards = data.communityCards || [];
        state.pot = data.pot || 0;
        syncChipsAndBets(data);
        logToConsole(`--- ${data.phase.toUpperCase()}: ${formatCommunityCards(state.communityCards)} ---`, 'event-phase');

    } else if (event === 'showdown') {
        state.communityCards = data.communityCards || [];
        state.pot = 0;

        if (data.playerHands) {
            for (const [pid, cards] of Object.entries(data.playerHands)) {
                const player = findPlayer(pid);
                if (player) player.cards = cards;
            }
        }

        const winners = (data.winners || []).map(w => {
            const p = findPlayer(w);
            return p ? p.name : w;
        });
        logToConsole(`WINNERS: ${winners.join(', ')}`, 'event-winner');

        if (data.playerChips) {
            for (const [pid, chips] of Object.entries(data.playerChips)) {
                const player = findPlayer(pid);
                if (player) {
                    const prevChips = player.chips;
                    player.chips = chips;

                    const stats = state.statistics[player.id];
                    if (stats) {
                        const delta = chips - prevChips;
                        if (delta > 0) {
                            stats.wins++;
                            stats.totalChipsWon += delta;
                        } else if (delta < 0) {
                            stats.totalChipsLost += Math.abs(delta);
                        }
                        stats.gamesPlayed++;
                        stats.winRate = stats.gamesPlayed > 0
                            ? (stats.wins / stats.gamesPlayed * 100).toFixed(1) : 0;

                        state.chipHistory[player.id].push({
                            game: state.gamesPlayed + 1,
                            chips: chips
                        });
                    }
                }
            }
        }

        state.gamesPlayed++;
        state.handInProgress = false;
        state.tablePlayers.forEach(p => { p.bet = 0; });
        renderStatistics();
    }

    if (data.state) {
        syncTournamentState(data.state);
    }

    renderTable();
    updateStatus();
}

// Custom mode helpers
function syncChipsAndBets(data) {
    if (data.playerChips) {
        for (const [pid, chips] of Object.entries(data.playerChips)) {
            const player = findPlayer(pid);
            if (player) player.chips = chips;
        }
    }
    if (data.playerBets) {
        for (const [pid, bet] of Object.entries(data.playerBets)) {
            const player = findPlayer(pid);
            if (player) player.bet = bet;
        }
    }
}

function syncTournamentState(ts) {
    if (ts.players) {
        for (const bp of ts.players) {
            const player = findPlayer(bp.id);
            if (player && bp.isEliminated) {
                player.chips = 0;
            }
        }
    }
}

function formatAction(player, action, amount) {
    const p = findPlayer(player);
    const name = p ? p.name : player.replace(/_(\d+)$/, ' $1');
    switch (action) {
        case 'fold': return `${name} folds`;
        case 'check': return `${name} checks`;
        case 'call': return `${name} calls`;
        case 'raise': return `${name} raises to ${amount}`;
        case 'all_in': return `${name} goes ALL-IN`;
        default: return `${name}: ${action}`;
    }
}

// Toggle play/pause (custom mode)
async function togglePlay() {
    if (state.mode !== 'custom') return;

    if (!state.tournamentInitialized && !state.isPlaying) {
        const initialized = await initializeTournament();
        if (!initialized) return;
    }

    state.isPlaying = !state.isPlaying;

    const playBtn = document.getElementById('playBtn');
    if (state.isPlaying) {
        playBtn.textContent = 'Pause';
        startGameLoop();
    } else {
        playBtn.textContent = 'Play';
        stopGameLoop();
    }
}

// Start game loop - fast stepping
function startGameLoop() {
    stopGameLoop();
    const interval = Math.max(150, 2000 / state.speed);
    state.gameInterval = setInterval(stepGame, interval);
}

// Stop game loop
function stopGameLoop() {
    if (state.gameInterval) {
        clearInterval(state.gameInterval);
        state.gameInterval = null;
    }
}

// Change speed (custom mode)
function changeSpeed(delta) {
    const speeds = [0.25, 1, 4, 16, 64, 256];
    let currentIndex = speeds.indexOf(state.speed);
    currentIndex = Math.max(0, Math.min(speeds.length - 1, currentIndex + delta));
    state.speed = speeds[currentIndex];

    document.getElementById('speedValue').textContent = state.speed + 'x';

    if (state.isPlaying) {
        stopGameLoop();
        startGameLoop();
    }
}

// Reset game (custom mode)
async function resetGame() {
    if (state.mode !== 'custom') return;
    if (state.isPlaying && !confirm('Game in progress. Are you sure?')) return;

    state.isPlaying = false;
    state.handInProgress = false;
    stopGameLoop();

    try {
        await fetch(`${API_BASE_URL}/tournament/reset`, { method: 'POST' });
    } catch (error) {
        // Ignore
    }

    state.tournamentInitialized = false;
    state.gamesPlayed = 0;
    state.communityCards = [];
    state.pot = 0;

    const resetChips = getCustomSettings().startingChips;
    state.tablePlayers.forEach(player => {
        player.chips = resetChips;
        player.bet = 0;
        player.folded = false;
        player.cards = [];
    });

    for (const playerId in state.statistics) {
        state.statistics[playerId] = {
            gamesPlayed: 0, wins: 0, winRate: 0,
            totalChipsWon: 0, totalChipsLost: 0
        };
        state.chipHistory[playerId] = [{ game: 0, chips: resetChips }];
    }

    logToConsole('Game reset', 'event-action');
    document.getElementById('playBtn').textContent = 'Play';
    renderTable();
    updateStatus();
    renderStatistics();
}

// ============================================================================
// STATUS & STATISTICS
// ============================================================================

function updateStatus() {
    document.getElementById('playerCount').textContent = state.tablePlayers.length;
    document.getElementById('gamesPlayed').textContent = state.gamesPlayed;

    if (state.mode === 'spectator') {
        const match = state.spectatorMatch;
        if (match && match.status === 'playing') {
            document.getElementById('gameStatus').textContent = 'Live Match';
        } else {
            document.getElementById('gameStatus').textContent = 'Spectator Mode';
        }
    } else {
        let status = 'Ready';
        if (state.isPlaying) status = 'Playing';
        else if (state.tablePlayers.length < MIN_PLAYERS) status = `Need ${MIN_PLAYERS - state.tablePlayers.length} more player(s)`;
        document.getElementById('gameStatus').textContent = status;
    }
}

function renderStatistics() {
    const statsGrid = document.getElementById('statsGrid');

    // In spectator mode, show stats for all tracked bots; in custom mode, show table players
    const statNames = Object.keys(state.statistics);
    if (statNames.length === 0 && state.tablePlayers.length === 0) {
        statsGrid.innerHTML = '<p class="stats-empty">Statistics will appear after hands are played</p>';
        drawChipsChart();
        return;
    }

    const players = statNames.length > 0 ? statNames : state.tablePlayers.map(p => p.id);
    const rows = players.map(name => {
        const stats = state.statistics[name];
        if (!stats) return '';
        const tablePlayer = state.tablePlayers.find(p => p.id === name);
        const chips = tablePlayer ? tablePlayer.chips : '--';
        const isEliminated = tablePlayer && tablePlayer.chips <= 0 && !tablePlayer.allIn;
        return `
            <tr class="${isEliminated ? 'stats-row-eliminated' : ''}">
                <td class="stats-cell-name">${name}</td>
                <td>${stats.gamesPlayed}</td>
                <td>${stats.wins}</td>
                <td>${stats.winRate}%</td>
                <td class="stats-cell-chips">${chips}</td>
                <td class="stats-cell-won">+${stats.totalChipsWon}</td>
                <td class="stats-cell-lost">-${stats.totalChipsLost}</td>
            </tr>
        `;
    }).join('');

    statsGrid.innerHTML = `
        <table class="stats-table">
            <thead>
                <tr>
                    <th>Player</th>
                    <th>Hands</th>
                    <th>Wins</th>
                    <th>Win Rate</th>
                    <th>Chips</th>
                    <th>Won</th>
                    <th>Lost</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>
    `;

    drawChipsChart();
}

// Draw chips over time chart
function drawChipsChart() {
    const canvas = document.getElementById('chipsChart');
    const ctx = canvas.getContext('2d');

    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const displayWidth = rect.width;
    const displayHeight = rect.height;

    ctx.clearRect(0, 0, displayWidth, displayHeight);

    if (state.gamesPlayed === 0 || Object.keys(state.chipHistory).length === 0) {
        ctx.fillStyle = '#666';
        ctx.font = '14px Arial';
        ctx.textAlign = 'center';
        ctx.fillText('Play games to see chip progression', displayWidth / 2, displayHeight / 2);
        return;
    }

    const padding = 40;
    const chartWidth = displayWidth - padding * 2;
    const chartHeight = displayHeight - padding * 2;

    let maxChips = DEFAULT_STARTING_CHIPS;
    let maxGames = state.gamesPlayed;

    const chartPlayers = Object.keys(state.chipHistory);
    chartPlayers.forEach(name => {
        const history = state.chipHistory[name];
        if (history) history.forEach(point => { maxChips = Math.max(maxChips, point.chips); });
    });

    // Draw axes
    ctx.strokeStyle = '#444';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padding, padding);
    ctx.lineTo(padding, displayHeight - padding);
    ctx.lineTo(displayWidth - padding, displayHeight - padding);
    ctx.stroke();

    // Draw grid lines
    ctx.strokeStyle = '#333';
    ctx.lineWidth = 0.5;
    for (let i = 0; i <= 5; i++) {
        const y = padding + (chartHeight / 5) * i;
        ctx.beginPath();
        ctx.moveTo(padding, y);
        ctx.lineTo(displayWidth - padding, y);
        ctx.stroke();

        const chipValue = Math.round(maxChips - (maxChips / 5) * i);
        ctx.fillStyle = '#888';
        ctx.font = '10px Arial';
        ctx.textAlign = 'right';
        ctx.fillText(`${chipValue}`, padding - 5, y + 4);
    }

    // X-axis labels
    ctx.fillStyle = '#888';
    ctx.font = '10px Arial';
    ctx.textAlign = 'center';
    for (let i = 0; i <= Math.min(maxGames, 10); i++) {
        const x = padding + (chartWidth / Math.min(maxGames, 10)) * i;
        const gameNum = Math.round((maxGames / Math.min(maxGames, 10)) * i);
        ctx.fillText(gameNum, x, displayHeight - padding + 15);
    }

    // Draw lines for each player
    const colors = ['#4a90e2', '#e24a4a', '#5ac', '#f90', '#9c3', '#c6c', '#fc3', '#6cf', '#f6c'];

    chartPlayers.forEach((name, idx) => {
        const history = state.chipHistory[name];
        if (!history || history.length === 0) return;

        ctx.strokeStyle = colors[idx % colors.length];
        ctx.lineWidth = 2;
        ctx.beginPath();

        history.forEach((point, i) => {
            const x = padding + (chartWidth / maxGames) * point.game;
            const y = displayHeight - padding - (chartHeight * point.chips / maxChips);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });

        ctx.stroke();

        // Draw legend
        const legendX = displayWidth - padding - 150;
        const legendY = padding + 20 + (idx * 20);
        ctx.fillStyle = colors[idx % colors.length];
        ctx.fillRect(legendX, legendY - 6, 12, 12);
        ctx.fillStyle = '#e0e0e0';
        ctx.font = '11px Arial';
        ctx.textAlign = 'left';
        ctx.fillText(name, legendX + 18, legendY + 4);
    });

    ctx.fillStyle = '#4a90e2';
    ctx.font = 'bold 12px Arial';
    ctx.textAlign = 'left';
    ctx.fillText('Games', displayWidth / 2 - 20, displayHeight - 5);

    ctx.save();
    ctx.translate(15, displayHeight / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText('Chips', 0, 0);
    ctx.restore();
}

window.addEventListener('resize', drawChipsChart);

// Switch tabs
function switchTab(tab) {
    document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));

    if (tab === 'game') {
        document.querySelectorAll('.nav-tab')[0].classList.add('active');
        document.getElementById('gameTab').classList.add('active');
    } else {
        document.querySelectorAll('.nav-tab')[1].classList.add('active');
        document.getElementById('statsTab').classList.add('active');
        renderStatistics();
    }
}

// ============================================================================
// RESIZE HANDLES
// ============================================================================

const sidebar = document.getElementById('sidebar');
const sidebarResize = document.getElementById('sidebarResize');

sidebarResize.addEventListener('mousedown', initSidebarResize);

function initSidebarResize(e) {
    e.preventDefault();
    window.addEventListener('mousemove', resizeSidebar);
    window.addEventListener('mouseup', stopSidebarResize);
}

function resizeSidebar(e) {
    const newWidth = e.clientX;
    if (newWidth > 10 && newWidth < 500) {
        sidebar.style.width = newWidth + 'px';
    }
}

function stopSidebarResize() {
    window.removeEventListener('mousemove', resizeSidebar);
    window.removeEventListener('mouseup', stopSidebarResize);
}

// Console resize
const consoleEl = document.getElementById('console');
const consoleResize = document.getElementById('consoleResize');

consoleResize.addEventListener('mousedown', initConsoleResize);

function initConsoleResize(e) {
    e.preventDefault();
    window.addEventListener('mousemove', resizeConsole);
    window.addEventListener('mouseup', stopConsoleResize);
}

function resizeConsole(e) {
    const containerHeight = document.querySelector('.main-content').clientHeight;
    const newHeight = containerHeight - e.clientY;
    if (newHeight > 20 && newHeight < 500) {
        consoleEl.style.height = newHeight + 'px';
    }
}

function stopConsoleResize() {
    window.removeEventListener('mousemove', resizeConsole);
    window.removeEventListener('mouseup', stopConsoleResize);
}

// Initialize on load
init();
