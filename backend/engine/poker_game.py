from typing import Any, List, Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import logging

from .cards import Card, Deck, HandEvaluator


@dataclass
class PlayerHand:
    cards: List[Card]

class PlayerAction(Enum):
    FOLD = 0
    CHECK = 1
    CALL = 2
    RAISE = 3
    ALL_IN = 4

@dataclass
class GameState:
    pot: int
    community_cards: List[Card]
    current_bet: int
    player_chips: Dict[str, int]
    player_bets: Dict[str, int]
    active_players: List[str]
    current_player: str
    round_name: str
    min_bet: int
    min_raise: int
    big_blind: int
    small_blind: int

class PokerGame:
    """Manages a single hand of Texas Hold'em poker"""
    
    def __init__(self, players: Dict[str, Any], starting_chips: int = 1000, 
                 small_blind: int = 10, big_blind: int = 20, dealer_button_index: int = 0):
        self.player_bots = players
        self.player_ids = list(players.keys())
        self.starting_chips = starting_chips
        self.small_blind = small_blind
        self.big_blind = big_blind
        
        # Game state
        self.deck = Deck()
        self.community_cards: List[Card] = []
        self.player_hands: Dict[str, PlayerHand] = {}
        self.player_chips: Dict[str, int] = {player: starting_chips for player in self.player_ids}
        self.player_bets: Dict[str, int] = {player: 0 for player in self.player_ids}
        self.active_players: List[str] = self.player_ids.copy()
        self.folded_players: List[str] = []
        self.total_pot_contributions: Dict[str, int] = {player: 0 for player in self.player_ids}
        
        # Betting state
        self.pot = 0
        self.current_bet = 0
        self.dealer_button = dealer_button_index
        self.round_name = "preflop"
        self.players_acted = set()

        # Logging
        self.logger = logging.getLogger(__name__)

    def play_hand(self) -> Dict[str, int]:
        """Plays a single hand of poker, returns chips distribution"""
        self._start_hand()

        # Pre-flop betting
        if len(self.active_players) > 1:
            self.logger.info("\n--- PRE-FLOP BETTING ---")
            self._run_betting_round()
            self._log_round_summary()

        # Flop
        if len(self.active_players) > 1:
            self.advance_to_next_round()
            self.logger.info("\n--- FLOP BETTING ---")
            self._run_betting_round()
            self._log_round_summary()

        # Turn
        if len(self.active_players) > 1:
            self.advance_to_next_round()
            self.logger.info("\n--- TURN BETTING ---")
            self._run_betting_round()
            self._log_round_summary()

        # River
        if len(self.active_players) > 1:
            self.advance_to_next_round()
            self.logger.info("\n--- RIVER BETTING ---")
            self._run_betting_round()
            self._log_round_summary()

        # Showdown
        if len(self.active_players) > 1:
            self.logger.info("\n--- SHOWDOWN ---")
            winners = self.determine_winners()
            self._distribute_pot(winners)
        else:
            # Last remaining player wins
            winners = self.active_players
            self._distribute_pot(winners)

        # Move dealer button for next hand
        self.dealer_button = (self.dealer_button + 1) % len(self.player_ids)
        
        return self.player_chips

    def _start_hand(self):
        """Start a new hand of poker"""
        self.reset_hand()
        self.deal_hole_cards()
        self.post_blinds()
        
        self.logger.info(f"\n{'='*30}\n--- NEW HAND ---")
        self.logger.info(f"Dealer: {self.player_ids[self.dealer_button]}")
        for p_id in self.active_players:
            self.logger.info(f"{p_id} has {self.player_hands[p_id]} (chips: {self.player_chips[p_id]})")
    
    def reset_hand(self):
        """Reset for a new hand"""
        self.deck = Deck()
        self.deck.shuffle()
        self.community_cards = []
        self.player_hands = {}
        self.active_players = [p for p in self.player_ids if self.player_chips[p] > 0]
        self.player_bets = {player: 0 for player in self.player_ids}
        self.total_pot_contributions = {player: 0 for player in self.player_ids}
        self.folded_players = []
        self.pot = 0
        self.current_bet = self.big_blind
        self.min_raise = self.big_blind
        self.round_name = "preflop"
    
    def deal_hole_cards(self):
        """Deal 2 cards to each active player"""
        for player in self.active_players:
            cards = [card for card in [self.deck.deal_card() for _ in range(2)] if card is not None]
            self.player_hands[player] = PlayerHand(cards)
    
    def post_blinds(self):
        """Post small and big blinds"""
        if len(self.active_players) < 2:
            return
        
        # Determine the players who post blinds relative to the dealer
        # Find the index of the current dealer within the active players list
        try:
            dealer_active_index = self.active_players.index(self.player_ids[self.dealer_button])
        except ValueError:
            # Dealer might have been eliminated, find next active player as nominal dealer
            for i in range(len(self.player_ids)):
                potential_dealer_index = (self.dealer_button + i) % len(self.player_ids)
                potential_dealer = self.player_ids[potential_dealer_index]
                if potential_dealer in self.active_players:
                    self.dealer_button = potential_dealer_index # Update actual dealer button
                    dealer_active_index = self.active_players.index(potential_dealer)
                    break
            else: # Should not happen if len(self.active_players) >= 1
                return # No active dealer found (e.g., all players eliminated)
        
        if len(self.active_players) == 2:
            # Heads-up: Dealer is small blind, non-dealer is big blind
            small_blind_player = self.active_players[dealer_active_index]
            big_blind_player = self.active_players[(dealer_active_index + 1) % 2]
        else:
            # Normal play: Small blind is left of dealer, big blind is left of small blind
            small_blind_player = self.active_players[(dealer_active_index + 1) % len(self.active_players)]
            big_blind_player = self.active_players[(dealer_active_index + 2) % len(self.active_players)]
        
        # Post small blind
        small_blind_amount = min(self.small_blind, self.player_chips[small_blind_player])
        self.player_bets[small_blind_player] = small_blind_amount
        self.player_chips[small_blind_player] -= small_blind_amount
        self.pot += small_blind_amount
        self.total_pot_contributions[small_blind_player] += small_blind_amount

        # Post big blind
        big_blind_amount = min(self.big_blind, self.player_chips[big_blind_player])
        self.player_bets[big_blind_player] = big_blind_amount
        self.player_chips[big_blind_player] -= big_blind_amount
        self.pot += big_blind_amount
        self.total_pot_contributions[big_blind_player] += big_blind_amount
        
        self.logger.info(f"{small_blind_player} posts small blind: {small_blind_amount}")
        self.logger.info(f"{big_blind_player} posts big blind: {big_blind_amount}")

    def _run_betting_round(self):
        self._start_betting_round()
        
        while not self.is_betting_round_complete():
            player_id = self.get_current_player()
            if not player_id:
                break
            
            # Skip players who are all-in
            if self.player_chips[player_id] == 0:
                self.advance_to_next_player()
                continue
            
            bot = self.player_bots[player_id]
            game_state = self.get_game_state()
            player_hand = self.get_player_hand(player_id)
            legal_actions = self.get_legal_actions(game_state, player_id)
            min_bet = game_state.min_bet
            max_bet = self.player_chips[player_id] + self.player_bets[player_id]

            if player_hand is None:
                self.logger.error(f"Player {player_id} has no hand")
                self.process_action(player_id, PlayerAction.FOLD, 0)
                self.advance_to_next_player()
                continue

            action, amount = bot.get_action(game_state, player_hand.cards, legal_actions, min_bet, max_bet)
            
            self.process_action(player_id, action, amount)
            self.advance_to_next_player()

    def _start_betting_round(self):
        """Resets betting state for a new round."""
        self.players_acted = set()
        # Find the index of the current dealer within the active players list
        try:
            dealer_active_index = self.active_players.index(self.player_ids[self.dealer_button])
        except ValueError:
            # Dealer might have been eliminated, find next active player as nominal dealer
            for i in range(len(self.player_ids)):
                potential_dealer_index = (self.dealer_button + i) % len(self.player_ids)
                potential_dealer = self.player_ids[potential_dealer_index]
                if potential_dealer in self.active_players:
                    self.dealer_button = potential_dealer_index # Update actual dealer button
                    dealer_active_index = self.active_players.index(potential_dealer)
                    break
            else: # Should not happen if len(self.active_players) >= 1
                dealer_active_index = 0 # Default to first active player if no valid dealer is found (shouldn't happen with >=1 active)

        if self.round_name == "preflop":
            if len(self.active_players) == 2:
                # Heads-up: Dealer (SB) acts first pre-flop
                self.current_player_index = dealer_active_index
            else:
                # 3+ players: UTG acts first (Left of BB)
                self.current_player_index = (dealer_active_index + 3) % len(self.active_players)
            self.min_raise = self.big_blind
        else:
            self.current_player_index = (dealer_active_index + 1) % len(self.active_players)
            self.current_bet = 0
            self.min_raise = self.big_blind
            for p in self.player_ids:
                self.player_bets[p] = 0
        
    def get_current_player(self) -> str:
        """Get the current player to act"""
        if not self.active_players:
            return ""
        return self.active_players[self.current_player_index % len(self.active_players)]
    
    def get_game_state(self) -> GameState:
        """Get the current game state visible to players"""
        return GameState(
            pot=self.pot,
            community_cards=self.community_cards.copy(),
            current_bet=self.current_bet,
            player_chips=self.player_chips.copy(),
            player_bets=self.player_bets.copy(),
            active_players=self.active_players.copy(),
            current_player=self.get_current_player(),
            round_name=self.round_name,
            min_bet=self.current_bet + self.min_raise,
            min_raise=self.min_raise,
            big_blind=self.big_blind,
            small_blind=self.small_blind
        )
    
    def get_player_hand(self, player: str) -> Optional[PlayerHand]:
        """Get a player's hole cards"""
        return self.player_hands.get(player)
    
    def validate_action(self, action: PlayerAction, amount: int, game_state: GameState, 
                   player_name: str) -> bool:
        """
        Validate if an action is legal given the current game state.
        
        Args:
            action: The action the player wants to take
            amount: The bet amount (if applicable)
            game_state: Current game state
            player_name: Name of the player taking the action
            
        Returns:
            bool: True if the action is valid
        """
        if player_name not in game_state.active_players:
            return False
        
        if player_name != game_state.current_player:
            return False
        
        player_chips = game_state.player_chips[player_name]
        player_bet = game_state.player_bets[player_name]
        to_call = game_state.current_bet - player_bet
        
        if action == PlayerAction.FOLD:
            return True
        elif action == PlayerAction.CHECK:
            return to_call == 0
        elif action == PlayerAction.CALL:
            return to_call > 0 and player_chips >= to_call
        elif action == PlayerAction.RAISE:
            min_raise = game_state.min_bet
            return (amount >= min_raise and 
                    player_chips >= (amount - player_bet) and
                    amount > game_state.current_bet)
        elif action == PlayerAction.ALL_IN:
            return player_chips > 0
        
        return False

    def process_action(self, player: str, action: PlayerAction, amount: int = 0):
        """Process a player's action"""
        if not self.validate_action(action, amount, self.get_game_state(), player):
            # Default to fold if action is invalid
            self.logger.warning(f"Bot {player} attempted illegal action {action.name}, folding.")
            action = PlayerAction.FOLD
            amount = 0
        
        self.players_acted.add(player)
        player_bet = self.player_bets[player]
        to_call = self.current_bet - player_bet
        
        if action == PlayerAction.FOLD:
            self.folded_players.append(player)
            if player in self.active_players:
                self.active_players.remove(player)
                # Decrement index so that the next increment in advance_to_next_player
                # points to the correct next player (who shifted into this slot)
                self.current_player_index -= 1
            self.logger.info(f"  {player} folds")
        
        elif action == PlayerAction.CHECK:
            self.logger.info(f"  {player} checks")
        
        elif action == PlayerAction.CALL:
            call_amount = min(to_call, self.player_chips[player])
            self.player_bets[player] += call_amount
            self.player_chips[player] -= call_amount
            self.pot += call_amount
            self.total_pot_contributions[player] += call_amount
            self.logger.info(f"  {player} calls {call_amount}")
        
        elif action == PlayerAction.RAISE:
            raise_total = amount
            raise_amount = raise_total - self.player_bets[player]

            if self.player_chips[player] <= raise_amount:
                # Player does not have enough chips, it's an all-in
                raise_amount = self.player_chips[player]
                action = PlayerAction.ALL_IN

            self.player_bets[player] += raise_amount
            self.player_chips[player] -= raise_amount
            self.pot += raise_amount
            self.total_pot_contributions[player] += raise_amount
            
            actual_raise = self.player_bets[player] - self.current_bet
            if actual_raise >= self.min_raise:
                self.min_raise = actual_raise
            
            if action == PlayerAction.ALL_IN:
                self.logger.info(f"  {player} goes all-in with {raise_amount}")
                if self.player_bets[player] > self.current_bet:
                    self.current_bet = self.player_bets[player]
                    self.players_acted.clear()
            else:
                self.current_bet = self.player_bets[player]
                self.logger.info(f"  {player} raises to {self.current_bet}")
                self.players_acted.clear() 
            self.players_acted.add(player)


        elif action == PlayerAction.ALL_IN:
            all_in_amount = self.player_chips[player]
            self.player_bets[player] += all_in_amount
            self.player_chips[player] = 0
            self.pot += all_in_amount
            self.total_pot_contributions[player] += all_in_amount
            new_bet = self.player_bets[player]
            if new_bet > self.current_bet:
                actual_raise = new_bet - self.current_bet
                if actual_raise >= self.min_raise:
                    self.min_raise = actual_raise
                self.current_bet = new_bet
                self.players_acted.clear() 
            self.players_acted.add(player)
            self.logger.info(f"  {player} goes all-in for {all_in_amount}")
    
    def advance_to_next_player(self):
        """Move to the next player"""
        if not self.active_players:
            return
        self.current_player_index = (self.current_player_index + 1) % len(self.active_players)
    
    def is_betting_round_complete(self) -> bool:
        """Check if the current betting round is complete"""
        if len(self.active_players) <= 1:
            return True
        
        # All non-all-in players have had a chance to act
        non_all_in_players = [p for p in self.active_players if self.player_chips[p] > 0]
        if not self.players_acted.issuperset(non_all_in_players):
            return False

        # All non-folded, non-all-in players have the same amount bet
        max_bet = self.current_bet
        for player in non_all_in_players:
            if self.player_bets[player] != max_bet:
                return False
        
        return True
    
    def advance_to_next_round(self):
        """Advance to the next betting round"""
        if self.round_name == "preflop":
            self.deal_flop()
            self.round_name = "flop"
        elif self.round_name == "flop":
            self.deal_turn()
            self.round_name = "turn"
        elif self.round_name == "turn":
            self.deal_river()
            self.round_name = "river"
        elif self.round_name == "river":
            self.round_name = "showdown"
    
    def deal_flop(self):
        """Deal the flop (3 community cards)"""
        self.deck.deal_card()  # Burn card
        for _ in range(3):
            card = self.deck.deal_card()
            if card is not None:
                self.community_cards.append(card)
        self.logger.info(f"FLOP: [{', '.join(map(str, self.community_cards))}]")
    
    def deal_turn(self):
        """Deal the turn (4th community card)"""
        self.deck.deal_card()  # Burn card
        card = self.deck.deal_card()
        if card is not None:
            self.community_cards.append(card)
        self.logger.info(f"TURN: {self.community_cards[-1]}")
    
    def deal_river(self):
        """Deal the river (5th community card)"""
        self.deck.deal_card()  # Burn card
        card = self.deck.deal_card()
        if card is not None:
            self.community_cards.append(card)
        self.logger.info(f"RIVER: {self.community_cards[-1]}")
    
    def determine_winners(self) -> List[str]:
        """Determine winners using HandEvaluator"""
        if len(self.active_players) == 1:
            return self.active_players
        
        player_hands_to_evaluate = []
        for player_id in self.active_players:
            hole_cards = self.player_hands[player_id].cards
            all_cards = hole_cards + self.community_cards
            player_hands_to_evaluate.append((player_id, all_cards))
            
            best_hand_type, _, best_5_cards = HandEvaluator.evaluate_best_hand(all_cards)
            self.logger.info(f"  {player_id}: Hand: {hole_cards} -> {best_hand_type} [{', '.join(map(str, best_5_cards))}]")

        winners = HandEvaluator.get_winners(player_hands_to_evaluate)
        self.logger.info(f"WINNERS: {', '.join(winners)}")
        return winners
    
    def _distribute_pot(self, winners: List[str]):
        """Distribute the pot among the winners, handling side pots."""
        
        # Contributions for this hand for all players who put money in
        contributions = self.total_pot_contributions.copy()
        active_contributors = [p for p, amount in contributions.items() if amount > 0]
        
        if not active_contributors:
            return

        # We distribute until pot is empty or contributions are exhausted
        while self.pot > 0 and sum(contributions.values()) > 0:
            # Find the smallest non-zero contribution among all contributors
            # (This defines the size of the current "main" or "side" pot level)
            min_contribution = min([c for c in contributions.values() if c > 0])
            
            current_pot_size = 0
            eligible_players = []
            
            # Collect chips for this pot level from everyone
            for player in list(contributions.keys()):
                if contributions[player] > 0:
                    amount_to_take = min(contributions[player], min_contribution)
                    current_pot_size += amount_to_take
                    contributions[player] -= amount_to_take
                    
                    # Players who are still active (haven't folded) and contributed 
                    # to this level are eligible to win this pot
                    if player in self.active_players:
                        eligible_players.append(player)

            if not eligible_players:
                # Should not happen if logic is correct, but handle gracefully
                # e.g. everyone folded? But determining winners happens after showdown.
                # If everyone folded, active_players has 1, who is eligible.
                continue

            # Determine winner(s) for this specific pot from eligible players
            # We need to re-evaluate winners among ONLY the eligible players
            
            if len(eligible_players) == 1:
                pot_winners = eligible_players
            else:
                # Evaluate hands only for eligible players
                player_hands_to_evaluate = []
                for player_id in eligible_players:
                    hole_cards = self.player_hands[player_id].cards
                    all_cards = hole_cards + self.community_cards
                    player_hands_to_evaluate.append((player_id, all_cards))
                
                pot_winners = HandEvaluator.get_winners(player_hands_to_evaluate)
            
            # Distribute this pot level
            winnings_per_player = current_pot_size // len(pot_winners)
            remainder = current_pot_size % len(pot_winners)
            
            for winner in pot_winners:
                self.player_chips[winner] += winnings_per_player
                self.logger.info(f"{winner} wins {winnings_per_player} from side/main pot")
            
            # Give remainder to first winner (simplified)
            if remainder > 0:
                self.player_chips[pot_winners[0]] += remainder

            self.pot -= current_pot_size
        
        # If pot still has chips (rounding errors or logic slips), give to main winners
        if self.pot > 0:
             self.logger.warning(f"Pot logic residual: {self.pot}")
             # Give to original overall winners
             for winner in winners:
                 self.player_chips[winner] += self.pot // len(winners)
             self.pot = 0

    def _log_round_summary(self):
        """Logs a summary of the current round."""
        self.logger.info(f"Community Cards: [{', '.join(map(str, self.community_cards))}]")
        self.logger.info(f"Pot: {self.pot}")
        self.logger.info("-" * 20)

    def get_legal_actions(self, game_state: GameState, player_name: str) -> List[PlayerAction]:
        """
        Get all legal actions for a player given the current game state.
        
        Args:
            game_state: Current game state
            player_name: Name of the player
            
        Returns:
            List[PlayerAction]: List of legal actions
        """
        if (player_name not in game_state.active_players or 
            player_name != game_state.current_player):
            return []
        
        player_chips = game_state.player_chips[player_name]
        player_bet = game_state.player_bets[player_name]
        to_call = game_state.current_bet - player_bet
        
        legal_actions = [PlayerAction.FOLD]
        
        if to_call == 0:
            legal_actions.append(PlayerAction.CHECK)
        else:
            if player_chips >= to_call:
                legal_actions.append(PlayerAction.CALL)
        
        # Can raise if we have chips to meet the minimum raise
        if (player_chips + player_bet) >= game_state.min_bet:
            legal_actions.append(PlayerAction.RAISE)
        
        # Can always go all-in if we have chips
        if player_chips > 0:
            legal_actions.append(PlayerAction.ALL_IN)
        
        return legal_actions