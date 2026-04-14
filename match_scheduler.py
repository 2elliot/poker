"""
Background Match Scheduler
Runs poker matches automatically and records results for ranking.
"""
import threading
import time
import random
import logging
import os
import json
from datetime import datetime
from typing import List, Dict, Optional

from backend.tournament import TournamentSettings, TournamentType, PokerTournament
from backend.bot_manager import BotManager, BotWrapper, BOT_TURN_TIMEOUT
from backend.engine.poker_game import PokerGame, PlayerAction
from secure_bot_storage import SecureBotStorage


class MatchScheduler:
    """Runs automated matches between bots in a background thread."""

    def __init__(self, bot_storage: SecureBotStorage, master_password: str,
                 stats_file: str = "match_stats.json",
                 min_bots: int = 2, table_size: int = 6,
                 hands_per_match: int = 200):
        self.bot_storage = bot_storage
        self.master_password = master_password
        self.stats_file = stats_file
        self.min_bots = min_bots
        self.table_size = table_size
        self.hands_per_match = hands_per_match

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self.logger = logging.getLogger("match_scheduler")
        self.stats = self._load_stats()

        # Current live match state (for spectator mode)
        self.live_match: Optional[Dict] = None
        # Event buffer for spectator replay — list of dicts
        self._live_events: List[Dict] = []
        self._event_seq: int = 0
        self._pending_reset: bool = False

    # ------------------------------------------------------------------
    # Stats persistence
    # ------------------------------------------------------------------

    def _load_stats(self) -> Dict:
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {"bots": {}, "matches": [], "match_count": 0}

    def _save_stats(self):
        with self._lock:
            tmp = self.stats_file + ".tmp"
            with open(tmp, 'w') as f:
                json.dump(self.stats, f, indent=2)
            os.replace(tmp, self.stats_file)

    def _ensure_bot_entry(self, name: str):
        if name not in self.stats["bots"]:
            self.stats["bots"][name] = {
                "elo": 1200,
                "hands_played": 0,
                "hands_won": 0,
                "chips_won": 0,
                "chips_lost": 0,
                "tournaments_played": 0,
                "tournaments_won": 0,
                "vpip_hands": 0,        # voluntarily put $ in pot
                "pfr_hands": 0,         # pre-flop raise
                "total_preflop_hands": 0,
                "last_played": None
            }

    # ------------------------------------------------------------------
    # Elo helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _elo_expected(ra: float, rb: float) -> float:
        return 1.0 / (1.0 + 10 ** ((rb - ra) / 400))

    def _get_k_factor(self, bot_name: str) -> float:
        """
        Adaptive K-factor: high for new bots so they find their level fast,
        low for established bots so rankings stay stable.
        K=16 at 0 matches, decays to 2 after ~30 matches.
        """
        tp = self.stats["bots"].get(bot_name, {}).get("tournaments_played", 0)
        return max(2, 16 * (0.93 ** tp))

    def _elo_update_match(self, results: List[tuple]):
        """
        Update Elo ratings once per match based on final placement.
        results: [(name, chips, position), ...] sorted by position (1 = winner).
        Each pair is compared; the higher-placed bot 'wins'.
        """
        for i in range(len(results)):
            for j in range(i + 1, len(results)):
                a = results[i][0]
                b = results[j][0]
                pos_a = results[i][2]
                pos_b = results[j][2]

                ra = self.stats["bots"][a]["elo"]
                rb = self.stats["bots"][b]["elo"]
                ea = self._elo_expected(ra, rb)
                eb = 1 - ea

                if pos_a < pos_b:
                    sa, sb = 1, 0
                elif pos_a > pos_b:
                    sa, sb = 0, 1
                else:
                    sa, sb = 0.5, 0.5

                # Use the average K of both bots so the update is symmetric
                k = (self._get_k_factor(a) + self._get_k_factor(b)) / 2

                self.stats["bots"][a]["elo"] = round(ra + k * (sa - ea), 1)
                self.stats["bots"][b]["elo"] = round(rb + k * (sb - eb), 1)

    # ------------------------------------------------------------------
    # mbb/hand helpers
    # ------------------------------------------------------------------

    def get_mbb_per_hand(self, bot_name: str) -> Optional[float]:
        """
        Calculate milli-big-blinds per hand.
        Positive = profitable, negative = losing.
        """
        b = self.stats["bots"].get(bot_name)
        if not b or b["hands_played"] < 100:
            return None
        net = b["chips_won"] - b["chips_lost"]
        # We use a fixed reference BB of 20 (the starting big blind)
        return round(net / b["hands_played"] / 20 * 1000, 1)

    # ------------------------------------------------------------------
    # Live event helpers (for spectator mode)
    # ------------------------------------------------------------------

    def _emit_event(self, event: Dict):
        """Append an event to the live event buffer for spectator consumption."""
        with self._lock:
            self._live_events.append(event)
            self._event_seq += 1
            event["seq"] = self._event_seq

    @staticmethod
    def _card_str(card) -> str:
        return str(card) if card else ""

    @staticmethod
    def _cards_list(cards) -> List[str]:
        return [str(c) for c in cards] if cards else []

    # ------------------------------------------------------------------
    # Single match runner (runs a full sit-and-go)
    # ------------------------------------------------------------------

    def _run_single_match(self, bot_names: List[str]):
        """Run one complete sit-and-go match and record results."""
        self.logger.info(f"Starting match: {', '.join(bot_names)}")

        # Load bot instances
        bot_manager = BotManager("players", BOT_TURN_TIMEOUT)
        bot_manager.bots = {}
        player_names = []

        for name in bot_names:
            instance = self.bot_storage.load_bot(name, self.master_password)
            if instance is None:
                self.logger.warning(f"Could not load bot {name}, skipping match")
                return
            player_names.append(name)
            bot_manager.bots[name] = BotWrapper(name, instance, BOT_TURN_TIMEOUT)

        if len(player_names) < 2:
            return

        settings = TournamentSettings(
            tournament_type=TournamentType.FREEZE_OUT,
            starting_chips=1000,
            small_blind=10,
            big_blind=20,
            blind_increase_interval=10,
            blind_increase_factor=1.5,
            max_players_per_table=len(player_names),
        )

        tournament = PokerTournament(player_names, settings)

        # Ensure stat entries exist
        for name in player_names:
            self._ensure_bot_entry(name)

        hand_count = 0

        # Reset live state for this match
        with self._lock:
            self._live_events = []
            self._event_seq = 0
        self.live_match = {
            "match_id": self.stats.get("match_count", 0) + 1,
            "players": player_names,
            "hand_number": 0,
            "total_hands": self.hands_per_match,
            "chips": {p: 1000 for p in player_names},
            "eliminated": [],
            "status": "playing",
        }

        self._emit_event({
            "event": "match_start",
            "players": player_names,
            "chips": {p: 1000 for p in player_names},
        })

        while not tournament.is_tournament_complete() and hand_count < self.hands_per_match:
            if self._stop_event.is_set():
                return

            # Get an active table
            table = None
            for t in tournament.tables.values():
                if len(t.get_active_players()) >= 2:
                    table = t
                    break

            if table is None:
                if len(tournament.get_active_players()) >= 2:
                    tournament.rebalance_tables()
                    for t in tournament.tables.values():
                        if len(t.get_active_players()) >= 2:
                            table = t
                            break
                if table is None:
                    break

            active_ids = table.get_active_players()
            small_blind, big_blind = table.get_current_blinds()
            bots = {pid: bot_manager.get_bot(pid) for pid in active_ids}

            game = PokerGame(
                bots,
                starting_chips=0,
                small_blind=small_blind,
                big_blind=big_blind,
                dealer_button_index=table.dealer_button % len(active_ids)
            )
            for p in active_ids:
                game.player_chips[p] = tournament.player_stats[p].chips

            chips_before = {p: game.player_chips[p] for p in active_ids}

            # Play hand through
            game.reset_hand()
            game.deal_hole_cards()
            game.post_blinds()
            game._start_betting_round()

            hand_count += 1

            # Emit deal event with hole cards (visible to spectators)
            hole_cards = {}
            for p in active_ids:
                h = game.get_player_hand(p)
                if h and h.cards:
                    hole_cards[p] = self._cards_list(h.cards)
            self._emit_event({
                "event": "deal",
                "hand_number": hand_count,
                "players": active_ids,
                "hole_cards": hole_cards,
                "chips": dict(game.player_chips),
                "bets": dict(game.player_bets),
                "pot": game.pot,
                "blinds": [small_blind, big_blind],
            })

            # Track VPIP/PFR during preflop
            preflop_actors = set()
            preflop_raisers = set()

            for phase in ['preflop', 'flop', 'turn', 'river']:
                if len(game.active_players) <= 1:
                    break
                if phase == 'flop':
                    game.deal_flop()
                    game.round_name = 'flop'
                    game._start_betting_round()
                    self._emit_event({
                        "event": "community",
                        "phase": "flop",
                        "cards": self._cards_list(game.community_cards),
                        "pot": game.pot,
                    })
                elif phase == 'turn':
                    game.deal_turn()
                    game.round_name = 'turn'
                    game._start_betting_round()
                    self._emit_event({
                        "event": "community",
                        "phase": "turn",
                        "cards": self._cards_list(game.community_cards),
                        "pot": game.pot,
                    })
                elif phase == 'river':
                    game.deal_river()
                    game.round_name = 'river'
                    game._start_betting_round()
                    self._emit_event({
                        "event": "community",
                        "phase": "river",
                        "cards": self._cards_list(game.community_cards),
                        "pot": game.pot,
                    })

                # Process all actions in the round
                safety = 0
                while not game.is_betting_round_complete() and safety < 100:
                    safety += 1
                    pid = game.get_current_player()
                    if not pid:
                        break
                    # Skip all-in players
                    if game.player_chips.get(pid, 0) == 0:
                        game.advance_to_next_player()
                        continue
                    if pid not in game.active_players:
                        game.advance_to_next_player()
                        continue

                    bot = game.player_bots.get(pid)
                    if not bot:
                        game.advance_to_next_player()
                        continue

                    gs = game.get_game_state()
                    hand = game.get_player_hand(pid)
                    if hand is None:
                        game.process_action(pid, PlayerAction.FOLD, 0)
                        game.advance_to_next_player()
                        continue

                    legal = game.get_legal_actions(gs, pid)
                    min_bet = gs.min_bet
                    max_bet = game.player_chips[pid] + game.player_bets[pid]
                    action, amount = bot.get_action(gs, hand.cards, legal, min_bet, max_bet)

                    # Track VPIP/PFR (preflop only)
                    if phase == 'preflop' and action in (PlayerAction.CALL, PlayerAction.RAISE, PlayerAction.ALL_IN):
                        preflop_actors.add(pid)
                        if action == PlayerAction.RAISE:
                            preflop_raisers.add(pid)

                    game.process_action(pid, action, amount)

                    # Emit action event
                    self._emit_event({
                        "event": "action",
                        "player": pid,
                        "action": action.name.lower(),
                        "amount": amount,
                        "pot": game.pot,
                        "chips": dict(game.player_chips),
                        "bets": dict(game.player_bets),
                    })

                    game.advance_to_next_player()

            # Showdown
            if len(game.active_players) > 1:
                winners = game.determine_winners()
            else:
                winners = game.active_players.copy()
            game._distribute_pot(winners)

            # Emit showdown event
            showdown_hands = {}
            for p in game.active_players:
                h = game.get_player_hand(p)
                if h and h.cards:
                    showdown_hands[p] = self._cards_list(h.cards)
            self._emit_event({
                "event": "showdown",
                "winners": winners,
                "player_hands": showdown_hands,
                "community_cards": self._cards_list(game.community_cards),
                "chips": dict(game.player_chips),
                "pot": 0,
            })

            # Update tournament state
            for pid, chips in game.player_chips.items():
                tournament.update_player_chips(pid, chips)
            table.dealer_button = (table.dealer_button + 1) % len(active_ids)
            tournament.advance_hand()

            # Record per-hand stats
            chips_after = {p: game.player_chips.get(p, 0) for p in active_ids}
            chip_deltas = {}
            for p in active_ids:
                delta = chips_after.get(p, 0) - chips_before.get(p, 0)
                chip_deltas[p] = delta
                b = self.stats["bots"][p]
                b["hands_played"] += 1
                if delta > 0:
                    b["hands_won"] += 1
                    b["chips_won"] += delta
                elif delta < 0:
                    b["chips_lost"] += abs(delta)
                b["total_preflop_hands"] += 1
                if p in preflop_actors:
                    b["vpip_hands"] += 1
                if p in preflop_raisers:
                    b["pfr_hands"] += 1

            # Update live match summary
            self.live_match = {
                "match_id": self.stats.get("match_count", 0) + 1,
                "players": player_names,
                "hand_number": hand_count,
                "total_hands": self.hands_per_match,
                "chips": {p: tournament.player_stats[p].chips for p in player_names},
                "eliminated": [p for p in player_names if p in tournament.eliminated_players],
                "status": "playing",
            }

            # Rebalance if needed
            if tournament.should_rebalance_tables():
                tournament.rebalance_tables()

        # Match complete — record tournament-level stats and update Elo once
        results = tournament.get_final_results()
        self._elo_update_match(results)

        for name, chips, position in results:
            b = self.stats["bots"][name]
            b["tournaments_played"] += 1
            if position == 1:
                b["tournaments_won"] += 1
            b["last_played"] = datetime.now().isoformat()

        # Save match summary
        self.stats["match_count"] += 1
        self.stats["matches"].append({
            "match_id": self.stats["match_count"],
            "date": datetime.now().isoformat(),
            "players": player_names,
            "hands_played": hand_count,
            "results": [(name, chips, pos) for name, chips, pos in results]
        })
        # Keep only last 200 match summaries
        self.stats["matches"] = self.stats["matches"][-200:]

        self._save_stats()

        # Emit match-end event and update live state
        winner = results[0][0] if results else None
        self._emit_event({
            "event": "match_end",
            "results": [{"name": n, "chips": c, "position": p} for n, c, p in results],
            "hands_played": hand_count,
            "winner": winner,
        })
        self.live_match["status"] = "complete"
        self.live_match["winner"] = winner
        self.live_match["hand_number"] = hand_count

        self.logger.info(f"Match complete: {hand_count} hands. Winner: {winner or 'none'}")

    # ------------------------------------------------------------------
    # Scheduling logic
    # ------------------------------------------------------------------

    def _pick_bots(self) -> List[str]:
        """Pick bots for the next match, prioritizing bots with fewer games."""
        available = [b["name"] for b in self.bot_storage.list_bots()]
        if len(available) < self.min_bots:
            return []

        table_size = min(self.table_size, len(available))

        # Sort by fewest hands played (bots needing more games first)
        def priority(name):
            entry = self.stats["bots"].get(name)
            return entry["hands_played"] if entry else 0

        available.sort(key=priority)

        # Take the bots with fewest games, add some randomness
        top = available[:table_size + 2]
        random.shuffle(top)
        return top[:table_size]

    def _scheduler_loop(self):
        """Main loop that runs in a background thread."""
        self.logger.info("Match scheduler started")
        while not self._stop_event.is_set():
            try:
                # Apply pending reset between matches (safe — no match in flight)
                if self._pending_reset:
                    self._apply_reset()

                bot_names = self._pick_bots()
                if len(bot_names) >= self.min_bots:
                    self._run_single_match(bot_names)
                else:
                    self.logger.debug("Not enough bots for a match, waiting...")

                # Wait between matches (shorter if few hands were played)
                self._stop_event.wait(5)

            except Exception as e:
                self.logger.error(f"Match scheduler error: {e}", exc_info=True)
                self._stop_event.wait(10)

        self.logger.info("Match scheduler stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self):
        """Start the background scheduler thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._scheduler_loop, daemon=True, name="match-scheduler")
        self._thread.start()
        self.logger.info("Match scheduler thread started")

    def stop(self):
        """Stop the scheduler."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=15)
        self.logger.info("Match scheduler thread stopped")

    def get_leaderboard(self) -> List[Dict]:
        """Get the current leaderboard sorted by Elo."""
        board = []
        for name, data in self.stats["bots"].items():
            mbb = self.get_mbb_per_hand(name)
            hp = data["hands_played"]
            board.append({
                "name": name,
                "elo": data["elo"],
                "mbb_per_hand": mbb,
                "hands_played": hp,
                "hands_won": data["hands_won"],
                "win_rate": round(data["hands_won"] / hp * 100, 1) if hp > 0 else 0,
                "tournaments_played": data["tournaments_played"],
                "tournaments_won": data["tournaments_won"],
                "vpip": round(data["vpip_hands"] / data["total_preflop_hands"] * 100, 1) if data["total_preflop_hands"] > 0 else 0,
                "pfr": round(data["pfr_hands"] / data["total_preflop_hands"] * 100, 1) if data["total_preflop_hands"] > 0 else 0,
                "calibrated": hp >= 5000,
                "last_played": data.get("last_played"),
            })
        board.sort(key=lambda x: x["elo"], reverse=True)
        return board

    def get_events_since(self, since_seq: int = 0, limit: int = 50) -> Dict:
        """
        Get live events since a given sequence number.
        Returns {"events": [...], "last_seq": N, "match": {...}}.
        The frontend polls this to replay hand events.
        """
        with self._lock:
            # Find events after since_seq
            new_events = [e for e in self._live_events if e.get("seq", 0) > since_seq]
            # Limit to avoid huge payloads
            new_events = new_events[:limit]
        return {
            "events": new_events,
            "last_seq": new_events[-1]["seq"] if new_events else since_seq,
            "match": self.live_match,
        }

    def reset_stats(self):
        """Schedule a stats reset. The scheduler loop picks this up between matches
        so we don't block the request or race with an in-flight match."""
        self._pending_reset = True
        self.logger.info("Leaderboard reset scheduled (will apply before next match)")

    def _apply_reset(self):
        """Actually perform the stats reset — called from the scheduler loop."""
        with self._lock:
            self.stats = {"bots": {}, "matches": [], "match_count": 0}
            self._save_stats()
            self.live_match = None
            self._live_events = []
            self._event_seq = 0

        # Clear wins/total_games in bot storage metadata
        for bot_name in list(self.bot_storage.metadata.get("bots", {}).keys()):
            self.bot_storage.metadata["bots"][bot_name]["wins"] = 0
            self.bot_storage.metadata["bots"][bot_name]["total_games"] = 0
        self.bot_storage._save_metadata()

        self._pending_reset = False
        self.logger.info("All leaderboard stats have been reset")

    def delete_bot_stats(self, bot_name: str):
        """Remove a single bot from the stats."""
        with self._lock:
            self.stats["bots"].pop(bot_name, None)
            self._save_stats()
        self.logger.info(f"Stats deleted for bot: {bot_name}")

    def get_bot_stats(self, bot_name: str) -> Optional[Dict]:
        """Get detailed stats for a single bot."""
        data = self.stats["bots"].get(bot_name)
        if not data:
            return None
        mbb = self.get_mbb_per_hand(bot_name)
        hp = data["hands_played"]
        return {
            "name": bot_name,
            "elo": data["elo"],
            "mbb_per_hand": mbb,
            "hands_played": hp,
            "hands_won": data["hands_won"],
            "win_rate": round(data["hands_won"] / hp * 100, 1) if hp > 0 else 0,
            "tournaments_played": data["tournaments_played"],
            "tournaments_won": data["tournaments_won"],
            "vpip": round(data["vpip_hands"] / data["total_preflop_hands"] * 100, 1) if data["total_preflop_hands"] > 0 else 0,
            "pfr": round(data["pfr_hands"] / data["total_preflop_hands"] * 100, 1) if data["total_preflop_hands"] > 0 else 0,
            "chips_won": data["chips_won"],
            "chips_lost": data["chips_lost"],
            "net_chips": data["chips_won"] - data["chips_lost"],
            "calibrated": hp >= 5000,
        }
