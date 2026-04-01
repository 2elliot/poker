"""
Secure Admin Authentication System
Multiple layers of security for admin panel access
"""
import hashlib
import secrets
import time
from flask_login import UserMixin
from datetime import datetime
import json
import os


class User(UserMixin):
    """User class for admin authentication"""
    def __init__(self, user_id, username, is_admin=False):
        self.id = user_id
        self.username = username
        self.is_admin = is_admin


class AdminAuthSystem:
    """Manages admin authentication with multiple security layers"""

    def __init__(self, auth_file: str = "admin_auth.json"):
        self.auth_file = auth_file
        self.rate_limit_storage = {}  # IP -> [timestamps]
        self.failed_attempts = {}  # IP -> count
        self.lockout_until = {}  # IP -> timestamp

        # Initialize auth file if doesn't exist
        if not os.path.exists(auth_file):
            self._create_default_admin()

    def _create_default_admin(self):
        """Create default admin account on first run"""
        default_password = os.environ.get('ADMIN_PASSWORD', 'admin')

        admin_data = {
            "admins": {
                "admin": {
                    "password_hash": self._hash_password(default_password),
                    "created_at": datetime.now().isoformat(),
                    "last_login": None,
                    "is_active": True
                }
            },
            "sessions": {},
            "audit_log": []
        }

        with open(self.auth_file, 'w') as f:
            json.dump(admin_data, f, indent=2)

        print("=" * 60)
        print("ADMIN ACCOUNT CREATED")
        print("=" * 60)
        print(f"Username: admin")
        print(f"Password: {default_password}")
        print("\nSAVE THIS PASSWORD - IT WILL NOT BE SHOWN AGAIN!")
        print("=" * 60)

    def _hash_password(self, password: str) -> str:
        """Hash password with salt"""
        salt = secrets.token_hex(16)
        pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
        return f"{salt}${pwd_hash.hex()}"

    def _verify_password(self, password: str, password_hash: str) -> bool:
        """Verify password against hash"""
        try:
            salt, hash_value = password_hash.split('$')
            pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
            return pwd_hash.hex() == hash_value
        except Exception:
            return False

    def _load_auth_data(self) -> dict:
        """Load authentication data"""
        with open(self.auth_file, 'r') as f:
            return json.load(f)

    def _save_auth_data(self, data: dict):
        """Save authentication data"""
        with open(self.auth_file, 'w') as f:
            json.dump(data, f, indent=2)

    def _log_audit_event(self, event_type: str, username: str, ip: str, details: str):
        """Log security events"""
        data = self._load_auth_data()
        data["audit_log"].append({
            "timestamp": datetime.now().isoformat(),
            "event": event_type,
            "username": username,
            "ip": ip,
            "details": details
        })
        # Keep only last 1000 events
        data["audit_log"] = data["audit_log"][-1000:]
        self._save_auth_data(data)

    def check_rate_limit(self, ip: str, max_requests: int = 5, window_seconds: int = 60) -> bool:
        """
        Rate limiting to prevent brute force attacks
        Returns True if under limit, False if over
        """
        now = time.time()

        # Clean old timestamps
        if ip in self.rate_limit_storage:
            self.rate_limit_storage[ip] = [
                ts for ts in self.rate_limit_storage[ip]
                if now - ts < window_seconds
            ]
        else:
            self.rate_limit_storage[ip] = []

        # Check if over limit
        if len(self.rate_limit_storage[ip]) >= max_requests:
            return False

        # Add current request
        self.rate_limit_storage[ip].append(now)
        return True

    def is_locked_out(self, ip: str) -> bool:
        """Check if IP is temporarily locked out"""
        if ip in self.lockout_until:
            if time.time() < self.lockout_until[ip]:
                return True
            else:
                # Lockout expired
                del self.lockout_until[ip]
                self.failed_attempts[ip] = 0
        return False

    def record_failed_attempt(self, ip: str):
        """Record failed login attempt"""
        self.failed_attempts[ip] = self.failed_attempts.get(ip, 0) + 1

        # Lock out after 5 failed attempts for 15 minutes
        if self.failed_attempts[ip] >= 5:
            self.lockout_until[ip] = time.time() + (15 * 60)

    def reset_failed_attempts(self, ip: str):
        """Reset failed attempts after successful login"""
        if ip in self.failed_attempts:
            del self.failed_attempts[ip]

    def authenticate(self, username: str, password: str, ip: str) -> dict:
        """
        Authenticate admin user
        Returns: {"success": bool, "user": User or None, "error": str}
        """
        # Load admin data
        data = self._load_auth_data()

        # Check if user exists
        if username not in data["admins"]:
            self._log_audit_event("LOGIN_FAIL", username, ip, "Invalid username")
            return {"success": False, "error": "Invalid credentials"}

        admin = data["admins"][username]

        # Check if account is active
        if not admin.get("is_active", True):
            self._log_audit_event("LOGIN_FAIL", username, ip, "Account disabled")
            return {"success": False, "error": "Account is disabled"}

        # Verify password
        if not self._verify_password(password, admin["password_hash"]):
            self._log_audit_event("LOGIN_FAIL", username, ip, "Invalid password")
            return {"success": False, "error": "Invalid credentials"}

        # Success - update last login
        admin["last_login"] = datetime.now().isoformat()
        self._save_auth_data(data)

        self._log_audit_event("LOGIN_SUCCESS", username, ip, "Successful login")

        return {
            "success": True,
            "user": User(username, username, is_admin=True),
            "error": None
        }

    def change_password(self, username: str, old_password: str, new_password: str) -> dict:
        """Change admin password"""
        data = self._load_auth_data()

        if username not in data["admins"]:
            return {"success": False, "error": "User not found"}

        admin = data["admins"][username]

        # Verify old password
        if not self._verify_password(old_password, admin["password_hash"]):
            return {"success": False, "error": "Invalid current password"}

        # Validate new password strength
        if len(new_password) < 12:
            return {"success": False, "error": "Password must be at least 12 characters"}

        # Update password
        admin["password_hash"] = self._hash_password(new_password)
        admin["password_changed_at"] = datetime.now().isoformat()
        self._save_auth_data(data)

        self._log_audit_event("PASSWORD_CHANGE", username, "system", "Password changed")

        return {"success": True, "message": "Password changed successfully"}

    def create_admin(self, username: str, password: str, creator: str) -> dict:
        """Create new admin account"""
        data = self._load_auth_data()

        if username in data["admins"]:
            return {"success": False, "error": "Username already exists"}

        if len(password) < 12:
            return {"success": False, "error": "Password must be at least 12 characters"}

        data["admins"][username] = {
            "password_hash": self._hash_password(password),
            "created_at": datetime.now().isoformat(),
            "created_by": creator,
            "last_login": None,
            "is_active": True
        }
        self._save_auth_data(data)

        self._log_audit_event("ADMIN_CREATED", username, "system", f"Created by {creator}")

        return {"success": True, "message": f"Admin account '{username}' created"}

    def get_audit_log(self, limit: int = 100) -> list:
        """Get recent audit log entries"""
        data = self._load_auth_data()
        return data["audit_log"][-limit:]
