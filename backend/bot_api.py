"""
Bot API Interface for Poker Tournament
This defines the interface that all student bots must implement
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any
try:
    from backend.engine.cards import Card
    from backend.engine.poker_game import GameState, PlayerAction
except ImportError:
    from engine.cards import Card
    from engine.poker_game import GameState, PlayerAction
import logging


class PokerBotAPI(ABC):
    """
    Abstract base class that all poker bots must inherit from.
    Students implement the required methods to create their bot strategy.
    """
    
    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(f"bot.{name}")
    
    @abstractmethod
    def get_action(self, game_state: GameState, hole_cards: List[Card], 
                   legal_actions: List[PlayerAction], min_bet: int, max_bet: int) -> tuple:
        """
        Decide what action to take given the current game state.
        
        Args:
            game_state: Current state of the poker game
            hole_cards: Your two hole cards
            legal_actions: List of actions you can legally take
            min_bet: Minimum bet amount (for raises)
            max_bet: Maximum bet amount (your remaining chips + current bet)
        
        Returns:
            tuple: (PlayerAction, amount)
            - For FOLD, CHECK, CALL, ALL_IN: amount should be 0
            - For RAISE: amount should be the total bet amount (not additional amount)
        
        Examples:
            return (PlayerAction.FOLD, 0)
            return (PlayerAction.CALL, 0)  
            return (PlayerAction.RAISE, 100)  # Raise to 100 total
            return (PlayerAction.ALL_IN, 0)
        """
        pass
    
    @abstractmethod
    def hand_complete(self, game_state: GameState, hand_result: Dict[str, Any]):
        """
        Called when a hand is complete. Use this to learn from the results.
        
        Args:
            game_state: Final game state
            hand_result: Dictionary containing:
                - 'winners': List of winning players
                - 'winning_hands': Dict of player -> best hand
                - 'pot_distribution': Dict of player -> winnings
                - 'showdown_hands': Dict of all revealed hands (if showdown)
        """
        pass
    
    def tournament_start(self, players: List[str], starting_chips: int):
        """
        Called when tournament starts.
        
        Args:
            players: List of all player names in tournament
            starting_chips: Starting chip count for each player
        """
        self.logger.info(f"Tournament starting with {len(players)} players")
    
    def tournament_end(self, final_standings: List[tuple]):
        """
        Called when tournament ends.
        
        Args:
            final_standings: List of (player_name, final_chips, placement) tuples
        """
        placement = next(place for name, chips, place in final_standings if name == self.name)
        self.logger.info(f"Tournament ended. Final placement: {placement}")


class GameInfoAPI:
    """
    Utility class providing game information and helper methods for bots
    """
    
    @staticmethod
    def get_pot_odds(pot: int, bet_to_call: int) -> float:
        """
        Calculate pot odds as a ratio.
        
        Args:
            pot: Current pot size
            bet_to_call: Amount you need to call
            
        Returns:
            float: Pot odds ratio (pot_size / bet_to_call)
        """
        if bet_to_call == 0:
            return float('inf')
        return pot / bet_to_call
    
    @staticmethod
    def get_position_info(game_state: GameState, player_name: str) -> Dict[str, Any]:
        """
        Get position information for a player.
        
        Args:
            game_state: Current game state
            player_name: Name of the player
            
        Returns:
            dict: Position information including:
                - 'position': 0-based position (0 = first to act)
                - 'players_after': Number of players acting after this player
                - 'is_last': True if this player acts last
        """
        try:
            position = game_state.active_players.index(player_name)
            current_pos = game_state.active_players.index(game_state.current_player)
            
            # Adjust position relative to current player
            relative_pos = (position - current_pos) % len(game_state.active_players)
            
            return {
                'position': relative_pos,
                'players_after': len(game_state.active_players) - relative_pos - 1,
                'is_last': relative_pos == len(game_state.active_players) - 1
            }
        except ValueError:
            return {'position': -1, 'players_after': 0, 'is_last': False}
    
    @staticmethod
    def calculate_bet_amount(current_bet: int, player_current_bet: int) -> int:
        """
        Calculate how much a player needs to call.
        
        Args:
            current_bet: The current highest bet
            player_current_bet: How much the player has already bet this round
            
        Returns:
            int: Amount needed to call
        """
        return max(0, current_bet - player_current_bet)
    
    @staticmethod
    def get_active_opponents(game_state: GameState, player_name: str) -> List[str]:
        """
        Get list of active opponents.
        
        Args:
            game_state: Current game state
            player_name: Name of the player
            
        Returns:
            List[str]: List of opponent names still in the hand
        """
        return [player for player in game_state.active_players if player != player_name]
    
    @staticmethod
    def is_heads_up(game_state: GameState) -> bool:
        """
        Check if the game is heads-up (only 2 players remaining).
        
        Args:
            game_state: Current game state
            
        Returns:
            bool: True if only 2 players remain
        """
        return len(game_state.active_players) == 2
    
    @staticmethod
    def get_stack_sizes(game_state: GameState) -> Dict[str, int]:
        """
        Get effective stack sizes for all players.
        
        Args:
            game_state: Current game state
            
        Returns:
            Dict[str, int]: Player names mapped to their chip counts
        """
        return game_state.player_chips.copy()
    
    @staticmethod
    def format_cards(cards: List[Card]) -> str:
        """
        Format a list of cards for display.
        
        Args:
            cards: List of Card objects
            
        Returns:
            str: Formatted string representation
        """
        return ', '.join(str(card) for card in cards)

