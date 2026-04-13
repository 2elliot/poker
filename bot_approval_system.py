"""
Bot Approval and Review System
Allows manual review of bots before they become active in tournaments
Includes email notifications and comprehensive logging
"""
import os
import json
import hashlib
from datetime import datetime
from typing import List, Dict, Optional
from enum import Enum
import logging
import sys
import importlib.util
import types
from threading import RLock

# Ensure bot code imports like "from bot_api import PokerBotAPI" resolve correctly
import backend.bot_api
import backend.engine
import backend.engine.poker_game
import backend.engine.cards
sys.modules.setdefault('bot_api', backend.bot_api)
sys.modules.setdefault('engine', backend.engine)
sys.modules.setdefault('engine.poker_game', backend.engine.poker_game)
sys.modules.setdefault('engine.cards', backend.engine.cards)


class BotStatus(Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVISION_REQUESTED = "revision_requested"


class BotReviewSystem:
    """Manages bot submissions, reviews, and approvals"""
    
    def __init__(self, review_directory: str = "bot_reviews", 
                 approved_directory: str = "encrypted_bots"):
        self.review_directory = review_directory
        self.approved_directory = approved_directory
        
        # Create directories if they don't exist
        os.makedirs(review_directory, exist_ok=True)
        os.makedirs(approved_directory, exist_ok=True)
        
        self.submissions_file = os.path.join(review_directory, "submissions.json")
        
        # Thread safety for concurrent requests (RLock allows reentrant locking
        # since _save_submissions is called from methods that already hold the lock)
        self._lock = RLock()
        
        self.submissions = self._load_submissions()
        
        # Initialize logger
        self.logger = logging.getLogger("bot_review_system")
        self.logger.info(f"Bot Review System initialized: {review_directory}")
    
    def _load_submissions(self) -> Dict:
        """Load submission metadata (thread-safe)"""
        if os.path.exists(self.submissions_file):
            try:
                with open(self.submissions_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                # Ensure both keys exist
                if "submissions" not in data:
                    data["submissions"] = {}
                if "approved_bots" not in data:
                    data["approved_bots"] = {}
                    
                return data
            except (json.JSONDecodeError, IOError) as e:
                self.logger.error(f"Error loading submissions file: {str(e)}")
                # Corrupted file, create backup and start fresh
                if os.path.exists(self.submissions_file):
                    backup_file = f"{self.submissions_file}.backup_{int(datetime.now().timestamp())}"
                    try:
                        os.rename(self.submissions_file, backup_file)
                        self.logger.warning(f"Corrupted file backed up to {backup_file}")
                    except:
                        pass
                return {"submissions": {}, "approved_bots": {}}
        
        return {"submissions": {}, "approved_bots": {}}
    
    def _save_submissions(self):
        """Save submission metadata (thread-safe, atomic write)"""
        with self._lock:
            try:
                # Write to temporary file first (atomic write)
                temp_file = f"{self.submissions_file}.tmp"
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(self.submissions, f, indent=2, ensure_ascii=False)
                
                # Atomic rename (replaces old file)
                if os.path.exists(self.submissions_file):
                    os.replace(temp_file, self.submissions_file)
                else:
                    os.rename(temp_file, self.submissions_file)
                    
                self.logger.debug("Submissions metadata saved successfully")
            except Exception as e:
                self.logger.error(f"Failed to save submissions metadata: {str(e)}")
                # Try to clean up temp file
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except:
                        pass
                raise
    
    def submit_bot(self, bot_name: str, bot_code: str,
                   submitter_username: str) -> Dict:
        """
        Submit a bot for review (tied to a user account)
        """
        self.logger.info(f"New bot submission attempt: {bot_name} from {submitter_username}")

        with self._lock:
            # Reload submissions to get latest data
            self.submissions = self._load_submissions()

            # Check if bot name is taken by another user
            if bot_name in self.submissions["approved_bots"]:
                existing = self.submissions["approved_bots"][bot_name]
                if existing.get("submitter_username") != submitter_username:
                    return {"success": False, "error": "Bot name already taken by another user"}

            # Check if user has pending submissions for this name
            for sub_id, sub in self.submissions["submissions"].items():
                if (sub["bot_name"] == bot_name and
                    sub.get("submitter_username") == submitter_username and
                    sub["status"] in [BotStatus.PENDING_REVIEW.value,
                                     BotStatus.REVISION_REQUESTED.value]):
                    return {
                        "success": False,
                        "error": f"You already have a pending submission for '{bot_name}'",
                        "submission_id": sub_id
                    }

            # Generate submission ID
            submission_id = hashlib.sha256(
                f"{bot_name}{submitter_username}{datetime.now().isoformat()}".encode()
            ).hexdigest()[:12]

            try:
                # Store bot code in plaintext for review
                code_file = os.path.join(self.review_directory, f"{submission_id}.py")
                with open(code_file, 'w', encoding='utf-8') as f:
                    f.write(bot_code)

                # Create submission record
                self.submissions["submissions"][submission_id] = {
                    "bot_name": bot_name,
                    "submitter_username": submitter_username,
                    "submission_date": datetime.now().isoformat(),
                    "status": BotStatus.PENDING_REVIEW.value,
                    "code_file": code_file,
                    "review_notes": [],
                    "revision_count": 0
                }
                self._save_submissions()

                self.logger.info(f"Bot submission successful: {bot_name} (ID: {submission_id})")

                result = {
                    "success": True,
                    "submission_id": submission_id,
                    "message": f"Bot '{bot_name}' submitted for review.",
                    "status": BotStatus.PENDING_REVIEW.value
                }

            except Exception as e:
                self.logger.error(f"Failed to submit bot {bot_name}: {str(e)}")
                return {
                    "success": False,
                    "error": f"Submission failed: {str(e)}"
                }

        return result
    
    def get_pending_submissions(self) -> List[Dict]:
        """Get all submissions pending review (ADMIN ONLY)"""
        self.logger.debug("Retrieving pending submissions")
        
        with self._lock:
            # Reload to get latest data
            self.submissions = self._load_submissions()
            
            pending = []
            
            for sub_id, sub in self.submissions["submissions"].items():
                if sub["status"] == BotStatus.PENDING_REVIEW.value:
                    try:
                        # Read the code for review
                        with open(sub["code_file"], 'r', encoding='utf-8') as f:
                            code = f.read()
                        
                        # Run automated safety checks
                        safety_check = self._run_automated_checks(code)
                        
                        pending.append({
                            "submission_id": sub_id,
                            "bot_name": sub["bot_name"],
                            "submitter_username": sub.get("submitter_username", sub.get("submitter_email", "unknown")),
                            "submission_date": sub["submission_date"],
                            "code": code,
                            "code_lines": len(code.split('\n')),
                            "safety_check": safety_check,
                            "review_notes": sub["review_notes"]
                        })
                    except FileNotFoundError:
                        self.logger.warning(f"Code file not found for submission {sub_id}")
                    except Exception as e:
                        self.logger.error(f"Error loading submission {sub_id}: {str(e)}")
            
            # Sort by submission date (oldest first)
            pending.sort(key=lambda x: x["submission_date"])
            self.logger.info(f"Retrieved {len(pending)} pending submissions")
            return pending
    
    def get_all_submissions_admin(self) -> List[Dict]:
        """Get ALL submissions regardless of status (ADMIN ONLY) - NEW METHOD"""
        self.logger.debug("Retrieving all submissions for admin")
        
        with self._lock:
            # Reload to get latest data
            self.submissions = self._load_submissions()
            
            all_subs = []
            
            for sub_id, sub in self.submissions["submissions"].items():
                try:
                    code = None
                    # Only load code if file still exists (pending/revision)
                    if os.path.exists(sub["code_file"]):
                        with open(sub["code_file"], 'r', encoding='utf-8') as f:
                            code = f.read()
                    
                    # Run safety checks if code available
                    safety_check = None
                    if code:
                        safety_check = self._run_automated_checks(code)
                    
                    all_subs.append({
                        "submission_id": sub_id,
                        "bot_name": sub["bot_name"],
                        "submitter_username": sub.get("submitter_username", sub.get("submitter_email", "unknown")),
                        "submission_date": sub["submission_date"],
                        "status": sub["status"],
                        "code": code,
                        "code_lines": len(code.split('\n')) if code else 0,
                        "safety_check": safety_check,
                        "review_notes": sub.get("review_notes", []),
                        "approval_date": sub.get("approval_date"),
                        "rejection_date": sub.get("rejection_date")
                    })
                except Exception as e:
                    self.logger.error(f"Error loading submission {sub_id}: {str(e)}")
            
            # Sort by submission date (newest first for admin)
            all_subs.sort(key=lambda x: x["submission_date"], reverse=True)
            self.logger.info(f"Retrieved {len(all_subs)} total submissions")
            return all_subs
    
    def _run_automated_checks(self, code: str) -> Dict:
        """Run automated safety checks on bot code"""
        flags = []
        severity = "safe"
        
        # Dangerous imports/functions
        dangerous_patterns = {
            'os.system': 'Command execution (os.system)',
            'subprocess': 'Subprocess execution',
            'eval(': 'Dynamic code evaluation (eval)',
            'exec(': 'Dynamic code execution (exec)',
            '__import__': 'Dynamic imports',
            'open(': 'File operations',
            'requests.': 'Network requests',
            'urllib': 'Network requests',
            'socket': 'Network sockets',
            'pickle': 'Pickle serialization (potential RCE)',
            'os.remove': 'File deletion',
            'os.rmdir': 'Directory deletion',
            'shutil': 'File system operations',
            'sys.exit': 'Program termination',
            '__builtins__': 'Built-ins manipulation',
            'globals()': 'Global scope access',
            'locals()': 'Local scope access',
            'compile(': 'Code compilation',
        }
        
        for pattern, description in dangerous_patterns.items():
            if pattern in code:
                flag_severity = "high" if pattern in ['os.system', 'subprocess', 'eval(', 'exec('] else "medium"
                flags.append({
                    "pattern": pattern,
                    "description": description,
                    "severity": flag_severity
                })
                if flag_severity == "high":
                    severity = "dangerous"
                elif severity != "dangerous":
                    severity = "suspicious"
        
        # Check for correct base class
        if 'PokerBotAPI' not in code:
            flags.append({
                "pattern": "Missing PokerBotAPI",
                "description": "Bot doesn't inherit from PokerBotAPI",
                "severity": "high"
            })
            severity = "invalid"
        
        # Check for required methods
        if 'def get_action' not in code:
            flags.append({
                "pattern": "Missing get_action",
                "description": "Required method get_action not found",
                "severity": "high"
            })
            severity = "invalid"
        
        if 'def hand_complete' not in code:
            flags.append({
                "pattern": "Missing hand_complete",
                "description": "Required method hand_complete not found",
                "severity": "high"
            })
            severity = "invalid"
        
        # Check for excessive complexity
        lines = code.split('\n')
        if len(lines) > 500:
            flags.append({
                "pattern": "Large file",
                "description": f"Bot has {len(lines)} lines (unusually large)",
                "severity": "low"
            })
        
        return {
            "severity": severity,
            "flags": flags,
            "total_flags": len(flags),
            "is_safe": severity == "safe"
        }
    
    def _validate_bot_code(self, code: str, bot_name: str) -> Dict:
        """Validate bot code by trying to load it - FIXED IMPORT PATH"""
        try:
            # Add backend to path if not already there
            backend_path = os.path.abspath('backend')
            if backend_path not in sys.path:
                sys.path.insert(0, backend_path)
            
            # Also add current directory
            current_path = os.path.abspath('.')
            if current_path not in sys.path:
                sys.path.insert(0, current_path)
            
            # Try to compile the code
            compile(code, bot_name, 'exec')
            
            # Try to load it as a module
            module = types.ModuleType(bot_name)
            
            # Execute the code in the module's namespace
            exec(code, module.__dict__)
            
            # Import PokerBotAPI to check inheritance
            try:
                from bot_api import PokerBotAPI
            except ImportError:
                # Try alternative import
                from backend.bot_api import PokerBotAPI
            
            # Find PokerBotAPI subclass
            bot_class = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and 
                    issubclass(attr, PokerBotAPI) and 
                    attr != PokerBotAPI):
                    bot_class = attr
                    break
            
            if bot_class is None:
                return {
                    "valid": False,
                    "error": "No valid PokerBotAPI subclass found. Make sure your bot class inherits from PokerBotAPI."
                }
            
            # Try to instantiate it
            try:
                test_bot = bot_class(bot_name)
            except Exception as e:
                return {
                    "valid": False,
                    "error": f"Bot failed to instantiate: {str(e)}"
                }
            
            # Check required methods
            if not hasattr(test_bot, 'get_action'):
                return {
                    "valid": False,
                    "error": "Bot missing get_action method"
                }
            
            if not hasattr(test_bot, 'hand_complete'):
                return {
                    "valid": False,
                    "error": "Bot missing hand_complete method"
                }
            
            return {"valid": True}
            
        except SyntaxError as e:
            return {
                "valid": False,
                "error": f"Syntax error on line {e.lineno}: {str(e)}"
            }
        except Exception as e:
            return {
                "valid": False,
                "error": f"Validation error: {str(e)}"
            }
    
    def approve_bot(self, submission_id: str, admin_notes: str = "") -> Dict:
        """Approve a bot submission (ADMIN ONLY) - FIXED VALIDATION"""
        self.logger.info(f"Approving bot submission: {submission_id}")
        
        with self._lock:
            # Reload to get latest data
            self.submissions = self._load_submissions()
            
            if submission_id not in self.submissions["submissions"]:
                self.logger.warning(f"Submission not found: {submission_id}")
                return {"success": False, "error": "Submission not found"}
            
            submission = self.submissions["submissions"][submission_id]
            
            try:
                # Read the reviewed code
                with open(submission["code_file"], 'r', encoding='utf-8') as f:
                    bot_code = f.read()
                
                # VALIDATE CODE BEFORE APPROVING
                validation = self._validate_bot_code(bot_code, submission["bot_name"])
                if not validation["valid"]:
                    self.logger.error(f"Bot validation failed: {validation['error']}")
                    return {
                        "success": False,
                        "error": f"Bot validation failed: {validation['error']}"
                    }
                
                # Use MASTER_PASSWORD for encryption (same key used to decrypt during tournaments)
                master_password = os.environ.get('MASTER_PASSWORD')
                if not master_password:
                    return {"success": False, "error": "MASTER_PASSWORD not configured on server"}

                # Encrypt and store using the secure storage system
                from secure_bot_storage import SecureBotStorage
                storage = SecureBotStorage(self.approved_directory)

                result = storage.upload_bot(
                    submission["bot_name"],
                    bot_code,
                    master_password
                )
                
                if not result["success"]:
                    self.logger.error(f"Failed to upload bot to storage: {result.get('error')}")
                    return result
                
                # Update submission status
                submission["status"] = BotStatus.APPROVED.value
                submission["approval_date"] = datetime.now().isoformat()
                submission["admin_notes"] = admin_notes
                submission["review_notes"].append({
                    "date": datetime.now().isoformat(),
                    "action": "approved",
                    "notes": admin_notes
                })
                
                # Move to approved bots
                self.submissions["approved_bots"][submission["bot_name"]] = {
                    "submission_id": submission_id,
                    "submitter_username": submission.get("submitter_username", submission.get("submitter_email", "unknown")),
                    "approval_date": submission["approval_date"]
                }
                
                self._save_submissions()
                
                # Clean up review files
                self._cleanup_submission_files(submission_id)
                
                self.logger.info(f"Bot approved successfully: {submission['bot_name']}")
                
                result = {
                    "success": True,
                    "message": f"Bot '{submission['bot_name']}' approved and activated"
                }
                
            except Exception as e:
                self.logger.error(f"Error approving bot {submission_id}: {str(e)}")
                import traceback
                self.logger.error(traceback.format_exc())
                return {
                    "success": False,
                    "error": f"Approval failed: {str(e)}"
                }
        
        return result
    
    def reject_bot(self, submission_id: str, reason: str) -> Dict:
        """Reject a bot submission (ADMIN ONLY)"""
        self.logger.info(f"Rejecting bot submission: {submission_id}")
        
        with self._lock:
            self.submissions = self._load_submissions()
            
            if submission_id not in self.submissions["submissions"]:
                self.logger.warning(f"Submission not found: {submission_id}")
                return {"success": False, "error": "Submission not found"}
            
            submission = self.submissions["submissions"][submission_id]
            
            try:
                submission["status"] = BotStatus.REJECTED.value
                submission["rejection_date"] = datetime.now().isoformat()
                submission["rejection_reason"] = reason
                submission["review_notes"].append({
                    "date": datetime.now().isoformat(),
                    "action": "rejected",
                    "notes": reason
                })
                
                self._save_submissions()
                
                # Clean up files
                self._cleanup_submission_files(submission_id)
                
                self.logger.info(f"Bot rejected: {submission['bot_name']}")
                
            except Exception as e:
                self.logger.error(f"Error rejecting bot {submission_id}: {str(e)}")
                return {
                    "success": False,
                    "error": f"Rejection failed: {str(e)}"
                }
        
        return {
            "success": True,
            "message": "Bot rejected"
        }
    
    def request_revision(self, submission_id: str, feedback: str) -> Dict:
        """Request revisions to a bot submission (ADMIN ONLY)"""
        self.logger.info(f"Requesting revision for submission: {submission_id}")
        
        with self._lock:
            self.submissions = self._load_submissions()
            
            if submission_id not in self.submissions["submissions"]:
                self.logger.warning(f"Submission not found: {submission_id}")
                return {"success": False, "error": "Submission not found"}
            
            submission = self.submissions["submissions"][submission_id]
            
            try:
                submission["status"] = BotStatus.REVISION_REQUESTED.value
                submission["revision_count"] += 1
                submission["review_notes"].append({
                    "date": datetime.now().isoformat(),
                    "action": "revision_requested",
                    "notes": feedback
                })
                
                self._save_submissions()
                
                self.logger.info(f"Revision requested for: {submission['bot_name']}")
                
            except Exception as e:
                self.logger.error(f"Error requesting revision for {submission_id}: {str(e)}")
                return {
                    "success": False,
                    "error": f"Request failed: {str(e)}"
                }
        
        return {
            "success": True,
            "message": "Revision requested"
        }
    
    def resubmit_bot(self, submission_id: str, new_code: str,
                     submitter_username: str) -> Dict:
        """User resubmits after revision request or updates an approved bot"""
        self.logger.info(f"Bot resubmission: {submission_id}")

        with self._lock:
            self.submissions = self._load_submissions()

            if submission_id not in self.submissions["submissions"]:
                return {"success": False, "error": "Submission not found"}

            submission = self.submissions["submissions"][submission_id]

            # Verify ownership
            if submission.get("submitter_username", submission.get("submitter_email")) != submitter_username:
                return {"success": False, "error": "Unauthorized"}

            # Allow resubmission from revision_requested or approved status
            allowed = [BotStatus.REVISION_REQUESTED.value, BotStatus.APPROVED.value]
            if submission["status"] not in allowed:
                return {"success": False, "error": "This submission cannot be updated right now"}

            try:
                # Write updated code
                code_file = submission.get("code_file") or os.path.join(self.review_directory, f"{submission_id}.py")
                with open(code_file, 'w', encoding='utf-8') as f:
                    f.write(new_code)
                submission["code_file"] = code_file

                # Reset to pending review
                submission["status"] = BotStatus.PENDING_REVIEW.value
                submission["resubmission_date"] = datetime.now().isoformat()
                submission["review_notes"].append({
                    "date": datetime.now().isoformat(),
                    "action": "resubmitted",
                    "notes": "Code updated by submitter"
                })

                self._save_submissions()
                self.logger.info(f"Bot resubmitted successfully: {submission['bot_name']}")

            except Exception as e:
                self.logger.error(f"Error resubmitting bot {submission_id}: {str(e)}")
                return {"success": False, "error": f"Resubmission failed: {str(e)}"}

        return {"success": True, "message": "Bot resubmitted for review"}

    def withdraw_submission(self, submission_id: str, submitter_username: str) -> Dict:
        """User withdraws a pending submission"""
        with self._lock:
            self.submissions = self._load_submissions()

            if submission_id not in self.submissions["submissions"]:
                return {"success": False, "error": "Submission not found"}

            submission = self.submissions["submissions"][submission_id]

            # Verify ownership
            if submission.get("submitter_username", submission.get("submitter_email")) != submitter_username:
                return {"success": False, "error": "Unauthorized"}

            # Only allow withdrawing non-approved submissions
            if submission["status"] == BotStatus.APPROVED.value:
                return {"success": False, "error": "Cannot withdraw an approved bot. Use 'update' instead."}

            # Clean up files and remove the submission
            self._cleanup_submission_files(submission_id)
            del self.submissions["submissions"][submission_id]
            self._save_submissions()

            self.logger.info(f"Submission withdrawn: {submission['bot_name']} by {submitter_username}")

        return {"success": True, "message": "Submission withdrawn"}
    
    def get_user_submissions(self, username: str) -> List[Dict]:
        """Get all submissions for a user by username"""
        with self._lock:
            self.submissions = self._load_submissions()

            user_subs = []

            for sub_id, sub in self.submissions["submissions"].items():
                sub_owner = sub.get("submitter_username", sub.get("submitter_email"))
                if sub_owner == username:
                    user_subs.append({
                        "submission_id": sub_id,
                        "bot_name": sub["bot_name"],
                        "status": sub["status"],
                        "submission_date": sub["submission_date"],
                        "review_notes": sub.get("review_notes", []),
                        "revision_count": sub.get("revision_count", 0)
                    })

            return user_subs
    
    def _cleanup_submission_files(self, submission_id: str):
        """Remove plaintext code files after approval/rejection"""
        files = [
            os.path.join(self.review_directory, f"{submission_id}.py"),
            os.path.join(self.review_directory, f"{submission_id}.pwd")
        ]

        for file_path in files:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    self.logger.debug(f"Cleaned up file: {file_path}")
                except Exception as e:
                    self.logger.error(f"Failed to remove file {file_path}: {str(e)}")