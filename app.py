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

from backend.tournament import TournamentSettings, TournamentType, PokerTournament
from backend.bot_manager import BotManager, BOT_TURN_TIMEOUT
from backend.engine.poker_game import PokerGame, PlayerAction

# Import security systems
from secure_admin_auth import AdminAuthSystem
from user_auth import UserAuthSystem, User
from bot_approval_system import BotReviewSystem
from secure_bot_storage import SecureBotStorage
from match_scheduler import MatchScheduler

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
login_manager.login_view = 'user_login_page' # type: ignore

@login_manager.unauthorized_handler
def unauthorized():
    """Redirect to appropriate login page based on route"""
    if request.path.startswith('/api/'):
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    if request.path.startswith('/admin'):
        return redirect(url_for('login_page'))
    return redirect(url_for('user_login_page'))

# Initialize systems - ALL DATA PERSISTS
auth_system = AdminAuthSystem()
user_system = UserAuthSystem()
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

# Background match scheduler — starts automatically on import
match_scheduler = MatchScheduler(bot_storage, MASTER_PASSWORD)
match_scheduler.start()

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
    """Flask-Login user loader — handles both admin and regular users"""
    # Regular users have "user:" prefix
    if user_id.startswith("user:"):
        return user_system.get_user(user_id)
    # Otherwise check admin accounts
    data = auth_system._load_auth_data()
    if user_id in data["admins"]:
        return User(user_id, user_id, is_admin=True)
    return None


# ============================================================================
# TEMPLATE CONTEXT
# ============================================================================

@app.context_processor
def inject_globals():
    """Inject global template variables for nav bar"""
    is_admin = current_user.is_authenticated and getattr(current_user, 'is_admin', False)
    username = current_user.username if current_user.is_authenticated else None
    return dict(is_admin=is_admin, current_username=username)


# ============================================================================
# PUBLIC ROUTES - User bot submission and status
# ============================================================================

@app.route('/')
def index():
    """Main landing page - tournament visualization"""
    return render_template('tournament.html')


@app.route('/submit')
def submit_page():
    """Bot submission portal"""
    return render_template('submit.html')


@app.route('/leaderboard')
def leaderboard_page():
    """Leaderboard page"""
    return render_template('leaderboard.html')


@app.route('/api/leaderboard', methods=['GET'])
def get_leaderboard():
    """Get bot leaderboard from the match scheduler, enriched with creator info"""
    try:
        board = match_scheduler.get_leaderboard()

        # Enrich with creator username from approved bots
        approved = review_system.submissions.get("approved_bots", {})
        for entry in board:
            bot_info = approved.get(entry["name"], {})
            entry["creator"] = bot_info.get("submitter_username", "unknown")

        return jsonify({'success': True, 'leaderboard': board})
    except Exception as e:
        logging.error(f"Error getting leaderboard: {e}")
        return jsonify({'success': False, 'error': 'Failed to load leaderboard'}), 500


@app.route('/api/bot-stats/<bot_name>', methods=['GET'])
def get_bot_detail(bot_name):
    """Get detailed stats for a single bot (used by profile modal)"""
    try:
        stats = match_scheduler.get_bot_stats(bot_name)
        if not stats:
            return jsonify({'success': False, 'error': 'Bot not found'}), 404

        # Add creator info
        approved = review_system.submissions.get("approved_bots", {})
        bot_info = approved.get(bot_name, {})
        stats["creator"] = bot_info.get("submitter_username", "unknown")

        return jsonify({'success': True, 'stats': stats})
    except Exception as e:
        logging.error(f"Error getting bot stats for {bot_name}: {e}")
        return jsonify({'success': False, 'error': 'Failed to load stats'}), 500


@app.route('/api/live-match', methods=['GET'])
def get_live_match():
    """Get live match events for spectator mode.
    Query param: since=<seq_number> to get events after a given sequence.
    """
    since = request.args.get('since', 0, type=int)
    data = match_scheduler.get_events_since(since_seq=since)
    return jsonify({'success': True, **data})


@app.route('/login')
def user_login_page():
    """User login/register page"""
    if current_user.is_authenticated:
        return redirect(url_for('submit_page'))
    return render_template('login.html')


@app.route('/api/user/register', methods=['POST'])
def user_register():
    """Register a new user account"""
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({"success": False, "error": "Username and password required"}), 400

    result = user_system.register(username, password)

    if result["success"]:
        # Auto-login after registration
        auth_result = user_system.authenticate(username, password)
        if auth_result["success"]:
            login_user(auth_result["user"], remember=True)
            session.permanent = True
        return jsonify({"success": True, "message": "Account created successfully"})
    else:
        return jsonify(result), 400


@app.route('/api/user/login', methods=['POST'])
def user_login():
    """User login endpoint"""
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({"success": False, "error": "Username and password required"}), 400

    result = user_system.authenticate(username, password)

    if result["success"]:
        login_user(result["user"], remember=True)
        session.permanent = True
        return jsonify({"success": True, "message": "Login successful", "username": result["user"].username})
    else:
        return jsonify(result), 401


@app.route('/api/user/logout', methods=['POST'])
def user_logout():
    """User logout endpoint"""
    logout_user()
    return jsonify({"success": True, "message": "Logged out"})


@app.route('/api/bots', methods=['GET'])
def get_available_bots():
    """Get list of APPROVED bots available for tournaments"""
    try:
        approved_bots = bot_storage.list_bots()
        # Look up creator usernames from the review system
        approved_map = review_system.submissions.get("approved_bots", {})

        bots_info = []
        for bot in approved_bots:
            # Merge scheduler stats if available
            sched_stats = match_scheduler.get_bot_stats(bot['name'])
            creator = approved_map.get(bot['name'], {}).get('submitter_username', '')
            bots_info.append({
                'id': bot['name'],
                'name': bot['name'],
                'type': 'Approved Bot',
                'creator': creator,
                'elo': sched_stats['elo'] if sched_stats else 1200,
                'hands_played': sched_stats['hands_played'] if sched_stats else 0,
                'win_rate': sched_stats['win_rate'] if sched_stats else 0,
                'calibrated': sched_stats['calibrated'] if sched_stats else False,
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


@app.route('/api/bots/my-pending', methods=['GET'])
@login_required
def get_my_pending_bots():
    """Get current user's pending bots that can be tested in custom table"""
    try:
        with review_system._lock:
            review_system.submissions = review_system._load_submissions()
            pending = []
            for sub_id, sub in review_system.submissions["submissions"].items():
                owner = sub.get("submitter_username", sub.get("submitter_email"))
                if owner != current_user.username:
                    continue
                if sub["status"] != "pending_review":
                    continue
                code_file = sub.get("code_file")
                if not code_file or not os.path.exists(code_file):
                    continue
                pending.append({
                    'id': f'pending:{sub_id}',
                    'name': sub["bot_name"],
                    'submission_id': sub_id,
                    'type': 'pending',
                    'creator': current_user.username,
                })
        return jsonify({'success': True, 'bots': pending})
    except Exception as e:
        logging.error(f"Error getting pending bots: {e}")
        return jsonify({'success': False, 'error': 'Failed to load pending bots'}), 500


@app.route('/api/bots/submit', methods=['POST'])
@login_required
def submit_bot():
    """Submit a bot for review (requires login)"""
    try:
        data = request.json
        bot_name = data.get('bot_name', '').strip()
        bot_code = data.get('bot_code', '')

        if not bot_name or not bot_code:
            return jsonify({'success': False, 'error': 'Bot name and code are required'}), 400

        if len(bot_name) < 3 or len(bot_name) > 50:
            return jsonify({'success': False, 'error': 'Bot name must be between 3 and 50 characters'}), 400

        if len(bot_code) > 500 * 1024:
            return jsonify({'success': False, 'error': 'Bot code too large (max 500KB)'}), 400

        result = review_system.submit_bot(
            bot_name=bot_name,
            bot_code=bot_code,
            submitter_username=current_user.username
        )

        if result['success']:
            logging.info(f"New bot submission: {bot_name} from {current_user.username}")

        return jsonify(result)

    except Exception as e:
        logging.error(f"Error submitting bot: {str(e)}")
        return jsonify({'success': False, 'error': 'Submission failed. Please try again.'}), 500


@app.route('/api/bots/my-submissions', methods=['GET'])
@login_required
def get_my_submissions():
    """Get current user's bot submissions"""
    try:
        submissions = review_system.get_user_submissions(current_user.username)
        return jsonify({'success': True, 'submissions': submissions})
    except Exception as e:
        logging.error(f"Error getting submissions: {str(e)}")
        return jsonify({'success': False, 'error': 'Failed to load submissions'}), 500


@app.route('/api/bots/resubmit/<submission_id>', methods=['POST'])
@login_required
def resubmit_bot(submission_id):
    """Resubmit/update a bot (requires login)"""
    try:
        data = request.json
        new_code = data.get('bot_code')

        if not new_code:
            return jsonify({'success': False, 'error': 'Bot code is required'}), 400

        # Check if this is an approved bot being updated — if so, snapshot
        # its stats, pull it from active play, and reset so the old version
        # doesn't keep competing
        with review_system._lock:
            review_system.submissions = review_system._load_submissions()
            sub = review_system.submissions["submissions"].get(submission_id)
            if sub and sub["status"] == "approved":
                bot_name = sub["bot_name"]
                # Save current stats as previous_version before resetting
                match_scheduler.snapshot_bot_version(bot_name)
                if bot_name in bot_storage.metadata.get("bots", {}):
                    bot_storage.delete_bot(bot_name, MASTER_PASSWORD)
                match_scheduler.delete_bot_stats(bot_name, preserve_version=True)
                logging.info(f"Bot '{bot_name}' removed from active play for resubmission")

        result = review_system.resubmit_bot(submission_id, new_code, current_user.username)
        return jsonify(result)

    except Exception as e:
        logging.error(f"Error resubmitting bot: {str(e)}")
        return jsonify({'success': False, 'error': 'Resubmission failed'}), 500


@app.route('/api/bots/withdraw/<submission_id>', methods=['POST'])
@login_required
def withdraw_bot(submission_id):
    """Withdraw a pending submission (requires login)"""
    try:
        result = review_system.withdraw_submission(submission_id, current_user.username)
        return jsonify(result)
    except Exception as e:
        logging.error(f"Error withdrawing bot: {str(e)}")
        return jsonify({'success': False, 'error': 'Withdrawal failed'}), 500


@app.route('/api/bots/code/<submission_id>', methods=['GET'])
@login_required
def get_bot_code(submission_id):
    """Get the current code for a user's bot (owner only)"""
    try:
        with review_system._lock:
            review_system.submissions = review_system._load_submissions()
            if submission_id not in review_system.submissions["submissions"]:
                return jsonify({'success': False, 'error': 'Submission not found'}), 404

            sub = review_system.submissions["submissions"][submission_id]
            owner = sub.get("submitter_username", sub.get("submitter_email"))
            if owner != current_user.username:
                return jsonify({'success': False, 'error': 'Unauthorized'}), 403

            bot_name = sub["bot_name"]

        # If there's a pending review file, return that
        code_file = sub.get("code_file")
        if code_file and os.path.exists(code_file):
            with open(code_file, 'r', encoding='utf-8') as f:
                return jsonify({'success': True, 'code': f.read()})

        # Otherwise decrypt from storage (approved bot)
        code = bot_storage.get_bot_code(bot_name, MASTER_PASSWORD)
        if code is not None:
            return jsonify({'success': True, 'code': code})

        return jsonify({'success': False, 'error': 'Bot code not available'}), 404

    except Exception as e:
        logging.error(f"Error getting bot code: {str(e)}")
        return jsonify({'success': False, 'error': 'Failed to load code'}), 500


@app.route('/api/bots/delete/<submission_id>', methods=['POST'])
@login_required
def user_delete_bot(submission_id):
    """User deletes their own bot (removes from storage, submissions, and stats)"""
    try:
        with review_system._lock:
            review_system.submissions = review_system._load_submissions()
            if submission_id not in review_system.submissions["submissions"]:
                return jsonify({'success': False, 'error': 'Submission not found'}), 404

            sub = review_system.submissions["submissions"][submission_id]
            owner = sub.get("submitter_username", sub.get("submitter_email"))
            if owner != current_user.username:
                return jsonify({'success': False, 'error': 'Unauthorized'}), 403

            bot_name = sub["bot_name"]

            # Delete from encrypted storage if it was approved
            if bot_name in bot_storage.metadata.get("bots", {}):
                bot_storage.delete_bot(bot_name, MASTER_PASSWORD)

            # Remove from approved_bots
            review_system.submissions["approved_bots"].pop(bot_name, None)

            # Clean up code file and remove submission
            review_system._cleanup_submission_files(submission_id)
            del review_system.submissions["submissions"][submission_id]
            review_system._save_submissions()

        # Remove from match stats
        match_scheduler.delete_bot_stats(bot_name)

        logging.info(f"Bot '{bot_name}' deleted by owner {current_user.username}")
        return jsonify({'success': True, 'message': f"Bot '{bot_name}' deleted"})

    except Exception as e:
        logging.error(f"Error deleting bot: {str(e)}")
        return jsonify({'success': False, 'error': 'Failed to delete bot'}), 500


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

        # Enrich approved bots with match stats
        for sub in all_submissions:
            if sub.get('status') == 'approved':
                stats = match_scheduler.get_bot_stats(sub['bot_name'])
                if stats:
                    sub['bot_stats'] = {
                        'elo': stats['elo'],
                        'hands_played': stats['hands_played'],
                        'win_rate': stats['win_rate'],
                        'tournaments_won': stats['tournaments_won'],
                        'tournaments_played': stats['tournaments_played'],
                    }

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


@app.route('/api/admin/delete-bot/<bot_name>', methods=['POST'])
@login_required
def admin_delete_bot(bot_name):
    """ADMIN - Delete a bot from storage, submissions, and stats"""
    if not current_user.is_admin:
        return jsonify({"error": "Unauthorized"}), 403

    try:
        # Delete from encrypted storage
        result = bot_storage.delete_bot(bot_name, MASTER_PASSWORD)
        if not result["success"]:
            logging.warning(f"Bot storage delete note for '{bot_name}': {result.get('error')}")

        # Remove from submissions and approved_bots
        with review_system._lock:
            review_system.submissions = review_system._load_submissions()
            # Remove from approved_bots
            review_system.submissions["approved_bots"].pop(bot_name, None)
            # Remove any submission entries for this bot
            to_remove = [
                sid for sid, sub in review_system.submissions["submissions"].items()
                if sub["bot_name"] == bot_name
            ]
            for sid in to_remove:
                # Clean up code file
                code_file = review_system.submissions["submissions"][sid].get("code_file")
                if code_file and os.path.exists(code_file):
                    os.remove(code_file)
                del review_system.submissions["submissions"][sid]
            review_system._save_submissions()

        # Remove from match stats
        match_scheduler.delete_bot_stats(bot_name)

        auth_system._log_audit_event(
            "BOT_DELETED",
            current_user.username,
            request.remote_addr or "unknown",
            f"Deleted bot '{bot_name}'"
        )
        logging.info(f"Bot '{bot_name}' deleted by {current_user.username}")

        return jsonify({"success": True, "message": f"Bot '{bot_name}' deleted successfully"})

    except Exception as e:
        logging.error(f"Error deleting bot '{bot_name}': {str(e)}")
        return jsonify({"success": False, "error": "Failed to delete bot"}), 500


@app.route('/api/admin/reset-leaderboard', methods=['POST'])
@login_required
def admin_reset_leaderboard():
    """ADMIN - Reset all leaderboard rankings and match statistics"""
    if not current_user.is_admin:
        return jsonify({"error": "Unauthorized"}), 403

    try:
        match_scheduler.reset_stats()

        auth_system._log_audit_event(
            "LEADERBOARD_RESET",
            current_user.username,
            request.remote_addr or "unknown",
            "Reset all leaderboard rankings"
        )
        logging.info(f"Leaderboard reset by {current_user.username}")

        return jsonify({"success": True, "message": "Leaderboard rankings have been reset"})

    except Exception as e:
        logging.error(f"Error resetting leaderboard: {str(e)}")
        return jsonify({"success": False, "error": "Failed to reset leaderboard"}), 500


# ============================================================================
# TOURNAMENT ROUTES - Use approved bots from storage
# ============================================================================

def serialize_card(card):
    """Convert a Card object to a JSON-serializable dict"""
    rank_str = {2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8",
                9: "9", 10: "10", 11: "J", 12: "Q", 13: "K", 14: "A"}
    return {'value': rank_str[card.rank.value], 'suit': card.suit.value}


def _clear_hand_state():
    """Reset the step-by-step hand state"""
    tournament_state['hand_phase'] = None
    tournament_state['active_game'] = None
    tournament_state['stats_recorded'] = False


@app.route('/api/tournament/init', methods=['POST'])
def initialize_tournament():
    """Initialize a new tournament with APPROVED bots only"""
    try:
        from backend.bot_manager import BotWrapper, BOT_TURN_TIMEOUT

        data = request.json
        selected_bot_names = data.get('bots', [])

        if len(selected_bot_names) < 2:
            return jsonify({
                'success': False,
                'error': 'Need at least 2 bots to start a tournament'
            }), 400

        settings = TournamentSettings(
            tournament_type=TournamentType.FREEZE_OUT,
            starting_chips=data.get('starting_chips', 1000),
            small_blind=data.get('small_blind', 10),
            big_blind=data.get('big_blind', 20),
            time_limit_per_action=10.0,
            blind_increase_interval=data.get('blind_increase_interval', 10),
            blind_increase_factor=1.5
        )

        bot_manager = BotManager("players", BOT_TURN_TIMEOUT)
        bot_manager.bots = {}

        player_names = []
        bot_count = {}

        for bot_data in selected_bot_names:
            if isinstance(bot_data, dict):
                bot_name = bot_data.get('id') or bot_data.get('name')
            else:
                bot_name = bot_data

            if not bot_name:
                continue

            # Handle pending bots (format: "pending:<submission_id>")
            if bot_name.startswith('pending:'):
                sub_id = bot_name.split(':', 1)[1]
                # Reload submissions from disk to get latest state
                with review_system._lock:
                    review_system.submissions = review_system._load_submissions()
                sub = review_system.submissions.get("submissions", {}).get(sub_id)
                if not sub or sub["status"] != "pending_review":
                    logging.warning(f"Pending bot {sub_id}: submission not found or status={sub.get('status') if sub else 'N/A'}")
                    continue
                code_file = sub.get("code_file")
                if not code_file or not os.path.exists(code_file):
                    logging.warning(f"Pending bot {sub_id}: code file missing ({code_file})")
                    continue
                with open(code_file, 'r', encoding='utf-8') as f:
                    code = f.read()
                validation = review_system._validate_bot_code(code, sub["bot_name"])
                if not validation.get("valid"):
                    logging.warning(f"Pending bot {sub_id}: validation failed: {validation.get('error')}")
                    continue
                # Load bot from source code
                bot_instance = bot_storage._load_bot_from_string(code, sub["bot_name"])
                if bot_instance is None:
                    logging.warning(f"Pending bot {sub_id}: _load_bot_from_string returned None")
                    continue
                actual_name = sub["bot_name"]
            else:
                if MASTER_PASSWORD is None:
                    continue
                bot_instance = bot_storage.load_bot(bot_name, MASTER_PASSWORD)
                if bot_instance is None:
                    continue
                actual_name = bot_name

            if actual_name not in bot_count:
                bot_count[actual_name] = 0
            bot_count[actual_name] += 1

            if bot_count[actual_name] > 1:
                player_name = f"{actual_name}_{bot_count[actual_name]}"
                # Re-load for unique instance
                if bot_name.startswith('pending:'):
                    with open(code_file, 'r', encoding='utf-8') as f:
                        unique_bot = bot_storage._load_bot_from_string(f.read(), actual_name)
                else:
                    unique_bot = bot_storage.load_bot(bot_name, MASTER_PASSWORD)
                if unique_bot is None:
                    continue
                unique_bot.name = player_name
            else:
                player_name = actual_name
                unique_bot = bot_instance

            player_names.append(player_name)
            bot_wrapper = BotWrapper(player_name, unique_bot, BOT_TURN_TIMEOUT)
            bot_manager.bots[player_name] = bot_wrapper

        if len(player_names) < 2:
            return jsonify({
                'success': False,
                'error': 'Failed to load enough bots'
            }), 400

        # Force all players onto a single table
        settings.max_players_per_table = len(player_names)

        tournament = PokerTournament(player_names, settings)

        with state_lock:
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
            if tournament_state['tournament'] is None:
                return jsonify({
                    'success': False,
                    'error': 'Tournament not initialized'
                }), 400

            tournament = tournament_state['tournament']
            bot_manager = tournament_state['bot_manager']

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
            if tournament_state['tournament'] is None:
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
    print("Server URLs:")
    print(f"   Tournament:      http://localhost:5000/")
    print(f"   Submit Bot:      http://localhost:5000/submit")
    print(f"   Leaderboard:     http://localhost:5000/leaderboard")
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
        print("PRODUCTION MODE")
        print("   Using Waitress for production serving...")
        from waitress import serve
        serve(app, host='0.0.0.0', port=5000, threads=4)
    else:
        print("DEVELOPMENT MODE")
        print("   For production, set: FLASK_ENV=production")
        app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)