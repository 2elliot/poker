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

from backend.tournament_runner import TournamentRunner, TournamentSettings, TournamentType
from backend.bot_manager import BotManager
from backend.engine.poker_game import PokerGame, PlayerAction
from backend.tournament import PokerTournament

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
    'runner': None,
    'tournament': None,
    'is_running': False,
    'is_paused': False,
    'current_game': None,
    'bot_manager': None,
    'log_queue': Queue(),
    'settings': None
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

# Setup logging
queue_handler = QueueHandler(tournament_state['log_queue'])
queue_handler.setFormatter(logging.Formatter('%(message)s'))
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
        
        with state_lock:
            # Create settings
            settings = TournamentSettings(
                tournament_type=TournamentType.FREEZE_OUT,
                starting_chips=data.get('starting_chips', 1000),
                small_blind=data.get('small_blind', 10),
                big_blind=data.get('big_blind', 20),
                time_limit_per_action=10.0,
                blind_increase_interval=data.get('blind_increase_interval', 10),
                blind_increase_factor=1.5
            )
            
            tournament_state['settings'] = settings
            
            # Create bot manager
            from backend.bot_manager import BotWrapper
            bot_manager = BotManager("players", 10.0)
            bot_manager.bots = {}  # Clear, we'll load from storage
            
            # Load approved bots from encrypted storage
            player_names = []
            bot_count = {}
            
            for bot_data in selected_bot_names:
                # Handle both string and dict formats
                if isinstance(bot_data, dict):
                    bot_name = bot_data.get('id') or bot_data.get('name')
                else:
                    bot_name = bot_data
                
                # Skip if bot_name is None or empty
                if not bot_name:
                    logging.warning("Skipping bot with invalid name")
                    continue
                
                # Load bot from encrypted storage using master password
                if MASTER_PASSWORD is None:
                    logging.error("MASTER_PASSWORD not set")
                    continue
                bot_instance = bot_storage.load_bot(bot_name, MASTER_PASSWORD)
                
                if bot_instance is None:
                    logging.warning(f"Failed to load bot: {bot_name}")
                    continue
                
                # Handle duplicates (same bot multiple times)
                if bot_name not in bot_count:
                    bot_count[bot_name] = 0
                bot_count[bot_name] += 1
                
                # Create unique player name for duplicates
                if bot_count[bot_name] > 1:
                    player_name = f"{bot_name}_{bot_count[bot_name]}"
                    # Create new instance with unique name
                    unique_bot = bot_instance.__class__(player_name)
                else:
                    player_name = bot_name
                    unique_bot = bot_instance
                
                player_names.append(player_name)
                
                # Wrap bot for safety and timeout handling
                bot_wrapper = BotWrapper(player_name, unique_bot, 10.0)
                bot_manager.bots[player_name] = bot_wrapper
            
            if len(player_names) < 2:
                return jsonify({
                    'success': False,
                    'error': 'Failed to load enough bots'
                }), 400
            
            tournament_state['bot_manager'] = bot_manager
            
            # Create tournament
            tournament = PokerTournament(player_names, settings)
            tournament_state['tournament'] = tournament
            tournament_state['is_running'] = False
            tournament_state['is_paused'] = False
            
            # Clear log queue
            while not tournament_state['log_queue'].empty():
                tournament_state['log_queue'].get()
            
            logging.info(f"Tournament initialized with {len(player_names)} bots")
        
        return jsonify({
            'success': True,
            'message': f'Tournament initialized with {len(player_names)} bots'
        })
        
    except Exception as e:
        import traceback
        logging.error(f"Error initializing tournament: {str(e)}")
        logging.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': 'Failed to initialize tournament'
        }), 500


@app.route('/api/tournament/step', methods=['POST'])
def step_tournament():
    """Execute one hand of the tournament"""
    try:
        with state_lock:
            tournament = tournament_state['tournament']
            bot_manager = tournament_state['bot_manager']
            
            if not tournament:
                return jsonify({
                    'success': False,
                    'error': 'Tournament not initialized'
                }), 400
            
            if tournament.is_tournament_complete():
                # Update bot statistics in storage
                final_results = tournament.get_final_results()
                for bot_name, chips, position in final_results:
                    # Remove instance number suffix if present
                    base_name = bot_name.split('_')[0] if '_' in bot_name else bot_name
                    won = position == 1
                    bot_storage.update_bot_stats(base_name, won)
                
                return jsonify({
                    'success': True,
                    'complete': True,
                    'state': get_tournament_state_dict(tournament)
                })
            
            # Play one round
            active_tables = {tid: table for tid, table in tournament.tables.items() 
                           if len(table.get_active_players()) >= 2}
            
            for table_id, table in active_tables.items():
                player_ids = table.get_active_players()
                if len(player_ids) >= 2:
                    small_blind, big_blind = table.get_current_blinds()
                    
                    bots = {pid: bot_manager.get_bot(pid) for pid in player_ids}
                    
                    game = PokerGame(bots,
                                   starting_chips=0,
                                   small_blind=small_blind,
                                   big_blind=big_blind,
                                   dealer_button_index=table.dealer_button % len(player_ids))
                    
                    # Set chip counts
                    for player in player_ids:
                        game.player_chips[player] = tournament.player_stats[player].chips
                    
                    # Play hand
                    final_chips = game.play_hand()
                    
                    # Check for disqualified bots
                    for player_id in list(final_chips.keys()):
                        bot = bot_manager.get_bot(player_id)
                        if bot and bot.is_disqualified():
                            final_chips[player_id] = 0
                    
                    # Update tournament
                    for player_id, chips in final_chips.items():
                        tournament.update_player_chips(player_id, chips)
                    
                    tournament.tables[table_id].dealer_button = game.dealer_button
            
            # Advance tournament
            tournament.advance_hand()
            
            # Rebalance if needed
            if tournament.should_rebalance_tables():
                tournament.rebalance_tables()
        
        return jsonify({
            'success': True,
            'complete': tournament.is_tournament_complete(),
            'state': get_tournament_state_dict(tournament)
        })
        
    except Exception as e:
        logging.error(f"Error in step_tournament: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': 'Tournament step failed'
        }), 500


@app.route('/api/tournament/state', methods=['GET'])
def get_tournament_state():
    """Get current tournament state"""
    try:
        with state_lock:
            tournament = tournament_state['tournament']
            
            if not tournament:
                return jsonify({
                    'success': False,
                    'error': 'Tournament not initialized'
                }), 400
            
            return jsonify({
                'success': True,
                'state': get_tournament_state_dict(tournament)
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
            tournament_state['is_running'] = False
            tournament_state['is_paused'] = False
            tournament_state['current_game'] = None
            
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
        app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)