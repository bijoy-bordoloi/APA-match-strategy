import json
import os
from session_loader import get_session_context
from strategies import AggressiveStrategy, NeutralStrategy
from mistralstrategy import MistralStrategy
from groqstrategy import GroqStrategy
from colors import COLORS

class APAMatchEngine:
    def __init__(self, config_path="config.json"):
        self.groq_api_key = os.getenv("GROQ_API_KEY")

        print(f"{COLORS.BOLD}APA 8-Ball Strategic Engine{COLORS.END}")
        week_num = int(input("Select Match Week: "))

        ctx = get_session_context(week_num)

        # --- Resolve guest/new players not yet in the roster ---
        guests = ctx.get('avl_guest_names', [])
        if guests:
            print(f"\n{COLORS.YELLOW}⚠  The following scheduled player(s) are not in the roster:{COLORS.END}")
            for first_name in guests:
                print(f"   {COLORS.BOLD}{first_name}{COLORS.END} — treating as guest/new player")
                sl_str = self.get_valid_input(
                    f"   Enter Skill Level for {COLORS.BOLD}{first_name}{COLORS.END} (1-7): ",
                    lambda x: x.isdigit() and 1 <= int(x) <= 7,
                    "Please enter a number between 1 and 7."
                )
                sl = int(sl_str)
                # Use first name as display name until they're formally added to the roster
                ctx['full_avl_roster'][first_name] = sl
                ctx['avl_scheduled'][first_name]   = sl
            print()

        self.match_context = ctx

        # --- Session Mode Selection ---
        print(f"\n{COLORS.BOLD}Session Mode:{COLORS.END}")
        print(f" {COLORS.CYAN}(1){COLORS.END} Regular Season  — all 5 matches played, maximise total points")
        print(f" {COLORS.CYAN}(2){COLORS.END} Playoff         — win 3 of 5, early exit when decided")
        mode_choice = self.get_valid_input("Select mode: ", lambda x: x in ["1", "2"], "Enter 1 or 2.")
        self.is_playoff = (mode_choice == "2")
        mode_label = f"{COLORS.YELLOW}⚔  PLAYOFF{COLORS.END}" if self.is_playoff else f"{COLORS.GREEN}🏆 REGULAR SEASON{COLORS.END}"
        print(f">> Mode: {mode_label}\n")

        # --- BRIEFING SUMMARY ---
        print("\n" + "="*40)
        print(f"WEEK {ctx['week']} MATCHUP ({ctx['date']})")
        print(f"Location: {ctx['location']}")
        print(f"HOME: {ctx['home_team']}")
        print(f"AWAY: {ctx['away_team']}")
        print("-" * 40)

        print(f"{COLORS.RED}OPPONENT ROSTER ({ctx['opponent_name']}):{COLORS.END}")
        for name, sl in ctx['opponent_roster'].items():
            print(f"  - {name} (SL{sl})")

        print(f"\n{COLORS.GREEN}ANTI VILLAIN LEAGUE (Scheduled):{COLORS.END}")
        for name, sl in ctx['avl_scheduled'].items():
            print(f"  - {name} (SL{sl})")
        print("="*40 + "\n")

        # Initialize State
        self.our_team = ctx['full_avl_roster']
        self.their_team = ctx['opponent_roster']

        self.strategy = None
        self.played_ours = []
        self.played_theirs = []
        self.total_points = 0
        self.total_sl_used = 0
        self.their_sl_used = 0
        self.our_dp_happened = False
        self.their_dp_happened = False
        self.match_history = []

    def get_valid_input(self, prompt, validation_fn=None, error_msg="Invalid input.", default=None):
        while True:
            display_prompt = f"{prompt} [{COLORS.CYAN}{default}{COLORS.END}]: " if default else prompt
            val = input(display_prompt).strip()

            if not val and default:
                return default
            if not val:
                continue

            if validation_fn and not validation_fn(val):
                print(f"{COLORS.RED}>> {error_msg}{COLORS.END}")
                continue
            return val

    def _player_status(self, name, eligible_names):
        """Return (tag, color) for our team players."""
        counts = {}
        for n, _ in self.played_ours:
            counts[n] = counts.get(n, 0) + 1
        play_count = counts.get(name, 0)

        if name not in eligible_names:
            if play_count >= 2:
                return "[2x-played]", COLORS.RED
            if play_count == 1 and self.our_dp_happened:
                return "[DP used]  ", COLORS.RED
            return "[ineligible]", COLORS.RED
        if play_count == 1:
            return "[played]   ", COLORS.YELLOW
        return "", ""

    def _opponent_status(self, name):
        """Return (tag, color) for opponent players based on their play history."""
        play_count = self.played_theirs.count(name)
        if play_count >= 2:
            return "[2x-played]", COLORS.RED
        if play_count == 1 and self.their_dp_happened:
            return "[DP used]  ", COLORS.RED
        if play_count == 1:
            return "[played]   ", COLORS.YELLOW
        return "", ""

    def print_our_roster_panel(self, eligible_names=None):
        """Roster overview panel shown once per match — not the interactive picker."""
        if eligible_names is None:
            eligible_names = set(self.get_eligible(for_suggestion=False).keys())
        scheduled_names = set(self.match_context.get('avl_scheduled', {}).keys())

        print(f"\n{COLORS.BOLD}┌─── AVL ROSTER ──────────────────────────────────┐{COLORS.END}")
        for name, sl in self.our_team.items():
            star = f"{COLORS.CYAN}★{COLORS.END}" if name in scheduled_names else " "
            tag, color = self._player_status(name, eligible_names)
            avail = f"{color}{tag}{COLORS.END}" if color else ""
            print(f"{COLORS.BOLD}│{COLORS.END} {star} {name:<24} SL-{sl}  {avail}")
        print(f"{COLORS.BOLD}└─────────────────────────────────────────────────┘{COLORS.END}")
        print(f"  {COLORS.CYAN}★{COLORS.END} = scheduled   "
              f"{COLORS.YELLOW}[played]{COLORS.END} = DP available   "
              f"{COLORS.RED}[ineligible/2x]{COLORS.END} = locked\n")

    def select_player_from_list(self, full_roster, eligible_dict, prompt,
                                default_name=None, show_status=True, is_opponent=False):
        """Display full roster with status; only allow selection from eligible_dict."""
        eligible_names  = set(eligible_dict.keys())
        scheduled_names = set(self.match_context.get('avl_scheduled', {}).keys())
        options_map     = {}

        print(f"\n{COLORS.BOLD}  #   Player                  SL   Status{COLORS.END}")
        print("  " + "─" * 48)

        sel_idx = 1
        for name, sl in full_roster.items():
            if is_opponent:
                tag, color = self._opponent_status(name)
            else:
                tag, color = self._player_status(name, eligible_names) if show_status else ("", "")

            star = f"{COLORS.CYAN}★{COLORS.END}" if (show_status and name in scheduled_names) else " "

            if name in eligible_names:
                num_str = f"{COLORS.BOLD}{sel_idx:>2}{COLORS.END}"
                options_map[str(sel_idx)] = name
                options_map[name.lower()] = name
                sel_idx += 1
            else:
                num_str = "  "

            status_str = f"{color}{tag}{COLORS.END}" if color else ""
            print(f"  {num_str}  {star} {name:<24} {sl:<2}  {status_str}")

        print()

        def validate(val):
            return val.lower() in options_map or val in options_map

        choice = self.get_valid_input(prompt, validate,
                                      "Select an eligible player by name or number.",
                                      default=default_name)
        key = choice if choice.isdigit() else choice.lower()
        return options_map.get(key)

    def get_eligible(self, for_suggestion=True):
        matches_left = 5 - len(self.played_ours)
        room = 23 - self.total_sl_used - ((matches_left - 1) * 2)

        counts = {}
        for name, _ in self.played_ours:
            counts[name] = counts.get(name, 0) + 1

        scheduled_names = set(self.match_context.get('avl_scheduled', {}).keys())

        eligible = {}
        for name, sl in self.our_team.items():
            count = counts.get(name, 0)
            if for_suggestion and name not in scheduled_names: continue
            if for_suggestion and count >= 1: continue
            if not for_suggestion:
                if count >= 2: continue
                if count == 1 and self.our_dp_happened: continue
            if sl <= room:
                eligible[name] = sl
        return eligible

    def check_double_play(self, name, team_list, is_ours):
        count = team_list.count(name)
        if count == 1:
            dp_flag = self.our_dp_happened if is_ours else self.their_dp_happened
            if dp_flag:
                print(f"!!! {COLORS.RED}{name} cannot play. Double play already used.{COLORS.END}")
                return False, False
            confirm = self.get_valid_input(
                f"Is {COLORS.BOLD}{name}{COLORS.END} a double play? (1)Yes (2)No: ",
                lambda x: x in ["1", "2"]
            )
            return (True, True) if confirm == "1" else (False, False)
        return True, False

    def select_strategy(self):
        print(f"\n{COLORS.BOLD}Strategy Options:{COLORS.END}")
        print(" (1) Aggressive\n (2) Neutral\n (3) Groq\n (4) AI Coach")
        choice = self.get_valid_input("I choose: ", lambda x: x in ["1", "2", "3", "4"], "Enter 1-4.")

        if choice == "1":
            self.strategy = AggressiveStrategy(is_playoff=self.is_playoff)
        elif choice == "2":
            self.strategy = NeutralStrategy(is_playoff=self.is_playoff)
        elif choice == "3":
            self.strategy = GroqStrategy(
                api_key=self.groq_api_key,
                match_context=self.match_context,
                is_playoff=self.is_playoff
            )
        else:
            self.strategy = MistralStrategy()
        print(f"{COLORS.GREEN}>> Strategy Activated.{COLORS.END}\n")

    def _should_end_early(self):
        """In playoff mode only, exit as soon as the match result is decided."""
        if not self.is_playoff:
            return False
        our_wins = sum(1 for m in self.match_history if m['won'])
        their_wins = len(self.match_history) - our_wins
        matches_played = len(self.match_history)
        matches_remaining = 5 - matches_played
        # End early if either side has clinched (unreachable for opponent to catch up / we can't win)
        return our_wins >= 3 or their_wins >= 3 or (our_wins + matches_remaining < 3)

    def run_session(self):
        self.select_strategy()
        first_move = self.get_valid_input(
            f"{COLORS.BOLD}[Match 1] Throwing (1) or Matching (2)?{COLORS.END} ",
            lambda x: x in ["1", "2"]
        )
        we_throw_first = (first_move == "1")

        for m_idx in range(1, 6):
            is_throwing = we_throw_first if m_idx % 2 != 0 else not we_throw_first
            strat_eligible = self.get_eligible(for_suggestion=True)
            legal_eligible = self.get_eligible(for_suggestion=False)

            print(f"\n{COLORS.BLUE}{'='*15} MATCH {m_idx}: {'THROWING' if is_throwing else 'MATCHING'} {'='*15}{COLORS.END}")
            print(f"AVL SL Total:      {COLORS.BOLD}{self.total_sl_used}/23{COLORS.END}")
            print(f"Opponent SL Total: {COLORS.BOLD}{self.their_sl_used}/23{COLORS.END}")
            self.print_our_roster_panel(eligible_names=set(legal_eligible.keys()))

            if is_throwing:
                sugg = self.strategy.suggest_throw(strat_eligible, self.their_team, self.total_sl_used)
                print(f"Strategic Suggestion: {COLORS.GREEN}{COLORS.BOLD}{sugg}{COLORS.END}")

                our_p = self.select_player_from_list(self.our_team, legal_eligible, "Player YOU threw: ", default_name=sugg)
                valid_p, is_dp = self.check_double_play(our_p, [m[0] for m in self.played_ours], True)
                if is_dp: self.our_dp_happened = True

                their_p = self.select_player_from_list(self.their_team, self.their_team, f"Opponent for {COLORS.BOLD}{our_p}{COLORS.END}: ", is_opponent=True)
                _, their_is_dp = self.check_double_play(their_p, self.played_theirs, False)
                if their_is_dp: self.their_dp_happened = True
            else:
                their_p = self.select_player_from_list(self.their_team, self.their_team, "Who did THEY throw? ", is_opponent=True)
                _, their_is_dp = self.check_double_play(their_p, self.played_theirs, False)
                if their_is_dp: self.their_dp_happened = True

                sugg = self.strategy.suggest_counter(strat_eligible, their_p, self.their_team[their_p], self.total_sl_used)
                print(f"Strategic Suggestion: {COLORS.GREEN}{COLORS.BOLD}{sugg}{COLORS.END}")

                our_p = self.select_player_from_list(self.our_team, legal_eligible, "Your Counter: ", default_name=sugg)
                valid_p, is_dp = self.check_double_play(our_p, [m[0] for m in self.played_ours], True)
                if is_dp: self.our_dp_happened = True

            print("\nMatch: " + our_p + " vs " + their_p)
            print("\nSelect Match Score (Our, Their): ")
            score_options = {"1": (3, 0), "2": (2, 0), "3": (2, 1), "4": (1, 2), "5": (0, 2), "6": (0, 3)}
            for k, v in score_options.items():
                print(f" {COLORS.BOLD}{k}{COLORS.END}. {v[0]}-{v[1]}", end=" ")

            score_choice = self.get_valid_input("\nMatch Score: ", lambda x: x in score_options.keys())
            our_pts, their_pts = score_options[score_choice]

            self.total_points += our_pts
            self.total_sl_used += self.our_team[our_p]
            self.played_ours.append((our_p, our_pts))
            self.played_theirs.append(their_p)
            self.their_sl_used += self.their_team[their_p]

            self.match_history.append({
                "our_player": our_p,
                "their_player": their_p,
                "sl": self.our_team[our_p],
                "our_pts": our_pts,
                "their_pts": their_pts,
                "won": our_pts >= 2
            })

            if self._should_end_early():
                our_wins = sum(1 for m in self.match_history if m['won'])
                their_wins = len(self.match_history) - our_wins
                winner = "Anti-Villain League" if our_wins >= 3 else "Opponent"
                print(f"\n{COLORS.GREEN}Playoff clinched after {m_idx} matches — {winner} wins the round!{COLORS.END}")
                break

        self.display_results()

    def display_results(self):
        our_wins = sum(1 for m in self.match_history if m['won'])
        their_wins = len(self.match_history) - our_wins
        our_total_pts = sum(m['our_pts'] for m in self.match_history)
        their_total_pts = sum(m['their_pts'] for m in self.match_history)

        print("\n" + "="*65)
        header = "🏁 PLAYOFF RESULT" if self.is_playoff else "🏆 REGULAR SEASON RESULT"
        print(f"{COLORS.BOLD}{header:^65}{COLORS.END}")
        print("="*65)

        print(f"{COLORS.BOLD}{'#':<4} | {'Our Player':<15} | {'Opponent':<15} | {'SL':<4} | {'Score':<7} | {'Result'}{COLORS.END}")
        print("-" * 65)

        for i, m in enumerate(self.match_history, 1):
            status = f"{COLORS.GREEN}✅ WIN{COLORS.END}" if m['won'] else f"{COLORS.RED}❌ LOSS{COLORS.END}"
            score = f"{m['our_pts']}-{m['their_pts']}"
            print(f"{i:<4} | {COLORS.BOLD}{m['our_player']:<15}{COLORS.END} | {COLORS.BOLD}{m['their_player']:<15}{COLORS.END} | {m['sl']:<4} | {score:<7} | {status}")

        print("-" * 65)

        if self.is_playoff:
            victory = our_wins >= 3
            result_color = COLORS.GREEN if victory else COLORS.RED
            result_text = "VICTORY" if victory else "DEFEAT"
            print(f"MATCH WINS:  {COLORS.BOLD}AVL {our_wins} – {their_wins} Opponent{COLORS.END}")
            print(f"TOTAL SL:    {COLORS.BOLD}{self.total_sl_used} / 23{COLORS.END}")
            print(f"\n{result_color}{'='*20} {result_text} {'='*20}{COLORS.END}\n")
        else:
            # Regular season: points are what matters for standings
            max_possible = len(self.match_history) * 3
            efficiency = (our_total_pts / max_possible * 100) if max_possible else 0
            result_color = COLORS.GREEN if our_total_pts > their_total_pts else COLORS.RED
            print(f"TOTAL POINTS: {COLORS.BOLD}AVL {our_total_pts} – {their_total_pts} Opponent{COLORS.END}")
            print(f"MATCH WINS:   {COLORS.BOLD}AVL {our_wins} – {their_wins} Opponent{COLORS.END}")
            print(f"POINT EFF.:   {COLORS.BOLD}{efficiency:.0f}%{COLORS.END} of max possible")
            print(f"TOTAL SL:     {COLORS.BOLD}{self.total_sl_used} / 23{COLORS.END}")
            outcome = "POINTS WIN" if our_total_pts > their_total_pts else ("TIED" if our_total_pts == their_total_pts else "POINTS LOSS")
            print(f"\n{result_color}{'='*20} {outcome} {'='*20}{COLORS.END}\n")


if __name__ == '__main__':
    APAMatchEngine().run_session()