"""
User Authentication System
Handles registration and login for regular users (bot submitters)
"""
import hashlib
import secrets
import json
import os
from datetime import datetime
from flask_login import UserMixin


class User(UserMixin):
    """User class for Flask-Login"""
    def __init__(self, user_id, username, is_admin=False):
        self.id = user_id
        self.username = username
        self.is_admin = is_admin


class UserAuthSystem:
    """Manages user registration and authentication"""

    def __init__(self, users_file: str = "users.json"):
        self.users_file = users_file
        if not os.path.exists(users_file):
            self._init_file()

    def _init_file(self):
        with open(self.users_file, 'w') as f:
            json.dump({"users": {}}, f, indent=2)

    def _load_data(self) -> dict:
        with open(self.users_file, 'r') as f:
            return json.load(f)

    def _save_data(self, data: dict):
        with open(self.users_file, 'w') as f:
            json.dump(data, f, indent=2)

    def _hash_password(self, password: str) -> str:
        salt = secrets.token_hex(16)
        pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
        return f"{salt}${pwd_hash.hex()}"

    def _verify_password(self, password: str, password_hash: str) -> bool:
        try:
            salt, hash_value = password_hash.split('$')
            pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
            return pwd_hash.hex() == hash_value
        except Exception:
            return False

    def register(self, username: str, password: str) -> dict:
        """
        Register a new user.
        Returns: {"success": bool, "error": str or None}
        """
        username = username.strip()

        # Validate username
        if not username or len(username) < 3:
            return {"success": False, "error": "Username must be at least 3 characters"}
        if len(username) > 30:
            return {"success": False, "error": "Username must be 30 characters or less"}
        if not username.replace('_', '').replace('-', '').isalnum():
            return {"success": False, "error": "Username can only contain letters, numbers, hyphens, and underscores"}

        # Validate password
        if len(password) < 8:
            return {"success": False, "error": "Password must be at least 8 characters"}

        data = self._load_data()

        # Check if username taken (case-insensitive)
        if username.lower() in {u.lower() for u in data["users"]}:
            return {"success": False, "error": "Username already taken"}

        # Create user
        data["users"][username] = {
            "password_hash": self._hash_password(password),
            "created_at": datetime.now().isoformat(),
            "last_login": None
        }
        self._save_data(data)

        return {"success": True, "user_id": username}

    def authenticate(self, username: str, password: str) -> dict:
        """
        Authenticate a user.
        Returns: {"success": bool, "user": User or None, "error": str or None}
        """
        data = self._load_data()

        # Find user (case-insensitive lookup, but store exact case)
        actual_username = None
        for u in data["users"]:
            if u.lower() == username.lower():
                actual_username = u
                break

        if not actual_username:
            return {"success": False, "user": None, "error": "Invalid username or password"}

        user_data = data["users"][actual_username]

        if not self._verify_password(password, user_data["password_hash"]):
            return {"success": False, "user": None, "error": "Invalid username or password"}

        # Update last login
        user_data["last_login"] = datetime.now().isoformat()
        self._save_data(data)

        return {
            "success": True,
            "user": User(f"user:{actual_username}", actual_username, is_admin=False),
            "error": None
        }

    def get_user(self, user_id: str):
        """Load a user by their ID (for Flask-Login user_loader)"""
        if not user_id.startswith("user:"):
            return None
        username = user_id[5:]  # Strip "user:" prefix
        data = self._load_data()
        if username in data["users"]:
            return User(user_id, username, is_admin=False)
        return None

    def user_exists(self, username: str) -> bool:
        data = self._load_data()
        return username.lower() in {u.lower() for u in data["users"]}
