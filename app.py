"""
Flask API Server for Poker Tournament - PRODUCTION READY
Complete workflow: Submit → Review → Approve → Test
All data persists across server restarts
"""
from flask import Flask, jsonify, request, Response, render_template, session, redirect, url_for
from flask_cors import CORS
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
import json
import logging
import sys
import os
from queue import Queue
from threading import Lock
import time
from datetime import timedelta
import secrets
from dotenv import load_dotenv

# Load .env from project directory (works on both Windows and Linux)
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from backend.tournament import TournamentSettings, TournamentType
from backend.bot_manager import BotManager
from backend.engine.poker_game import PokerGame, PlayerAction
from backend.tournament import PokerTournament
from backend.engine.cards import Card

# Import security systems
from secure_admin_auth import AdminAuthSystem, User
from bot_approval_system import BotReviewSystem
from secure_bot_storage import SecureBotStorage

# ============================================================================
# APP CONFIGURATION
# ============================================================================

app = Flask(__name__)
CORS(app)

# Security configuration - PRODUCTION READY
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
# Only set Secure cookie flag if HTTPS is actually configured
# (setting this over plain HTTP causes the browser to silently discard the session cookie)
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('USE_HTTPS', '').lower() == 'true'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=2)
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1MB max file size

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login_page' # type: ignore

@login_manager.unauthorized_handler
def unauthorized():
    """Return JSON for API requests instead of redirecting to login page"""
    if request.path.startswith('/api/'):
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    return redirect(url_for('login_page'))

# Initialize systems - ALL DATA PERSISTS
auth_system = AdminAuthSystem()
review_system = BotReviewSystem()
bot_storage = SecureBotStorage()

# Master password from environment (REQUIRED)
MASTER_PASSWORD = os.environ.get('MASTER_PASSWORD')
if not MASTER_PASSWORD:
    print("=" * 80)
    print("ERROR: MASTER_PASSWORD environment variable not set!")
    print("=" * 80)
    print("Please set it before starting the server:")
    print("  Windows (PowerShell): $env:MASTER_PASSWORD = 'your-secure-password'")
    print("  Windows (CMD):        set MASTER_PASSWORD=your-secure-password")
    print("  Linux/Mac:            export MASTER_PASSWORD='your-secure-password'")
    print("=" * 80)
    sys.exit(1)

# Tournament state (temporary, cleared on restart - this is OK)
tournament_state = {
    'tournament': None,
    'bot_manager': None,
    'log_queue': Queue(),
    'settings': None,
    # Step-by-step hand state
    'hand_phase': None,       # None, 'preflop', 'flop', 'turn', 'river', 'showdown'
    'active_game': None,      # Current PokerGame instance (persists across steps)
    'stats_recorded': False,  # Prevents duplicate stats recording at tournament end
}

state_lock = Lock()

# ============================================================================
# LOGGING SETUP
# ============================================================================

class QueueHandler(logging.Handler):
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        log_entry = {
            'timestamp': time.time(),
            'level': record.levelname,
            'message': self.format(record),
            'name': record.name
        }
        self.log_queue.put(log_entry)

class GameEngineLogFilter(logging.Filter):
    """Filter out game-related messages from the SSE log stream.
    The step-based frontend generates its own log messages from step event
    data, so engine/tournament messages would appear as duplicates."""
    def filter(self, record):
        return not (record.name.startswith('backend.engine') or
                    record.name.startswith('bot_manager') or
                    record.name.startswith('tournament'))

# Setup logging
queue_handler = QueueHandler(tournament_state['log_queue'])
queue_handler.setFormatter(logging.Formatter('%(message)s'))
queue_handler.addFilter(GameEngineLogFilter())
logging.getLogger().addHandler(queue_handler)
logging.getLogger().setLevel(logging.INFO)

# File logging for persistence
os.makedirs('logs', exist_ok=True)
file_handler = logging.FileHandler('logs/server.log')
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logging.getLogger().addHandler(file_handler)


# ============================================================================
# FLASK-LOGIN
# ============================================================================

@login_manager.user_loader
def load_user(user_id):
    """Flask-Login user loader"""
    data = auth_system._load_auth_data()
    if user_id in data["admins"]:
        return User(user_id, user_id, is_admin=True)
    return None


# ============================================================================
# PUBLIC ROUTES - User bot submission and status
# ============================================================================

@app.route('/')
def index():
    """Main landing page - bot submission portal"""
    return render_template('submit.html')


@app.route('/tournament')
def tournament_page():
    """Tournament visualization page"""
    return render_template('tournament.html')


@app.route('/api/bots', methods=['GET'])
def get_available_bots():
    """Get list of APPROVED bots available for tournaments"""
    try:
        # Only return approved bots from secure storage
        approved_bots = bot_storage.list_bots()
        
        bots_info = []
        for bot in approved_bots:
            bots_info.append({
                'id': bot['name'],
                'name': bot['name'],
                'type': 'Approved Bot',
                'wins': bot.get('wins', 0),
                'total_games': bot.get('total_games', 0),
                'win_rate': round(bot.get('win_rate', 0), 1)
            })
        
        return jsonify({
            'success': True,
            'bots': bots_info
        })
    except Exception as e:
        logging.error(f"Error getting bots: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Failed to load bots'
        }), 500


@app.route('/api/bots/submit', methods=['POST'])
def submit_bot():
    """PUBLIC - Submit a bot for review"""
    try:
        data = request.json
        
        # Validate required fields
        required_fields = ['bot_name', 'bot_code', 'email', 'password']
        for field in required_fields:
            if not data.get(field):
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400
        
        bot_name = data['bot_name'].strip()
        bot_code = data['bot_code']
        submitter_email = data['email'].strip()
        submitter_password = data['password']
        
        # Basic validation
        if len(bot_name) < 3 or len(bot_name) > 50:
            return jsonify({
                'success': False,
                'error': 'Bot name must be between 3 and 50 characters'
            }), 400
        
        if len(submitter_password) < 12:
            return jsonify({
                'success': False,
                'error': 'Password must be at least 12 characters'
            }), 400
        
        if len(bot_code) > 500 * 1024:  # 500KB limit
            return jsonify({
                'success': False,
                'error': 'Bot code too large (max 500KB)'
            }), 400
        
        # Submit to review system
        result = review_system.submit_bot(
            bot_name=bot_name,
            bot_code=bot_code,
            submitter_email=submitter_email,
            submitter_password=submitter_password
        )
        
        if result['success']:
            logging.info(f"New bot submission: {bot_name} from {submitter_email}")
        
        return jsonify(result)
        
    except Exception as e:
        logging.error(f"Error submitting bot: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Submission failed. Please try again.'
        }), 500


@app.route('/api/bots/my-submissions', methods=['GET'])
def get_my_submissions():
    """PUBLIC - Get user's bot submissions"""
    try:
        email = request.args.get('email', '').strip()
        if not email:
            return jsonify({
                'success': False,
                'error': 'Email required'
            }), 400
        
        submissions = review_system.get_user_submissions(email)
        return jsonify({
            'success': True,
            'submissions': submissions
        })
        
    except Exception as e:
        logging.error(f"Error getting submissions: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Failed to load submissions'
        }), 500


@app.route('/api/bots/resubmit/<submission_id>', methods=['POST'])
def resubmit_bot(submission_id):
    """PUBLIC - Resubmit a bot after revision request"""
    try:
        data = request.json
        new_code = data.get('bot_code')
        email = data.get('email', '').strip()
        
        if not all([new_code, email]):
            return jsonify({
                'success': False,
                'error': 'Missing required fields'
            }), 400
        
        result = review_system.resubmit_bot(submission_id, new_code, email)
        return jsonify(result)
        
    except Exception as e:
        logging.error(f"Error resubmitting bot: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Resubmission failed'
        }), 500


# ============================================================================
# AUTHENTICATION ROUTES
# ============================================================================

@app.route('/admin/login')
def login_page():
    """Admin login page"""
    if current_user.is_authenticated:
        return redirect(url_for('admin_review_page'))
    return render_template('admin_login.html')


@app.route('/api/auth/login', methods=['POST'])
def login():
    """Admin login endpoint"""
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    ip = request.remote_addr
    
    if not username or not password:
        return jsonify({
            "success": False, 
            "error": "Username and password required"
        }), 400
    
    result = auth_system.authenticate(username, password, ip or "unknown")
    
    if result["success"]:
        login_user(result["user"], remember=True)
        session.permanent = True
        logging.info(f"Admin login: {username} from {ip or 'unknown'}")
        return jsonify({
            "success": True,
            "message": "Login successful",
            "username": username
        })
    else:
        return jsonify(result), 401


@app.route('/api/auth/logout', methods=['POST'])
@login_required
def logout():
    """Admin logout endpoint"""
    username = current_user.username
    ip = request.remote_addr
    auth_system._log_audit_event("LOGOUT", username, ip or "unknown", "User logged out")
    logout_user()
    logging.info(f"Admin logout: {username}")
    return jsonify({"success": True, "message": "Logged out successfully"})


@app.route('/api/auth/check', methods=['GET'])
def check_auth():
    """Check if user is authenticated"""
    if current_user.is_authenticated:
        return jsonify({
            "authenticated": True,
            "username": current_user.username,
            "is_admin": current_user.is_admin
        })
    return jsonify({"authenticated": False}), 401


# ============================================================================
# ADMIN ROUTES - Bot Review & Approval
# ============================================================================

@app.route('/admin/review')
@login_required
def admin_review_page():
    """Admin review page"""
    if not current_user.is_admin:
        return redirect(url_for('login_page'))
    return render_template('admin_review.html')


@app.route('/api/admin/submissions', methods=['GET'])
@login_required
def get_pending_submissions():
    """ADMIN - Get ALL bot submissions (not just pending)"""
    if not current_user.is_admin:
        return jsonify({"error": "Unauthorized"}), 403
    
    try:
        # Use the new method that returns ALL submissions
        all_submissions = review_system.get_all_submissions_admin()
        
        # Optionally filter by status if requested
        status_filter = request.args.get('status', None)
        if status_filter:
            all_submissions = [s for s in all_submissions if s['status'] == status_filter]
        
        return jsonify({
            "success": True,
            "submissions": all_submissions
        })
    except Exception as e:
        logging.error(f"Error getting submissions: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())
        return jsonify({
            "success": False,
            "error": "Failed to load submissions"
        }), 500


@app.route('/api/admin/approve/<submission_id>', methods=['POST'])
@login_required
def approve_submission(submission_id):
    """ADMIN - Approve a bot submission"""
    if not current_user.is_admin:
        return jsonify({"error": "Unauthorized"}), 403
    
    try:
        notes = request.json.get('notes', '')
        result = review_system.approve_bot(submission_id, notes)
        
        if result["success"]:
            auth_system._log_audit_event(
                "BOT_APPROVED",
                current_user.username,
                request.remote_addr or "unknown",
                f"Approved bot submission {submission_id}"
            )
            logging.info(f"Bot approved by {current_user.username}: {submission_id}")
        
        return jsonify(result)
        
    except Exception as e:
        logging.error(f"Error approving bot: {str(e)}")
        return jsonify({
            "success": False,
            "error": "Approval failed"
        }), 500


@app.route('/api/admin/reject/<submission_id>', methods=['POST'])
@login_required
def reject_submission(submission_id):
    """ADMIN - Reject a bot submission"""
    if not current_user.is_admin:
        return jsonify({"error": "Unauthorized"}), 403
    
    try:
        reason = request.json.get('reason', 'No reason provided')
        result = review_system.reject_bot(submission_id, reason)
        
        if result["success"]:
            auth_system._log_audit_event(
                "BOT_REJECTED",
                current_user.username,
                request.remote_addr or "unknown",
                f"Rejected bot submission {submission_id}"
            )
            logging.info(f"Bot rejected by {current_user.username}: {submission_id}")
        
        return jsonify(result)
        
    except Exception as e:
        logging.error(f"Error rejecting bot: {str(e)}")
        return jsonify({
            "success": False,
            "error": "Rejection failed"
        }), 500


@app.route('/api/admin/request-revision/<submission_id>', methods=['POST'])
@login_required
def request_revision(submission_id):
    """ADMIN - Request revisions to a bot submission"""
    if not current_user.is_admin:
        return jsonify({"error": "Unauthorized"}), 403
    
    try:
        feedback = request.json.get('feedback', '')
        result = review_system.request_revision(submission_id, feedback)
        
        if result["success"]:
            auth_system._log_audit_event(
                "REVISION_REQUESTED",
                current_user.username,
                request.remote_addr or "unknown",
                f"Requested revision for {submission_id}"
            )
            logging.info(f"Revision requested by {current_user.username}: {submission_id}")
        
        return jsonify(result)
        
    except Exception as e:
        logging.error(f"Error requesting revision: {str(e)}")
        return jsonify({
            "success": False,
            "error": "Request failed"
        }), 500


@app.route('/api/admin/audit-log', methods=['GET'])
@login_required
def get_audit_log():
    """ADMIN - Get security audit log"""
    if not current_user.is_admin:
        return jsonify({"error": "Unauthorized"}), 403
    
    try:
        limit = request.args.get('limit', 100, type=int)
        log = auth_system.get_audit_log(limit)
        return jsonify({"success": True, "audit_log": log})
    except Exception as e:
        logging.error(f"Error getting audit log: {str(e)}")
        return jsonify({"success": False, "error": "Failed to load log"}), 500


# ============================================================================
# TOURNAMENT ROUTES - Use approved bots from storage
# ============================================================================

def serialize_card(card):
    """Convert a Card object to a JSON-serializable dict"""
    rank_str = {2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8",
                9: "9", 10: "10", 11: "J", 12: "Q", 13: "K", 14: "A"}
    return {'value': rank_str[card.rank.value], 'suit': card.suit.value}


INIT_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'tournament_init.json')


def _clear_hand_state():
    """Reset the step-by-step hand state"""
    tournament_state['hand_phase'] = None
    tournament_state['active_game'] = None
    tournament_state['stats_recorded'] = False


def _build_tournament(init_config):
    """Build tournament + bot_manager from an init config dict.
    This is the single source of truth for creating tournament state
    so that both init and lazy-recovery use the same logic.
    """
    from backend.bot_manager import BotWrapper

    settings = TournamentSettings(
        tournament_type=TournamentType.FREEZE_OUT,
        starting_chips=init_config.get('starting_chips', 1000),
        small_blind=init_config.get('small_blind', 10),
        big_blind=init_config.get('big_blind', 20),
        time_limit_per_action=10.0,
        blind_increase_interval=init_config.get('blind_increase_interval', 10),
        blind_increase_factor=1.5
    )

    bot_manager = BotManager("players", 10.0)
    bot_manager.bots = {}

    player_names = []
    bot_count = {}

    for bot_data in init_config.get('bots', []):
        if isinstance(bot_data, dict):
            bot_name = bot_data.get('id') or bot_data.get('name')
        else:
            bot_name = bot_data

        if not bot_name:
            continue

        if MASTER_PASSWORD is None:
            continue
        bot_instance = bot_storage.load_bot(bot_name, MASTER_PASSWORD)
        if bot_instance is None:
            continue

        if bot_name not in bot_count:
            bot_count[bot_name] = 0
        bot_count[bot_name] += 1

        if bot_count[bot_name] > 1:
            player_name = f"{bot_name}_{bot_count[bot_name]}"
            unique_bot = bot_storage.load_bot(bot_name, MASTER_PASSWORD)
            if unique_bot is None:
                continue
            unique_bot.name = player_name
        else:
            player_name = bot_name
            unique_bot = bot_instance

        player_names.append(player_name)
        bot_wrapper = BotWrapper(player_name, unique_bot, 10.0)
        bot_manager.bots[player_name] = bot_wrapper

    if len(player_names) < 2:
        return None, None, None

    # Force all players onto a single table
    settings.max_players_per_table = len(player_names)

    tournament = PokerTournament(player_names, settings)
    return tournament, bot_manager, settings


def _ensure_tournament():
    """Ensure tournament_state has a live tournament.
    If it was lost (e.g. process respawn), rebuild from saved init config.
    Must be called while holding state_lock.
    Returns True if tournament is available, False otherwise.
    """
    if tournament_state['tournament'] is not None:
        return True

    # Try to restore from saved init config
    if not os.path.exists(INIT_CONFIG_FILE):
        return False

    try:
        with open(INIT_CONFIG_FILE, 'r') as f:
            init_config = json.load(f)

        tournament, bot_manager, settings = _build_tournament(init_config)
        if tournament is None:
            return False

        tournament_state['tournament'] = tournament
        tournament_state['bot_manager'] = bot_manager
        tournament_state['settings'] = settings
        _clear_hand_state()

        logging.info(f"Tournament restored from saved config with "
                    f"{len(tournament.players)} bots (pid={os.getpid()})")
        return True
    except Exception as e:
        logging.error(f"Failed to restore tournament: {e}")
        return False


@app.route('/api/tournament/init', methods=['POST'])
def initialize_tournament():
    """Initialize a new tournament with APPROVED bots only"""
    try:
        data = request.json
        selected_bot_names = data.get('bots', [])

        if len(selected_bot_names) < 2:
            return jsonify({
                'success': False,
                'error': 'Need at least 2 bots to start a tournament'
            }), 400

        # Save init config to disk so step endpoint can restore if needed
        init_config = {
            'bots': selected_bot_names,
            'starting_chips': data.get('starting_chips', 1000),
            'small_blind': data.get('small_blind', 10),
            'big_blind': data.get('big_blind', 20),
            'blind_increase_interval': data.get('blind_increase_interval', 10),
        }
        with open(INIT_CONFIG_FILE, 'w') as f:
            json.dump(init_config, f)

        with state_lock:
            tournament, bot_manager, settings = _build_tournament(init_config)

            if tournament is None:
                return jsonify({
                    'success': False,
                    'error': 'Failed to load enough bots'
                }), 400

            tournament_state['bot_manager'] = bot_manager
            tournament_state['tournament'] = tournament
            tournament_state['settings'] = settings
            _clear_hand_state()

            # Clear log queue
            while not tournament_state['log_queue'].empty():
                tournament_state['log_queue'].get()

            logging.info(f"Tournament initialized with {len(tournament.players)} bots (pid={os.getpid()})")

        return jsonify({
            'success': True,
            'message': f'Tournament initialized with {len(tournament.players)} bots'
        })

    except Exception as e:
        import traceback
        logging.error(f"Error initializing tournament: {str(e)}")
        logging.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': 'Failed to initialize tournament'
        }), 500


def _get_active_table(tournament):
    """Get the single active table with 2+ players, or None."""
    for table in tournament.tables.values():
        if len(table.get_active_players()) >= 2:
            return table
    return None


@app.route('/api/tournament/step', methods=['POST'])
def step_tournament():
    """Step through the tournament one action at a time.

    Each call returns one 'event':
      - 'deal': hole cards dealt, blinds posted (start of hand)
      - 'action': a single player action (fold/check/call/raise/all-in)
      - 'community': new community cards revealed (flop/turn/river)
      - 'showdown': hand result with winners
      - 'hand_complete': tournament state updated, ready for next hand
    """
    try:
        with state_lock:
            # Lazy restore: if tournament was lost (server restart, process respawn),
            # rebuild it from the saved init config file.
            if not _ensure_tournament():
                return jsonify({
                    'success': False,
                    'error': 'Tournament not initialized'
                }), 400

            tournament = tournament_state['tournament']
            bot_manager = tournament_state['bot_manager']

            # Diagnostic: increment step counter to trace duplicate calls
            tournament_state.setdefault('_step_seq', 0)
            tournament_state['_step_seq'] += 1
            _seq = tournament_state['_step_seq']
            logging.info(f"[STEP #{_seq}] pid={os.getpid()} game={'ACTIVE' if tournament_state['active_game'] else 'NONE'} phase={tournament_state['hand_phase']}")

            if tournament.is_tournament_complete():
                # Only update stats once (not on every repeated step call)
                if not tournament_state.get('stats_recorded'):
                    tournament_state['stats_recorded'] = True
                    final_results = tournament.get_final_results()
                    for bot_name, chips, position in final_results:
                        base_name = bot_name.split('_')[0] if '_' in bot_name else bot_name
                        won = position == 1
                        bot_storage.update_bot_stats(base_name, won)

                return jsonify({
                    'success': True,
                    'complete': True,
                    'event': 'tournament_complete',
                    'state': get_tournament_state_dict(tournament)
                })

            game = tournament_state['active_game']

            # === No active hand: start a new one ===
            if game is None:
                table = _get_active_table(tournament)

                if table is None:
                    # Try rebalancing to consolidate stranded players
                    if len(tournament.get_active_players()) >= 2:
                        tournament.rebalance_tables()
                        table = _get_active_table(tournament)

                    if table is None:
                        return jsonify({
                            'success': True,
                            'complete': True,
                            'event': 'tournament_complete',
                            'state': get_tournament_state_dict(tournament)
                        })

                player_ids = table.get_active_players()
                small_blind, big_blind = table.get_current_blinds()
                bots = {pid: bot_manager.get_bot(pid) for pid in player_ids}

                game = PokerGame(bots,
                               starting_chips=0,
                               small_blind=small_blind,
                               big_blind=big_blind,
                               dealer_button_index=table.dealer_button % len(player_ids))

                for player in player_ids:
                    game.player_chips[player] = tournament.player_stats[player].chips

                # Start the hand (deal cards, post blinds)
                game.reset_hand()
                game.deal_hole_cards()
                game.post_blinds()
                game._start_betting_round()

                tournament_state['active_game'] = game
                tournament_state['hand_phase'] = 'preflop'

                # Return deal event with hole cards and blinds
                player_cards = {}
                for pid in game.active_players:
                    hand = game.get_player_hand(pid)
                    if hand:
                        player_cards[pid] = [serialize_card(c) for c in hand.cards]

                return jsonify({
                    'success': True,
                    'complete': False,
                    'event': 'deal',
                    'phase': 'preflop',
                    'playerCards': player_cards,
                    'pot': game.pot,
                    'playerChips': game.player_chips.copy(),
                    'playerBets': game.player_bets.copy(),
                    'communityCards': [],
                    'state': get_tournament_state_dict(tournament)
                })

            # === Active hand: process next action ===
            phase = tournament_state['hand_phase']

            # Check if only one player left (everyone else folded)
            if len(game.active_players) <= 1:
                # Skip to showdown
                tournament_state['hand_phase'] = 'showdown'
                phase = 'showdown'

            # If betting round is complete, advance to next phase
            if phase != 'showdown' and game.is_betting_round_complete():
                if phase == 'preflop':
                    game.deal_flop()
                    game.round_name = 'flop'
                    tournament_state['hand_phase'] = 'flop'
                    if len(game.active_players) > 1:
                        game._start_betting_round()

                    return jsonify({
                        'success': True,
                        'complete': False,
                        'event': 'community',
                        'phase': 'flop',
                        'communityCards': [serialize_card(c) for c in game.community_cards],
                        'pot': game.pot,
                        'playerChips': game.player_chips.copy(),
                        'playerBets': game.player_bets.copy(),
                        'state': get_tournament_state_dict(tournament)
                    })

                elif phase == 'flop':
                    game.deal_turn()
                    game.round_name = 'turn'
                    tournament_state['hand_phase'] = 'turn'
                    if len(game.active_players) > 1:
                        game._start_betting_round()

                    return jsonify({
                        'success': True,
                        'complete': False,
                        'event': 'community',
                        'phase': 'turn',
                        'communityCards': [serialize_card(c) for c in game.community_cards],
                        'pot': game.pot,
                        'playerChips': game.player_chips.copy(),
                        'playerBets': game.player_bets.copy(),
                        'state': get_tournament_state_dict(tournament)
                    })

                elif phase == 'turn':
                    game.deal_river()
                    game.round_name = 'river'
                    tournament_state['hand_phase'] = 'river'
                    if len(game.active_players) > 1:
                        game._start_betting_round()

                    return jsonify({
                        'success': True,
                        'complete': False,
                        'event': 'community',
                        'phase': 'river',
                        'communityCards': [serialize_card(c) for c in game.community_cards],
                        'pot': game.pot,
                        'playerChips': game.player_chips.copy(),
                        'playerBets': game.player_bets.copy(),
                        'state': get_tournament_state_dict(tournament)
                    })

                elif phase == 'river':
                    tournament_state['hand_phase'] = 'showdown'
                    phase = 'showdown'

            # === Showdown ===
            if phase == 'showdown':
                if len(game.active_players) > 1:
                    winners = game.determine_winners()
                    game._distribute_pot(winners)
                else:
                    winners = game.active_players.copy()
                    game._distribute_pot(winners)

                game.dealer_button = (game.dealer_button + 1) % len(game.player_ids)

                # Check for disqualified bots
                for player_id in list(game.player_chips.keys()):
                    bot = bot_manager.get_bot(player_id)
                    if bot and bot.is_disqualified():
                        game.player_chips[player_id] = 0

                # Update tournament chips
                for player_id, chips in game.player_chips.items():
                    tournament.update_player_chips(player_id, chips)
                # Update dealer button on the table
                table = next(iter(tournament.tables.values()), None)
                if table:
                    table.dealer_button = game.dealer_button

                # Build showdown result with player hands
                showdown_hands = {}
                for pid in game.player_ids:
                    hand = game.get_player_hand(pid)
                    if hand:
                        showdown_hands[pid] = [serialize_card(c) for c in hand.cards]

                # Advance hand and clear state for next hand
                tournament.advance_hand()
                _clear_hand_state()

                return jsonify({
                    'success': True,
                    'complete': tournament.is_tournament_complete(),
                    'event': 'showdown',
                    'winners': winners,
                    'playerChips': game.player_chips.copy(),
                    'playerHands': showdown_hands,
                    'communityCards': [serialize_card(c) for c in game.community_cards],
                    'pot': 0,
                    'state': get_tournament_state_dict(tournament)
                })

            # === Normal betting action ===
            player_id = game.get_current_player()
            if not player_id:
                # No valid player, force round complete
                tournament_state['hand_phase'] = 'showdown'
                return jsonify({
                    'success': True,
                    'complete': False,
                    'event': 'waiting',
                    'state': get_tournament_state_dict(tournament)
                })

            # Skip all-in players silently (they can't act)
            skipped = 0
            while player_id and game.player_chips[player_id] == 0 and skipped < len(game.active_players):
                game.advance_to_next_player()
                player_id = game.get_current_player()
                skipped += 1

            # After skipping, re-check if the round is now complete
            if not player_id or game.is_betting_round_complete():
                return jsonify({
                    'success': True,
                    'complete': False,
                    'event': 'waiting',
                    'state': get_tournament_state_dict(tournament)
                })

            # Guard: verify player is still active and not folded
            if player_id not in game.active_players:
                game.advance_to_next_player()
                return jsonify({
                    'success': True,
                    'complete': False,
                    'event': 'waiting',
                    'state': get_tournament_state_dict(tournament)
                })

            # Get bot action
            bot = game.player_bots[player_id]
            game_state = game.get_game_state()
            player_hand = game.get_player_hand(player_id)
            legal_actions = game.get_legal_actions(game_state, player_id)
            min_bet = game_state.min_bet
            max_bet = game.player_chips[player_id] + game.player_bets[player_id]

            if player_hand is None:
                game.process_action(player_id, PlayerAction.FOLD, 0)
                game.advance_to_next_player()
                return jsonify({
                    'success': True,
                    'complete': False,
                    'event': 'action',
                    'player': player_id,
                    'action': 'fold',
                    'amount': 0,
                    'pot': game.pot,
                    'playerChips': game.player_chips.copy(),
                    'playerBets': game.player_bets.copy(),
                    'phase': tournament_state['hand_phase'],
                    'state': get_tournament_state_dict(tournament)
                })

            action, amount = bot.get_action(game_state, player_hand.cards, legal_actions, min_bet, max_bet)
            game.process_action(player_id, action, amount)
            game.advance_to_next_player()

            # Detect RAISE→ALL_IN conversion (player went to 0 chips after a raise)
            action_name = action.name.lower()
            if action_name == 'raise' and game.player_chips[player_id] == 0:
                action_name = 'all_in'

            return jsonify({
                'success': True,
                'complete': False,
                'event': 'action',
                'player': player_id,
                'action': action_name,
                'amount': amount,
                'pot': game.pot,
                'playerChips': game.player_chips.copy(),
                'playerBets': game.player_bets.copy(),
                'communityCards': [serialize_card(c) for c in game.community_cards],
                'phase': tournament_state['hand_phase'],
                'state': get_tournament_state_dict(tournament)
            })

    except Exception as e:
        logging.error(f"Error in step_tournament: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())
        # Clear broken hand state so next step starts fresh
        _clear_hand_state()
        return jsonify({
            'success': False,
            'error': 'Tournament step failed'
        }), 500


@app.route('/api/tournament/state', methods=['GET'])
def get_tournament_state():
    """Get current tournament state"""
    try:
        with state_lock:
            if not _ensure_tournament():
                return jsonify({
                    'success': False,
                    'error': 'Tournament not initialized'
                }), 400

            tournament = tournament_state['tournament']
            game = tournament_state['active_game']
            result = get_tournament_state_dict(tournament)

            # Include live hand state if mid-hand
            if game:
                result['communityCards'] = [serialize_card(c) for c in game.community_cards]
                result['pot'] = game.pot
                for p in result['players']:
                    pid = p['id']
                    if pid in game.player_chips:
                        p['chips'] = game.player_chips[pid]
                    if pid in game.player_bets:
                        p['bet'] = game.player_bets[pid]
                    hand = game.get_player_hand(pid)
                    if hand:
                        p['cards'] = [serialize_card(c) for c in hand.cards]

            return jsonify({
                'success': True,
                'state': result
            })
    except Exception as e:
        logging.error(f"Error getting tournament state: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Failed to get state'
        }), 500


@app.route('/api/logs/stream')
def stream_logs():
    """Server-sent events endpoint for streaming logs"""
    def generate():
        while True:
            try:
                log_entry = tournament_state['log_queue'].get(timeout=1)
                yield f"data: {json.dumps(log_entry)}\n\n"
            except:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
    
    return Response(generate(), mimetype='text/event-stream')


@app.route('/api/tournament/reset', methods=['POST'])
def reset_tournament():
    """Reset the tournament"""
    try:
        with state_lock:
            tournament_state['tournament'] = None
            tournament_state['bot_manager'] = None
            tournament_state['settings'] = None
            _clear_hand_state()

            # Remove saved init config so lazy restore won't recreate
            if os.path.exists(INIT_CONFIG_FILE):
                os.remove(INIT_CONFIG_FILE)

            # Clear logs
            while not tournament_state['log_queue'].empty():
                tournament_state['log_queue'].get()
        
        logging.info("Tournament reset")
        return jsonify({
            'success': True,
            'message': 'Tournament reset'
        })
    except Exception as e:
        logging.error(f"Error resetting tournament: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Reset failed'
        }), 500


def get_tournament_state_dict(tournament):
    """Convert tournament state to dictionary"""
    active_players = tournament.get_active_players()
    
    players = []
    for i, player_id in enumerate(tournament.players):
        stats = tournament.player_stats[player_id]
        players.append({
            'id': player_id,
            'name': player_id.replace('_', ' ').title(),
            'chips': stats.chips,
            'position': i,
            'isEliminated': stats.is_eliminated,
            'isActive': player_id in active_players,
            'cards': [],
            'bet': 0
        })
    
    return {
        'handNumber': tournament.current_hand,
        'totalPlayers': len(tournament.players),
        'activePlayers': len(active_players),
        'eliminatedPlayers': len(tournament.eliminated_players),
        'isComplete': tournament.is_tournament_complete(),
        'players': players,
        'communityCards': [],
        'pot': 0,
        'leaderboard': [
            {'name': name, 'chips': chips, 'position': pos}
            for name, chips, pos in tournament.get_leaderboard()
        ]
    }


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404


@app.errorhandler(500)
def internal_error(error):
    logging.error(f"Internal error: {str(error)}")
    return jsonify({'error': 'Internal server error'}), 500


# ============================================================================
# STARTUP
# ============================================================================

if __name__ == '__main__':
    print("=" * 80)
    print("🚀 POKER TOURNAMENT SERVER - PRODUCTION READY")
    print("=" * 80)
    print()
    print("📍 Server URLs:")
    print(f"   User Portal:     http://localhost:5000/")
    print(f"   Tournament:      http://localhost:5000/tournament")
    print(f"   Admin Login:     http://localhost:5000/admin/login")
    print()
    print("💾 Data Persistence:")
    print(f"   Admin accounts:  admin_auth.json")
    print(f"   Bot submissions: bot_reviews/submissions.json")
    print(f"   Approved bots:   encrypted_bots/metadata.json")
    print(f"   Server logs:     logs/server.log")
    print()
    print("🔐 Security:")
    print(f"   Master password: {'✓ SET' if MASTER_PASSWORD else '✗ NOT SET'}")
    print(f"   Secret key:      {'✓ SET' if app.secret_key else '✗ NOT SET'}")
    print()
    print("⚠️  IMPORTANT:")
    print("   - All bot data persists across server restarts")
    print("   - Submissions remain in review queue")
    print("   - Approved bots stay approved")
    print("   - Only active tournaments are cleared on restart")
    print()
    print("=" * 80)
    
    # Production mode check
    if os.environ.get('FLASK_ENV') == 'production':
        print("🏭 PRODUCTION MODE")
        print("   Using Waitress for production serving...")
        from waitress import serve
        serve(app, host='0.0.0.0', port=5000, threads=4)
    else:
        print("🔧 DEVELOPMENT MODE")
        print("   For production, set: FLASK_ENV=production")
        app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)