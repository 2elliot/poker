"""
Poker Tournament Management System
Handles tournament structure, player elimination, and progression
"""
import math
import random
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass
from enum import Enum
import logging


class TournamentType(Enum):
    SINGLE_ELIMINATION = "single_elimination"
    ROUND_ROBIN = "round_robin" 
    FREEZE_OUT = "freeze_out"  # No rebuys, play until one player remains


@dataclass
class TournamentSettings:
    tournament_type: TournamentType = TournamentType.FREEZE_OUT
    starting_chips: int = 1000
    small_blind: int = 10
    big_blind: int = 20
    blind_increase_interval: int = 10  # hands
    blind_increase_factor: float = 1.5
    max_players_per_table: int = 6
    min_players_per_table: int = 2
    time_limit_per_action: float = 10.0  # seconds
    max_hands_per_level: int = 50


@dataclass
class PlayerStats:
    name: str
    chips: int = 0
    hands_played: int = 0
    hands_won: int = 0
    total_winnings: int = 0
    biggest_pot_won: int = 0
    position: int = 0
    is_eliminated: bool = False
    elimination_hand: int = 0


class TournamentTable:
    """Manages a single table in the tournament"""
    
    def __init__(self, table_id: int, players: List[str], settings: TournamentSettings):
        self.table_id = table_id
        self.players = players.copy()
        self.settings = settings
        self.eliminated_players: List[str] = []
        self.hands_played = 0
        self.current_blind_level = 1
        self.dealer_button: int = 0
        
        self.logger = logging.getLogger(f"tournament.table_{table_id}")
        
    def get_active_players(self) -> List[str]:
        """Get list of players still active at this table"""
        return [p for p in self.players if p not in self.eliminated_players]
    
    def eliminate_player(self, player: str, hand_number: int):
        """Eliminate a player from this table"""
        if player in self.players and player not in self.eliminated_players:
            self.eliminated_players.append(player)
            self.logger.info(f"Player {player} eliminated from table {self.table_id} on hand {hand_number}")
    
    def should_increase_blinds(self) -> bool:
        """Check if blinds should be increased"""
        return self.hands_played % self.settings.blind_increase_interval == 0 and self.hands_played > 0
    
    def increase_blinds(self):
        """Increase the blind levels"""
        self.current_blind_level += 1
        new_small = int(self.settings.small_blind * (self.settings.blind_increase_factor ** (self.current_blind_level - 1)))
        new_big = int(self.settings.big_blind * (self.settings.blind_increase_factor ** (self.current_blind_level - 1)))
        
        self.logger.info(f"Table {self.table_id} blinds increased to {new_small}/{new_big} (Level {self.current_blind_level})")
        return new_small, new_big
    
    def get_current_blinds(self) -> Tuple[int, int]:
        """Get current blind levels for this table"""
        small = int(self.settings.small_blind * (self.settings.blind_increase_factor ** (self.current_blind_level - 1)))
        big = int(self.settings.big_blind * (self.settings.blind_increase_factor ** (self.current_blind_level - 1)))
        return small, big
    
    def is_ready_to_break(self) -> bool:
        """Check if table should be broken up (too few players)"""
        return len(self.get_active_players()) < self.settings.min_players_per_table


class PokerTournament:
    """Manages the entire poker tournament"""
    
    def __init__(self, players: List[str], settings: Optional[TournamentSettings] = None):
        self.players = players.copy()
        self.settings = settings or TournamentSettings()
        
        # Tournament state
        self.tables: Dict[int, TournamentTable] = {}
        self.player_stats: Dict[str, PlayerStats] = {}
        self.eliminated_players: List[str] = []
        self.current_hand = 0
        self.tournament_complete = False
        
        # Initialize player stats
        for player in players:
            self.player_stats[player] = PlayerStats(
                name=player, 
                chips=self.settings.starting_chips
            )
        
        self.logger = logging.getLogger("tournament")
        self.setup_tables()
    
    def setup_tables(self):
        """Set up initial tournament tables"""
        random.shuffle(self.players)  # Randomize seating
        
        players_per_table = min(self.settings.max_players_per_table, 
                               max(self.settings.min_players_per_table,
                                   len(self.players) // self.calculate_optimal_table_count()))
        
        table_id = 1
        for i in range(0, len(self.players), players_per_table):
            table_players = self.players[i:i + players_per_table]
            if len(table_players) >= self.settings.min_players_per_table:
                self.tables[table_id] = TournamentTable(table_id, table_players, self.settings)
                table_id += 1
            else:
                # Distribute remaining players to existing tables
                for j, player in enumerate(table_players):
                    target_table = (j % (table_id - 1)) + 1
                    self.tables[target_table].players.append(player)
        
        self.logger.info(f"Tournament setup with {len(self.tables)} tables")
        for table_id, table in self.tables.items():
            self.logger.info(f"Table {table_id}: {table.players}")
    
    def calculate_optimal_table_count(self) -> int:
        """Calculate optimal number of tables"""
        if len(self.players) <= self.settings.max_players_per_table:
            return 1
        
        # Try to balance table sizes
        optimal_count = math.ceil(len(self.players) / self.settings.max_players_per_table)
        
        # Ensure no table has too few players
        while optimal_count > 1:
            min_players = len(self.players) // optimal_count
            if min_players >= self.settings.min_players_per_table:
                break
            optimal_count -= 1
        
        return max(1, optimal_count)
    
    def get_active_players(self) -> List[str]:
        """Get all active players across all tables"""
        active = []
        for table in self.tables.values():
            active.extend(table.get_active_players())
        return active
    
    def eliminate_player(self, player: str, final_chips: int = 0):
        """Eliminate a player from the tournament"""
        if player in self.eliminated_players:
            return
        
        self.eliminated_players.append(player)
        self.player_stats[player].chips = final_chips
        self.player_stats[player].is_eliminated = True
        self.player_stats[player].elimination_hand = self.current_hand
        self.player_stats[player].position = len(self.players) - len(self.eliminated_players) + 1
        
        # Remove from table
        for table in self.tables.values():
            if player in table.players:
                table.eliminate_player(player, self.current_hand)
                break
        
        self.logger.info(f"Player {player} eliminated in position {self.player_stats[player].position}")
        
        # Check if tournament is complete
        if len(self.get_active_players()) <= 1:
            self.tournament_complete = True
            if self.get_active_players():
                winner = self.get_active_players()[0]
                self.player_stats[winner].position = 1
                self.logger.info(f"Tournament complete! Winner: {winner}")
    
    def update_player_chips(self, player: str, new_chip_count: int):
        """Update a player's chip count"""
        if player in self.player_stats:
            old_chips = self.player_stats[player].chips
            self.player_stats[player].chips = new_chip_count
            
            # Check for elimination
            if new_chip_count <= 0 and player not in self.eliminated_players:
                self.eliminate_player(player, 0)
    
    def record_hand_result(self, player: str, won: bool, winnings: int = 0):
        """Record the result of a hand for a player"""
        if player in self.player_stats:
            stats = self.player_stats[player]
            stats.hands_played += 1
            if won:
                stats.hands_won += 1
                stats.total_winnings += winnings
                stats.biggest_pot_won = max(stats.biggest_pot_won, winnings)
    
    def should_rebalance_tables(self) -> bool:
        """Check if tables need rebalancing based on stricter criteria."""
        active_players = self.get_active_players()
        active_tables = [t for t in self.tables.values() if len(t.get_active_players()) > 0]
        
        # If all active players can fit on one table, and there's more than one table, consolidate.
        if len(active_players) <= self.settings.max_players_per_table and len(active_tables) > 1:
            return True
        
        if len(active_tables) <= 1:
            return False
        
        table_sizes = [len(t.get_active_players()) for t in active_tables]

        # Trigger if any table is "ready to break" (e.g., < min_players_per_table, which is 2 by default)
        for table in active_tables:
            if table.is_ready_to_break():
                return True
        
        # User's specific rule: If total active players >= 4, no table should have < 4 players.
        # This check should only trigger if it's actually possible to form tables of 4+.
        if len(active_players) >= 4:
            if any(size < 4 for size in table_sizes):
                # Calculate if it's possible to form tables of 4+
                # Find the maximum number of tables that could be formed with 4 players each
                max_tables_at_4 = len(active_players) // 4
                if max_tables_at_4 >= len(active_tables): # If we can form all tables with 4+ players, rebalance
                     return True

        # Check for significant imbalance (difference of more than 1 player between tables)
        if len(table_sizes) > 1 and max(table_sizes) - min(table_sizes) > 1:
            return True
        
        return False
    
    def rebalance_tables(self):
        """Rebalance players across tables by gathering all active players and re-distributing them."""
        self.logger.info("Rebalancing tables...")
        active_players = self.get_active_players()
        
        # Clear all existing tables
        self.tables.clear()
        
        # Re-distribute all active players from scratch
        random.shuffle(active_players) # Shuffle to randomize seating again
        
        # Determine the target number of tables and players per table
        num_players = len(active_players)
        
        if num_players == 0:
            return

        if num_players <= self.settings.max_players_per_table:
            # All players fit on one table (final table or early game)
            self.tables[1] = TournamentTable(1, active_players, self.settings)
            self.logger.info(f"Consolidated to single table with {num_players} players.")
            return

        # Calculate ideal players per table aiming for >= 4 if possible
        # This will try to create tables of min_size 4 if total_players >=4
        min_players_per_table_target = max(self.settings.min_players_per_table, 4 if num_players >= 4 else 0)

        # Calculate optimal number of tables based on max_players_per_table and min_players_per_table_target
        # Try to make tables as full as possible without exceeding max_players_per_table
        # And keeping above min_players_per_table_target
        
        num_tables = math.ceil(num_players / self.settings.max_players_per_table)
        
        # Adjust num_tables to avoid having tables smaller than min_players_per_table_target if possible
        while num_tables > 1 and (num_players / num_tables) < min_players_per_table_target:
            num_tables -= 1
        num_tables = max(1, num_tables) # Ensure at least one table

        players_per_table_base = num_players // num_tables
        remaining_players = num_players % num_tables

        current_player_idx = 0
        for i in range(num_tables):
            table_id = i + 1
            players_on_this_table = players_per_table_base
            if i < remaining_players:
                players_on_this_table += 1
            
            table_players_subset = active_players[current_player_idx : current_player_idx + players_on_this_table]
            
            if len(table_players_subset) < self.settings.min_players_per_table:
                # This should ideally not happen with the logic above, but as a safeguard.
                self.logger.warning(f"Attempted to create table with fewer than min_players_per_table: {len(table_players_subset)}")
                if len(table_players_subset) == 0: continue # Don't create empty tables
            
            self.tables[table_id] = TournamentTable(table_id, table_players_subset, self.settings)
            self.logger.info(f"Table {table_id}: {len(table_players_subset)} players: {', '.join(table_players_subset)}")
            current_player_idx += players_on_this_table
        
        self.logger.info(f"Tables rebalanced. Active tables: {len(self.tables)}")
    
    def consolidate_to_final_table(self, players: List[str]):
        """Move all remaining players to a single final table"""
        self.tables.clear()
        self.tables[1] = TournamentTable(1, players, self.settings)
        self.logger.info(f"Consolidated to final table with {len(players)} players")
    
    def get_tournament_status(self) -> Dict[str, Any]:
        """Get current tournament status"""
        active_players = self.get_active_players()
        
        return {
            'hand_number': self.current_hand,
            'total_players': len(self.players),
            'active_players': len(active_players),
            'eliminated_players': len(self.eliminated_players),
            'active_tables': len([t for t in self.tables.values() if len(t.get_active_players()) > 0]),
            'tournament_complete': self.tournament_complete,
            'chip_leader': self.get_chip_leader(),
            'average_stack': self.get_average_stack(),
            'players_remaining': active_players
        }
    
    def get_chip_leader(self) -> Optional[str]:
        """Get the current chip leader"""
        active_players = self.get_active_players()
        if not active_players:
            return None
        
        return max(active_players, key=lambda p: self.player_stats[p].chips)
    
    def get_average_stack(self) -> int:
        """Get the average chip stack of active players"""
        active_players = self.get_active_players()
        if not active_players:
            return 0
        
        total_chips = sum(self.player_stats[p].chips for p in active_players)
        return total_chips // len(active_players)
    
    def get_leaderboard(self) -> List[Tuple[str, int, int]]:
        """Get current leaderboard (name, chips, position)"""
        leaderboard = []
        
        # Active players sorted by chips
        active_players = self.get_active_players()
        active_sorted = sorted(active_players, key=lambda p: self.player_stats[p].chips, reverse=True)
        
        for i, player in enumerate(active_sorted):
            leaderboard.append((player, self.player_stats[player].chips, i + 1))
        
        # Eliminated players by elimination order (reverse)
        eliminated_sorted = sorted(self.eliminated_players, 
                                 key=lambda p: self.player_stats[p].position)
        
        for player in eliminated_sorted:
            leaderboard.append((player, self.player_stats[player].chips, self.player_stats[player].position))
        
        return leaderboard
    
    def advance_hand(self):
        """Advance to the next hand"""
        self.current_hand += 1

        # Increase blinds if necessary
        for table in self.tables.values():
            table.hands_played += 1
            if table.should_increase_blinds():
                table.increase_blinds()
    
    def is_tournament_complete(self) -> bool:
        """Check if tournament is complete"""
        return self.tournament_complete or len(self.get_active_players()) <= 1
    
    def get_final_results(self) -> List[Tuple[str, int, int]]:
        """Get final tournament results"""
        return self.get_leaderboard()