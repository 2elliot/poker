"""
Main Tournament Runner
Orchestrates the entire poker tournament from start to finish
Windows-safe version (fully cross-platform)
"""

import sys
import logging
import time
import random
import json
import os
import multiprocessing
from typing import List, Dict, Any, Optional
from datetime import datetime

try:
    from backend.engine.poker_game import PokerGame, GameState, PlayerAction
    from backend.engine.cards import HandEvaluator
    from backend.tournament import PokerTournament, TournamentSettings, TournamentType
    from backend.bot_manager import BotManager
except ImportError:
    from engine.poker_game import PokerGame, GameState, PlayerAction
    from engine.cards import HandEvaluator
    from tournament import PokerTournament, TournamentSettings, TournamentType
    from bot_manager import BotManager


class TournamentRunner:
    """Main class that runs the entire poker tournament"""

    def __init__(
        self,
        settings: Optional[TournamentSettings] = None,
        players_directory: str = "players",
        log_directory: str = "logs",
    ):
        # Normalize paths for Windows/macOS/Linux
        self.players_directory = os.path.abspath(players_directory)
        self.log_directory = os.path.abspath(log_directory)

        self.settings = settings or TournamentSettings()

        # Core components
        self.bot_manager = BotManager(
            self.players_directory,
            self.settings.time_limit_per_action,
        )
        self.tournament: Optional[PokerTournament] = None
        self.current_games: Dict[int, PokerGame] = {}

        # Results tracking
        self.tournament_results: Dict[str, Any] = {}
        self.hand_histories: List[Dict[str, Any]] = []

        # Setup logging
        self.setup_logging()
        self.logger = logging.getLogger("tournament_runner")

    def setup_logging(self):
        """Configure logging for the tournament (Windows-safe)"""

        # Force UTF-8 on Windows terminals
        os.environ.setdefault("PYTHONUTF8", "1")

        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8") # type: ignore

        os.makedirs(self.log_directory, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_filename = os.path.join(
            self.log_directory, f"tournament_{timestamp}.log"
        )

        # Prevent duplicate handlers (common Windows issue)
        root_logger = logging.getLogger()
        if root_logger.handlers:
            root_logger.handlers.clear()

        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler(log_filename, encoding="utf-8"),
                logging.StreamHandler(sys.stdout),
            ],
        )

    def run_tournament(self) -> Dict[str, Any]:
        """Run the complete tournament from start to finish"""
        self.logger.info("Starting poker tournament...")
        start_time = time.time()

        try:
            loaded_bots = self.bot_manager.load_all_bots()
            if len(loaded_bots) < 2:
                raise ValueError(
                    f"Need at least 2 bots to run tournament, found {len(loaded_bots)}"
                )

            self.logger.info(f"Loaded {len(loaded_bots)} bots: {loaded_bots}")

            self.tournament = PokerTournament(loaded_bots, self.settings)

            while not self.tournament.is_tournament_complete():
                self.run_tournament_round()

                if self.tournament.should_rebalance_tables():
                    self.tournament.rebalance_tables()
                    self.logger.info("Tables rebalanced")

            final_results = self.tournament.get_final_results()
            end_time = time.time()

            self.tournament_results = {
                "final_standings": final_results,
                "tournament_duration": end_time - start_time,
                "total_hands": self.tournament.current_hand,
                "settings": {
                    "starting_chips": self.settings.starting_chips,
                    "tournament_type": self.settings.tournament_type.value,
                    "blind_levels": f"{self.settings.small_blind}/{self.settings.big_blind}",
                    "time_limit": self.settings.time_limit_per_action,
                },
                "bot_stats": self.bot_manager.get_bot_stats(),
            }

            self.save_tournament_results()
            self.print_final_results()

            return self.tournament_results

        except Exception as e:
            self.logger.error(f"Tournament error: {str(e)}")
            raise
        finally:
            self.bot_manager.cleanup()

    def run_tournament_round(self):
        if self.tournament is None:
            return
        
        active_tables = {
            tid: table
            for tid, table in self.tournament.tables.items()
            if len(table.get_active_players()) >= 2
        }

        if not active_tables:
            return

        self.current_games.clear()

        for table_id, table in active_tables.items():
            player_ids = table.get_active_players()
            if len(player_ids) < 2:
                continue

            small_blind, big_blind = table.get_current_blinds()
            bots = {pid: self.bot_manager.get_bot(pid) for pid in player_ids}

            game = PokerGame(
                bots,
                starting_chips=0,
                small_blind=small_blind,
                big_blind=big_blind,
                dealer_button_index=table.dealer_button % len(player_ids),
            )

            for player in player_ids:
                game.player_chips[player] = (
                    self.tournament.player_stats[player].chips
                )

            self.current_games[table_id] = game

        for table_id, game in self.current_games.items():
            try:
                self.play_single_hand(table_id, game)
                self.tournament.tables[table_id].dealer_button = game.dealer_button
            except Exception as e:
                self.logger.error(
                    f"Error playing hand on table {table_id}: {str(e)}"
                )

        self.tournament.advance_hand()

    def play_single_hand(self, table_id: int, game: PokerGame):
        if self.tournament is None:
            return
        
        self.logger.info(
            f"Starting hand #{self.tournament.current_hand + 1} on table {table_id}"
        )

        final_chips = game.play_hand()

        for player_id in list(final_chips.keys()):
            bot = self.bot_manager.get_bot(player_id)
            if bot and bot.is_disqualified():
                self.logger.info(
                    f"Bot {player_id} disqualified. Removing remaining chips "
                    f"({final_chips[player_id]})."
                )
                final_chips[player_id] = 0

        for player_id, chips in final_chips.items():
            self.tournament.update_player_chips(player_id, chips)

        self.logger.info(
            f"Hand #{self.tournament.current_hand + 1} complete on table {table_id}"
        )

    def save_tournament_results(self):
        if not self.tournament_results:
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_file = os.path.join(
            self.log_directory, f"results_{timestamp}.json"
        )

        with open(results_file, "w", encoding="utf-8") as f:
            json.dump(
                self._make_json_serializable(self.tournament_results),
                f,
                indent=2,
            )

        self.logger.info(f"Tournament results saved to {results_file}")

    def _make_json_serializable(self, data):
        if isinstance(data, dict):
            return {k: self._make_json_serializable(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self._make_json_serializable(i) for i in data]
        if isinstance(data, (str, int, float, bool, type(None))):
            return data
        return str(data)

    def print_final_results(self):
        if not self.tournament_results:
            return

        print("\n" + "=" * 60)
        print("POKER TOURNAMENT RESULTS")
        print("=" * 60)

        for player, chips, position in self.tournament_results["final_standings"]:
            prefix = {1: "🏆", 2: "🥈", 3: "🥉"}.get(position, " ")
            print(f"{prefix} {position}. {player} - {chips:,} chips")

        print("=" * 60 + "\n")


def main():
    multiprocessing.freeze_support()  # Required on Windows

    import argparse

    parser = argparse.ArgumentParser(description="Run a poker bot tournament")
    parser.add_argument("--players-dir", default="players")
    parser.add_argument("--starting-chips", type=int, default=1000)
    parser.add_argument("--small-blind", type=int, default=10)
    parser.add_argument("--big-blind", type=int, default=20)
    parser.add_argument("--time-limit", type=float, default=10.0)
    parser.add_argument("--blind-increase", type=int, default=10)

    args = parser.parse_args()

    settings = TournamentSettings(
        tournament_type=TournamentType.FREEZE_OUT,
        starting_chips=args.starting_chips,
        small_blind=args.small_blind,
        big_blind=args.big_blind,
        time_limit_per_action=args.time_limit,
        blind_increase_interval=args.blind_increase,
    )

    runner = TournamentRunner(settings, args.players_dir)
    results = runner.run_tournament()
    print(f"Winner: {results['final_standings'][0][0]}")


if __name__ == "__main__":
    main()
