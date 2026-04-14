"""
Bot Management System with Timeout and Error Handling
Loads, manages, and executes student poker bots safely
"""
import importlib.util
import sys
import os
import signal
import time
import traceback
import threading
import multiprocessing
from typing import Dict, List, Optional, Any, Tuple
from contextlib import contextmanager
import logging

try:
    from backend.bot_api import PokerBotAPI
    from backend.engine.cards import Card
    from backend.engine.poker_game import GameState, PlayerAction
except ImportError:
    from bot_api import PokerBotAPI
    from engine.cards import Card
    from engine.poker_game import GameState, PlayerAction


class TimeoutException(Exception):
    """Exception raised when bot action times out"""
    pass


class BotError(Exception):
    """Exception raised when bot encounters an error"""
    pass


def timeout_handler(signum, frame):
    """Signal handler for timeout"""
    raise TimeoutException("Bot action timed out")


@contextmanager
def timeout_context(seconds: float):
    """Context manager for handling timeouts - Windows compatible"""
    import threading
    
    timer = None
    timed_out = [False]
    
    def timeout_handler():
        timed_out[0] = True
    
    try:
        timer = threading.Timer(seconds, timeout_handler)
        timer.start()
        yield
        if timed_out[0]:
            raise TimeoutException("Bot action timed out")
    finally:
        if timer:
            timer.cancel()


BOT_TURN_TIMEOUT = 0.05  # seconds (50ms) — max time a bot has to return an action


class BotWrapper:
    """Wrapper for a poker bot that handles execution and errors"""

    def __init__(self, name: str, bot_instance: PokerBotAPI, timeout: float = BOT_TURN_TIMEOUT):
        self.name = name
        self.bot = bot_instance
        self.timeout = timeout
        self.error_count = 0
        self.timeout_count = 0
        self.max_errors = 5
        self.max_timeouts = 3
        
        self.logger = logging.getLogger(f"bot_manager.{name}")
        
    def is_disqualified(self) -> bool:
        """Check if bot should be disqualified due to errors/timeouts"""
        return (self.error_count >= self.max_errors or 
                self.timeout_count >= self.max_timeouts)
    
    def get_action(self, game_state: GameState, hole_cards: List[Card], 
                   legal_actions: List[PlayerAction], min_bet: int, max_bet: int) -> Tuple[PlayerAction, int]:
        """
        Get action from bot with timeout and error handling
        """
        if self.is_disqualified():
            self.logger.warning(f"Bot {self.name} is disqualified, folding automatically")
            return PlayerAction.FOLD, 0
        
        try:
            with timeout_context(self.timeout):
                action, amount = self.bot.get_action(game_state, hole_cards, legal_actions, min_bet, max_bet)
                
                # Validate action
                if not isinstance(action, PlayerAction):
                    raise BotError(f"Invalid action type: {type(action)}")
                
                if not isinstance(amount, (int, float)):
                    raise BotError(f"Invalid amount type: {type(amount)}")
                
                amount = int(amount)
                
                # Ensure action is legal
                if action not in legal_actions:
                    self.logger.warning(f"Bot {self.name} attempted illegal action {action}, folding instead")
                    return PlayerAction.FOLD, 0
                
                # Validate amount for raises
                if action == PlayerAction.RAISE:
                    if amount < min_bet or amount > max_bet:
                        self.logger.warning(f"Bot {self.name} attempted invalid raise amount {amount}, folding instead")
                        return PlayerAction.FOLD, 0
                
                return action, amount
                
        except TimeoutException:
            self.timeout_count += 1
            self.logger.warning(f"Bot {self.name} timed out ({self.timeout_count}/{self.max_timeouts})")
            return PlayerAction.FOLD, 0
            
        except Exception as e:
            self.error_count += 1
            self.logger.error(f"Bot {self.name} error ({self.error_count}/{self.max_errors}): {str(e)}")
            self.logger.debug(traceback.format_exc())
            return PlayerAction.FOLD, 0
    
    def hand_complete(self, game_state: GameState, hand_result: Dict[str, Any]):
        """Notify bot of hand completion with error handling"""
        try:
            with timeout_context(self.timeout):
                self.bot.hand_complete(game_state, hand_result)
        except TimeoutException:
            self.timeout_count += 1
            self.logger.warning(f"Bot {self.name} timed out during hand_complete ({self.timeout_count}/{self.max_timeouts})")
        except Exception as e:
            self.error_count += 1
            self.logger.error(f"Bot {self.name} error in hand_complete ({self.error_count}/{self.max_errors}): {str(e)}")
    
    def tournament_start(self, players: List[str], starting_chips: int):
        """Notify bot of tournament start with error handling"""
        try:
            with timeout_context(self.timeout):
                self.bot.tournament_start(players, starting_chips)
        except TimeoutException:
            self.timeout_count += 1
            self.logger.warning(f"Bot {self.name} timed out during tournament_start ({self.timeout_count}/{self.max_timeouts})")
        except Exception as e:
            self.error_count += 1
            self.logger.error(f"Bot {self.name} error in tournament_start ({self.error_count}/{self.max_errors}): {str(e)}")
    
    def tournament_end(self, final_standings: List[Tuple[str, int, int]]):
        """Notify bot of tournament end with error handling"""
        try:
            with timeout_context(self.timeout):
                self.bot.tournament_end(final_standings)
        except TimeoutException:
            self.timeout_count += 1
            self.logger.warning(f"Bot {self.name} timed out during tournament_end ({self.timeout_count}/{self.max_timeouts})")
        except Exception as e:
            self.error_count += 1
            self.logger.error(f"Bot {self.name} error in tournament_end ({self.error_count}/{self.max_errors}): {str(e)}")


class BotManager:
    """Manages loading and execution of all poker bots"""
    
    def __init__(self, players_directory: str = "players", timeout: float = BOT_TURN_TIMEOUT):
        self.players_directory = players_directory
        self.timeout = timeout
        self.bots: Dict[str, BotWrapper] = {}
        self.failed_bots: List[str] = []
        
        self.logger = logging.getLogger("bot_manager")
        
    def load_all_bots(self) -> List[str]:
        """
        Load all bot files from the players directory
        Returns list of successfully loaded bot names
        """
        if not os.path.exists(self.players_directory):
            self.logger.error(f"Players directory '{self.players_directory}' does not exist")
            return []
        
        bot_files = [f for f in os.listdir(self.players_directory) 
                     if f.endswith('.py') and not f.startswith('_')]
        
        loaded_bots = []
        
        for bot_file in bot_files:
            bot_name = bot_file[:-3]  # Remove .py extension
            
            try:
                bot_instance = self._load_bot_from_file(bot_file, bot_name)
                if bot_instance:
                    wrapper = BotWrapper(bot_name, bot_instance, self.timeout)
                    self.bots[bot_name] = wrapper
                    loaded_bots.append(bot_name)
                    self.logger.info(f"Successfully loaded bot: {bot_name}")
                else:
                    self.failed_bots.append(bot_name)
                    self.logger.error(f"Failed to instantiate bot: {bot_name}")
                    
            except Exception as e:
                self.failed_bots.append(bot_name)
                self.logger.error(f"Error loading bot {bot_name}: {str(e)}")
                self.logger.debug(traceback.format_exc())
        
        self.logger.info(f"Loaded {len(loaded_bots)} bots successfully")
        if self.failed_bots:
            self.logger.warning(f"Failed to load bots: {self.failed_bots}")
        
        return loaded_bots
    
    def _load_bot_from_file(self, filename: str, bot_name: str) -> Optional[PokerBotAPI]:
        """Load a single bot from a Python file"""
        file_path = os.path.join(self.players_directory, filename)
        
        # Create module spec
        spec = importlib.util.spec_from_file_location(bot_name, file_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load module spec for {filename}")
        
        # Load the module
        module = importlib.util.module_from_spec(spec)
        sys.modules[bot_name] = module
        spec.loader.exec_module(module)
        
        # Find the bot class
        bot_class = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (isinstance(attr, type) and 
                issubclass(attr, PokerBotAPI) and 
                attr != PokerBotAPI):
                bot_class = attr
                break
        
        if bot_class is None:
            raise ImportError(f"No PokerBotAPI subclass found in {filename}")
        
        # Instantiate the bot
        return bot_class(bot_name)
    
    def get_bot(self, bot_name: str) -> Optional[BotWrapper]:
        """Get a bot wrapper by name"""
        return self.bots.get(bot_name)
    
    def get_active_bots(self) -> List[str]:
        """Get list of bot names that are not disqualified"""
        return [name for name, wrapper in self.bots.items() if not wrapper.is_disqualified()]
    
    def get_all_bot_names(self) -> List[str]:
        """Get all loaded bot names"""
        return list(self.bots.keys())
    
    def disqualify_bot(self, bot_name: str, reason: str):
        """Manually disqualify a bot"""
        if bot_name in self.bots:
            wrapper = self.bots[bot_name]
            wrapper.error_count = wrapper.max_errors  # Force disqualification
            self.logger.warning(f"Bot {bot_name} disqualified: {reason}")
    
    def get_bot_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all bots"""
        stats = {}
        for name, wrapper in self.bots.items():
            stats[name] = {
                'error_count': wrapper.error_count,
                'timeout_count': wrapper.timeout_count,
                'is_disqualified': wrapper.is_disqualified(),
                'max_errors': wrapper.max_errors,
                'max_timeouts': wrapper.max_timeouts
            }
        return stats
    
    def cleanup(self):
        """Clean up resources"""
        # Remove modules from sys.modules to allow reloading
        modules_to_remove = []
        for module_name in sys.modules:
            if module_name in self.bots:
                modules_to_remove.append(module_name)
        
        for module_name in modules_to_remove:
            del sys.modules[module_name]
        
        self.bots.clear()
        self.failed_bots.clear()


# Utility functions for safe bot execution
def safe_bot_call(func, *args, timeout: float = BOT_TURN_TIMEOUT, **kwargs):
    """
    Safely call a bot function with timeout protection
    Returns (success: bool, result: Any, error: Optional[str])
    """
    try:
        with timeout_context(timeout):
            result = func(*args, **kwargs)
            return True, result, None
    except TimeoutException:
        return False, None, "Timeout"
    except Exception as e:
        return False, None, str(e)


def validate_bot_file(file_path: str) -> Tuple[bool, str]:
    """
    Validate that a bot file contains a proper PokerBotAPI implementation
    Returns (is_valid: bool, error_message: str)
    """
    try:
        # Try to load and inspect the module
        spec = importlib.util.spec_from_file_location("temp_bot", file_path)
        if spec is None or spec.loader is None:
            return False, "Could not load module spec"
        
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        # Look for PokerBotAPI subclass
        bot_class = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (isinstance(attr, type) and 
                issubclass(attr, PokerBotAPI) and 
                attr != PokerBotAPI):
                bot_class = attr
                break
        
        if bot_class is None:
            return False, "No PokerBotAPI subclass found"
        
        # Check required methods
        required_methods = ['get_action', 'hand_complete']
        for method in required_methods:
            if not hasattr(bot_class, method):
                return False, f"Missing required method: {method}"
        
        return True, "Valid bot file"
        
    except Exception as e:
        return False, f"Error validating bot file: {str(e)}"