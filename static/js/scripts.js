// API Configuration
const API_BASE_URL = window.location.hostname === 'localhost'
    ? 'http://localhost:5000/api'
    : '/api';

// Game State
const MIN_PLAYERS = 2;
const MAX_PLAYERS = 10;
const STARTING_CHIPS = 1000;

const state = {
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
    // Guards against overlapping step calls
    stepping: false,
    handInProgress: false,
};

// Initialize
async function init() {
    await loadAvailableBots();
    setupLogStreaming();
    updateStatus();
    logToConsole('Poker tournament system initialized', 'event-phase');
}

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
        logToConsole('Log stream disconnected', 'event-error');
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
            <div class="bot-type">${bot.type}</div>
        </div>
    `).join('');
}

// Add bot to table
function addBotToTable(botId) {
    if (state.isPlaying) {
        alert('Cannot add bots while tournament is running');
        return;
    }
    if (state.tablePlayers.length >= MAX_PLAYERS) {
        alert(`Maximum ${MAX_PLAYERS} players allowed`);
        return;
    }

    const bot = state.availableBots.find(b => b.id === botId);
    const existingCount = state.tablePlayers.filter(p => p.botId === botId).length;
    const playerId = existingCount > 0 ? `${botId}_${existingCount + 1}` : botId;
    const displayName = existingCount > 0 ? `${bot.name} #${existingCount + 1}` : bot.name;

    const player = {
        id: playerId,
        botId: botId,
        name: displayName,
        chips: STARTING_CHIPS,
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
        state.chipHistory[playerId] = [{ game: 0, chips: STARTING_CHIPS }];
    }

    logToConsole(`${bot.name} joined the table`, 'event-action');
    renderTable();
    updateStatus();
}

// Remove bot from table
function removeBotFromTable(botId) {
    if (state.isPlaying) {
        alert('Cannot remove bots while tournament is running');
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

// Console functions
function logToConsole(message, className = '') {
    const consoleContent = document.getElementById('consoleContent');
    const line = document.createElement('div');
    line.className = `console-line ${className}`;
    const timestamp = new Date().toLocaleTimeString();
    line.textContent = `[${timestamp}] ${message}`;
    consoleContent.appendChild(line);
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
            const isEliminated = player.chips <= 0;
            const isFolded = player.folded;
            seat.classList.remove('empty');
            seat.innerHTML = `
                <div class="player-info ${isEliminated ? 'eliminated' : ''} ${isFolded ? 'folded' : ''}">
                    <div class="player-name">${player.name}</div>
                    <div class="player-chips">${player.chips}</div>
                    ${player.bet > 0 ? `<div class="player-bet">Bet: ${player.bet}</div>` : ''}
                    ${isEliminated ? '<div class="player-status eliminated-tag">ELIMINATED</div>' : ''}
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

// Initialize tournament on backend
async function initializeTournament() {
    if (state.tablePlayers.length < MIN_PLAYERS) {
        alert(`Need at least ${MIN_PLAYERS} players to start`);
        return false;
    }

    try {
        const response = await fetch(`${API_BASE_URL}/tournament/init`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                bots: state.tablePlayers.map(p => ({
                    id: p.botId,
                    name: p.name,
                    frontendId: p.id
                })),
                starting_chips: STARTING_CHIPS,
                small_blind: 10,
                big_blind: 20,
                blind_increase_interval: 10
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
    // Prevent overlapping calls
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

// Handle a step event from the backend
function handleStepEvent(data) {
    const event = data.event;

    if (event === 'deal') {
        state.handInProgress = true;
        // Clear previous hand state
        state.communityCards = [];
        state.tablePlayers.forEach(p => { p.folded = false; p.cards = []; p.bet = 0; });

        // Set hole cards
        if (data.playerCards) {
            for (const [pid, cards] of Object.entries(data.playerCards)) {
                const player = findPlayer(pid);
                if (player) player.cards = cards;
            }
        }

        // Update chips and bets
        syncChipsAndBets(data);
        state.pot = data.pot || 0;
        logToConsole(`--- NEW HAND (${data.phase}) ---`, 'event-phase');

    } else if (event === 'action') {
        const actionStr = formatAction(data.player, data.action, data.amount);
        logToConsole(actionStr, 'event-action');

        // Mark folded players
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

        // Show all hands at showdown
        if (data.playerHands) {
            for (const [pid, cards] of Object.entries(data.playerHands)) {
                const player = findPlayer(pid);
                if (player) player.cards = cards;
            }
        }

        const winners = data.winners || [];
        logToConsole(`WINNERS: ${winners.join(', ')}`, 'event-winner');

        // Update chips from showdown
        if (data.playerChips) {
            for (const [pid, chips] of Object.entries(data.playerChips)) {
                const player = findPlayer(pid);
                if (player) {
                    const prevChips = player.chips;
                    player.chips = chips;

                    // Update statistics
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

        // Clear bets
        state.tablePlayers.forEach(p => { p.bet = 0; });

        renderStatistics();
    }

    // Always sync tournament-level state
    if (data.state) {
        syncTournamentState(data.state);
    }

    renderTable();
    updateStatus();
}

// Helpers
function findPlayer(pid) {
    const coreId = pid.includes('_') ? pid.split('_')[0] : pid;
    return state.tablePlayers.find(p => p.id === pid || p.id === coreId);
}

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
    // Sync eliminations from tournament state
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
    const name = player.replace(/_/g, ' ');
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

// Toggle play/pause
async function togglePlay() {
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

// Change speed
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

// Reset game
async function resetGame() {
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

    state.tablePlayers.forEach(player => {
        player.chips = STARTING_CHIPS;
        player.bet = 0;
        player.folded = false;
        player.cards = [];
    });

    for (const playerId in state.statistics) {
        state.statistics[playerId] = {
            gamesPlayed: 0, wins: 0, winRate: 0,
            totalChipsWon: 0, totalChipsLost: 0
        };
        state.chipHistory[playerId] = [{ game: 0, chips: STARTING_CHIPS }];
    }

    logToConsole('Game reset', 'event-action');
    document.getElementById('playBtn').textContent = 'Play';
    renderTable();
    updateStatus();
    renderStatistics();
}

// Update status
function updateStatus() {
    document.getElementById('playerCount').textContent = state.tablePlayers.length;
    document.getElementById('gamesPlayed').textContent = state.gamesPlayed;

    let status = 'Ready';
    if (state.isPlaying) status = 'Playing';
    else if (state.tablePlayers.length < MIN_PLAYERS) status = `Need ${MIN_PLAYERS - state.tablePlayers.length} more player(s)`;
    document.getElementById('gameStatus').textContent = status;
}

// Render statistics
function renderStatistics() {
    const statsGrid = document.getElementById('statsGrid');

    if (state.tablePlayers.length === 0) {
        statsGrid.innerHTML = '<p class="stats-empty">Add players to see statistics</p>';
        drawChipsChart();
        return;
    }

    // Build a table for all players
    const rows = state.tablePlayers.map(player => {
        const stats = state.statistics[player.id];
        const isEliminated = player.chips <= 0;
        return `
            <tr class="${isEliminated ? 'stats-row-eliminated' : ''}">
                <td class="stats-cell-name">${player.name}</td>
                <td>${stats.gamesPlayed}</td>
                <td>${stats.wins}</td>
                <td>${stats.winRate}%</td>
                <td class="stats-cell-chips">${player.chips}</td>
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

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (state.gamesPlayed === 0 || state.tablePlayers.length === 0) {
        ctx.fillStyle = '#666';
        ctx.font = '14px Arial';
        ctx.textAlign = 'center';
        ctx.fillText('Play games to see chip progression', canvas.width / 2, canvas.height / 2);
        return;
    }

    const padding = 40;
    const chartWidth = canvas.width - padding * 2;
    const chartHeight = canvas.height - padding * 2;

    let maxChips = STARTING_CHIPS;
    let maxGames = state.gamesPlayed;

    state.tablePlayers.forEach(player => {
        const history = state.chipHistory[player.id];
        if (history) history.forEach(point => { maxChips = Math.max(maxChips, point.chips); });
    });

    // Draw axes
    ctx.strokeStyle = '#444';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padding, padding);
    ctx.lineTo(padding, canvas.height - padding);
    ctx.lineTo(canvas.width - padding, canvas.height - padding);
    ctx.stroke();

    // Draw grid lines
    ctx.strokeStyle = '#333';
    ctx.lineWidth = 0.5;
    for (let i = 0; i <= 5; i++) {
        const y = padding + (chartHeight / 5) * i;
        ctx.beginPath();
        ctx.moveTo(padding, y);
        ctx.lineTo(canvas.width - padding, y);
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
        ctx.fillText(gameNum, x, canvas.height - padding + 15);
    }

    // Draw lines for each player
    const colors = ['#4a90e2', '#e24a4a', '#5ac', '#f90', '#9c3', '#c6c', '#fc3', '#6cf', '#f6c'];

    state.tablePlayers.forEach((player, idx) => {
        const history = state.chipHistory[player.id];
        if (!history || history.length === 0) return;

        ctx.strokeStyle = colors[idx % colors.length];
        ctx.lineWidth = 2;
        ctx.beginPath();

        history.forEach((point, i) => {
            const x = padding + (chartWidth / maxGames) * point.game;
            const y = canvas.height - padding - (chartHeight * point.chips / maxChips);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });

        ctx.stroke();

        // Draw legend
        const legendX = canvas.width - padding - 150;
        const legendY = padding + 20 + (idx * 20);
        ctx.fillStyle = colors[idx % colors.length];
        ctx.fillRect(legendX, legendY - 6, 12, 12);
        ctx.fillStyle = '#e0e0e0';
        ctx.font = '11px Arial';
        ctx.textAlign = 'left';
        ctx.fillText(player.name, legendX + 18, legendY + 4);
    });

    ctx.fillStyle = '#4a90e2';
    ctx.font = 'bold 12px Arial';
    ctx.textAlign = 'left';
    ctx.fillText('Games', canvas.width / 2 - 20, canvas.height - 5);

    ctx.save();
    ctx.translate(15, canvas.height / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText('Chips', 0, 0);
    ctx.restore();
}

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

// Sidebar resize
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
