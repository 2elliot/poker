"""
Secure Bot Storage and Management System
Keeps bot code encrypted while allowing tournaments to run
"""
import os
import sys
import json
import hashlib
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend
import base64
from typing import Optional, List, Dict
import types

from backend.bot_api import PokerBotAPI
import backend.bot_api
import backend.engine.poker_game
import backend.engine.cards

# Register module aliases so bot code like "from bot_api import PokerBotAPI"
# resolves to the same class objects as "from backend.bot_api import PokerBotAPI"
sys.modules.setdefault('bot_api', backend.bot_api)
sys.modules.setdefault('engine', backend.engine)
sys.modules.setdefault('engine.poker_game', backend.engine.poker_game)
sys.modules.setdefault('engine.cards', backend.engine.cards)


class SecureBotStorage:
    """Manages encrypted bot storage and execution"""
    
    def __init__(self, storage_directory: str = "encrypted_bots"):
        self.storage_directory = storage_directory
        os.makedirs(storage_directory, exist_ok=True)
        
        # Metadata file tracks bot names without exposing code
        self.metadata_file = os.path.join(storage_directory, "metadata.json")
        self.metadata = self._load_metadata()
    
    def _load_metadata(self) -> Dict:
        """Load bot metadata (names, upload dates, etc.)"""
        if os.path.exists(self.metadata_file):
            with open(self.metadata_file, 'r') as f:
                return json.load(f)
        return {"bots": {}}
    
    def _save_metadata(self):
        """Save bot metadata"""
        with open(self.metadata_file, 'w') as f:
            json.dump(self.metadata, f, indent=2)
    
    def _generate_encryption_key(self, password: str, salt: bytes) -> bytes:
        """Generate encryption key from password"""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return key
    
    def upload_bot(self, bot_name: str, bot_code: str, owner_password: str) -> Dict:
        """
        Upload and encrypt a bot
        
        Args:
            bot_name: Unique name for the bot
            bot_code: Python code for the bot
            owner_password: Password to encrypt/decrypt this bot
            
        Returns:
            dict with status and bot_id
        """
        if bot_name in self.metadata["bots"]:
            return {"success": False, "error": "Bot name already exists"}
        
        # Validate bot code before storing
        validation_result = self._validate_bot_code(bot_code, bot_name)
        if not validation_result["valid"]:
            return {"success": False, "error": validation_result["error"]}
        
        # Generate unique salt for this bot
        salt = os.urandom(16)
        encryption_key = self._generate_encryption_key(owner_password, salt)
        
        # Encrypt the bot code
        f = Fernet(encryption_key)
        encrypted_code = f.encrypt(bot_code.encode())
        
        # Generate bot ID (hash of encrypted code for verification)
        bot_id = hashlib.sha256(encrypted_code).hexdigest()[:16]
        
        # Store encrypted bot
        bot_file = os.path.join(self.storage_directory, f"{bot_id}.enc")
        salt_file = os.path.join(self.storage_directory, f"{bot_id}.salt")
        
        with open(bot_file, 'wb') as f:
            f.write(encrypted_code)
        
        with open(salt_file, 'wb') as f:
            f.write(salt)
        
        # Update metadata
        self.metadata["bots"][bot_name] = {
            "bot_id": bot_id,
            "upload_date": str(os.path.getmtime(bot_file)),
            "wins": 0,
            "total_games": 0
        }
        self._save_metadata()
        
        return {
            "success": True,
            "bot_id": bot_id,
            "message": f"Bot '{bot_name}' uploaded successfully"
        }
    
    def update_bot(self, bot_name: str, new_code: str, owner_password: str) -> Dict:
        """Update an existing bot (requires correct password)"""
        if bot_name not in self.metadata["bots"]:
            return {"success": False, "error": "Bot not found"}
        
        # Try to load existing bot with password (validates password)
        bot_instance = self.load_bot(bot_name, owner_password)
        if bot_instance is None:
            return {"success": False, "error": "Invalid password"}
        
        # Validate new code
        validation_result = self._validate_bot_code(new_code, bot_name)
        if not validation_result["valid"]:
            return {"success": False, "error": validation_result["error"]}
        
        # Delete old bot files
        old_bot_id = self.metadata["bots"][bot_name]["bot_id"]
        old_files = [
            os.path.join(self.storage_directory, f"{old_bot_id}.enc"),
            os.path.join(self.storage_directory, f"{old_bot_id}.salt")
        ]
        for f in old_files:
            if os.path.exists(f):
                os.remove(f)
        
        # Remove from metadata (will be re-added by upload)
        stats = self.metadata["bots"][bot_name].copy()
        del self.metadata["bots"][bot_name]
        
        # Upload new version
        result = self.upload_bot(bot_name, new_code, owner_password)
        
        # Preserve statistics
        if result["success"]:
            self.metadata["bots"][bot_name]["wins"] = stats.get("wins", 0)
            self.metadata["bots"][bot_name]["total_games"] = stats.get("total_games", 0)
            self._save_metadata()
        
        return result
    
    def load_bot(self, bot_name: str, password: str) -> Optional[PokerBotAPI]:
        """
        Load and decrypt a bot for execution
        Bot code is never written to disk in plaintext
        """
        if bot_name not in self.metadata["bots"]:
            return None
        
        bot_id = self.metadata["bots"][bot_name]["bot_id"]
        bot_file = os.path.join(self.storage_directory, f"{bot_id}.enc")
        salt_file = os.path.join(self.storage_directory, f"{bot_id}.salt")
        
        if not os.path.exists(bot_file) or not os.path.exists(salt_file):
            return None
        
        # Read salt and encrypted code
        with open(salt_file, 'rb') as f:
            salt = f.read()
        
        with open(bot_file, 'rb') as f:
            encrypted_code = f.read()
        
        # Generate decryption key
        encryption_key = self._generate_encryption_key(password, salt)
        
        try:
            # Decrypt
            f = Fernet(encryption_key)
            bot_code = f.decrypt(encrypted_code).decode()
            
            # Load bot from string (never touches disk)
            return self._load_bot_from_string(bot_code, bot_name)
        except Exception as e:
            # Decryption failed (wrong password) or invalid code
            return None
    
    def get_bot_code(self, bot_name: str, password: str) -> Optional[str]:
        """Decrypt and return bot source code as a string."""
        if bot_name not in self.metadata["bots"]:
            return None

        bot_id = self.metadata["bots"][bot_name]["bot_id"]
        bot_file = os.path.join(self.storage_directory, f"{bot_id}.enc")
        salt_file = os.path.join(self.storage_directory, f"{bot_id}.salt")

        if not os.path.exists(bot_file) or not os.path.exists(salt_file):
            return None

        with open(salt_file, 'rb') as f:
            salt = f.read()
        with open(bot_file, 'rb') as f:
            encrypted_code = f.read()

        try:
            encryption_key = self._generate_encryption_key(password, salt)
            f = Fernet(encryption_key)
            return f.decrypt(encrypted_code).decode()
        except Exception:
            return None

    def _load_bot_from_string(self, code: str, bot_name: str) -> Optional[PokerBotAPI]:
        """Load bot from code string without writing to disk"""
        try:
            # Create a module from the code
            module = types.ModuleType(bot_name)
            exec(code, module.__dict__)
            
            # Find PokerBotAPI subclass
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and 
                    issubclass(attr, PokerBotAPI) and 
                    attr != PokerBotAPI):
                    return attr(bot_name)
            
            return None
        except Exception as e:
            return None
    
    def _validate_bot_code(self, code: str, bot_name: str) -> Dict:
        """Validate bot code before storing"""
        try:
            # Try to compile the code
            compile(code, bot_name, 'exec')
            
            # Try to load it
            test_bot = self._load_bot_from_string(code, bot_name)
            if test_bot is None:
                return {
                    "valid": False,
                    "error": "No valid PokerBotAPI subclass found"
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
                "error": f"Syntax error: {str(e)}"
            }
        except Exception as e:
            return {
                "valid": False,
                "error": f"Validation error: {str(e)}"
            }
    
    def list_bots(self) -> List[Dict]:
        """List all available bots (without exposing code)"""
        self.metadata = self._load_metadata()
        bots = []
        for bot_name, info in self.metadata["bots"].items():
            bots.append({
                "name": bot_name,
                "bot_id": info["bot_id"],
                "wins": info.get("wins", 0),
                "total_games": info.get("total_games", 0),
                "win_rate": (info.get("wins", 0) / info.get("total_games", 1) * 100) 
                           if info.get("total_games", 0) > 0 else 0
            })
        return bots
    
    def delete_bot(self, bot_name: str, owner_password: str) -> Dict:
        """Delete a bot (requires correct password)"""
        if bot_name not in self.metadata["bots"]:
            return {"success": False, "error": "Bot not found"}
        
        # Verify password by trying to load
        bot_instance = self.load_bot(bot_name, owner_password)
        if bot_instance is None:
            return {"success": False, "error": "Invalid password"}
        
        # Delete files
        bot_id = self.metadata["bots"][bot_name]["bot_id"]
        files_to_delete = [
            os.path.join(self.storage_directory, f"{bot_id}.enc"),
            os.path.join(self.storage_directory, f"{bot_id}.salt")
        ]
        
        for file_path in files_to_delete:
            if os.path.exists(file_path):
                os.remove(file_path)
        
        # Remove from metadata
        del self.metadata["bots"][bot_name]
        self._save_metadata()
        
        return {
            "success": True,
            "message": f"Bot '{bot_name}' deleted successfully"
        }
    
    def update_bot_stats(self, bot_name: str, won: bool):
        """Update win/loss statistics for a bot"""
        if bot_name in self.metadata["bots"]:
            self.metadata["bots"][bot_name]["total_games"] = \
                self.metadata["bots"][bot_name].get("total_games", 0) + 1
            if won:
                self.metadata["bots"][bot_name]["wins"] = \
                    self.metadata["bots"][bot_name].get("wins", 0) + 1
            self._save_metadata()


# Usage example:
if __name__ == "__main__":
    storage = SecureBotStorage()
    
    # Upload a bot
    sample_bot = '''
from bot_api import PokerBotAPI, PlayerAction
from engine.poker_game import GameState
from engine.cards import Card
from typing import List, Dict, Any

class MyBot(PokerBotAPI):
    def get_action(self, game_state: GameState, hole_cards: List[Card], 
                   legal_actions: List[PlayerAction], min_bet: int, max_bet: int):
        return PlayerAction.CALL, 0
    
    def hand_complete(self, game_state: GameState, hand_result: Dict[str, Any]):
        pass
'''
    
    result = storage.upload_bot("TestBot", sample_bot, "my_password_123")
    print(result)
    
    # List all bots
    print("\nAvailable bots:")
    for bot in storage.list_bots():
        print(f"  - {bot['name']} (ID: {bot['bot_id']})")
    
    # Load a bot for tournament
    bot = storage.load_bot("TestBot", "my_password_123")
    if bot:
        print(f"\nSuccessfully loaded: {bot.name}")