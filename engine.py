
import json
import os
from strategies import AggressiveStrategy, NeutralStrategy
from mistralstrategy import MistralStrategy
from groqstrategy import GroqStrategy
from colors import COLORS

class APAMatchEngine:
    def __init__(self, config_path="config.json"):
        # Load the Groq key from environment variable
        self.groq_api_key = os.getenv("GROQ_API_KEY")
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                self.our_team = config['our_team']
                self.their_team = config['their_team']
        except FileNotFoundError:
            print(f"{COLORS.RED}Error: {config_path} not found. Please create it.{COLORS.END}")
            exit(1)
            
        self.strategy = None
        self.played_ours = []   
        self.played_theirs = [] 
        self.total_points = 0
        self.total_sl_used = 0
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

    def select_player_from_list(self, player_dict, prompt, default_name=None):
        options_map = {}
        print(f"\n{COLORS.BOLD}Available Players:{COLORS.END}")
        
        for idx, (name, sl) in enumerate(player_dict.items(), 1):
            print(f" {COLORS.BOLD}{idx}{COLORS.END}. {name}(SL-{sl})", end=" ")
            options_map[str(idx)] = name
            options_map[name.lower()] = name
            
        print("\n")
        
        def validate(val):
            return val.lower() in options_map or val in options_map

        choice = self.get_valid_input(prompt, validate, "Select by name or number.", default=default_name)
        key = choice.isdigit() and choice or choice.lower()
        return options_map.get(key)

    def get_eligible(self, for_suggestion=True):
        matches_left = 5 - len(self.played_ours)
        room = 23 - self.total_sl_used - ((matches_left - 1) * 2)
        
        counts = {}
        for name, _ in self.played_ours:
            counts[name] = counts.get(name, 0) + 1
            
        eligible = {}
        for name, sl in self.our_team.items():
            count = counts.get(name, 0)
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
            confirm = self.get_valid_input(f"Is {COLORS.BOLD}{name}{COLORS.END} a double play? (1)Yes (2)No: ", lambda x: x in ["1", "2"])
            return (True, True) if confirm == "1" else (False, False)
        return True, False

    def select_strategy(self):
        print(f"\n{COLORS.BOLD}Strategy Options:{COLORS.END}")
        print(" (1) Aggressive\n (2) Neutral\n (3) Groq\n (4) AI Coach")
        choice = self.get_valid_input("I choose: ", lambda x: x in ["1", "2", "3", "4"], "Enter 1-4.")
    
        if choice == "1":
            self.strategy = AggressiveStrategy()
        elif choice == "2":
            self.strategy = NeutralStrategy()
        elif choice == "3":
            self.strategy = GroqStrategy(api_key=self.groq_api_key)
        else:
            self.strategy = MistralStrategy()
        print(f"{COLORS.GREEN}>> Strategy Activated.{COLORS.END}\n")

    def run_session(self):
        self.select_strategy()
        first_move = self.get_valid_input(f"{COLORS.BOLD}[Match 1] Throwing (1) or Matching (2)?{COLORS.END} ", lambda x: x in ["1", "2"])
        we_throw_first = (first_move == "1")

        for m_idx in range(1, 6):
            is_throwing = we_throw_first if m_idx % 2 != 0 else not we_throw_first
            strat_eligible = self.get_eligible(for_suggestion=True)
            legal_eligible = self.get_eligible(for_suggestion=False)
            
            print(f"\n{COLORS.BLUE}{'='*15} MATCH {m_idx}: {'THROWING' if is_throwing else 'MATCHING'} {'='*15}{COLORS.END}")
            print(f"Current SL Total: {COLORS.BOLD}{self.total_sl_used}/23{COLORS.END}")

            if is_throwing:
                sugg = self.strategy.suggest_throw(strat_eligible, self.their_team, self.total_sl_used)
                print(f"Strategic Suggestion: {COLORS.GREEN}{COLORS.BOLD}{sugg}{COLORS.END}")
                
                our_p = self.select_player_from_list(legal_eligible, "Player YOU threw: ", default_name=sugg)
                valid_p, is_dp = self.check_double_play(our_p, [m[0] for m in self.played_ours], True)
                if is_dp: self.our_dp_happened = True
                
                their_p = self.select_player_from_list(self.their_team, f"Opponent for {COLORS.BOLD}{our_p}{COLORS.END}: ")
            else:
                their_p = self.select_player_from_list(self.their_team, "Who did THEY throw? ")
                
                sugg = self.strategy.suggest_counter(strat_eligible, their_p, self.their_team[their_p], self.total_sl_used)
                print(f"Strategic Suggestion: {COLORS.GREEN}{COLORS.BOLD}{sugg}{COLORS.END}")

                our_p = self.select_player_from_list(legal_eligible, "Your Counter", default_name=sugg)
                valid_p, is_dp = self.check_double_play(our_p, [m[0] for m in self.played_ours], True)
                if is_dp: self.our_dp_happened = True

            print("\nSelect Match Score (Our, Their): ")
            score_options = {"1": (3, 0), "2": (2, 0), "3": (2, 1), "4": (1, 2), "5": (0, 2), "6": (0, 3)}
            for k, v in score_options.items():
                print(f" {COLORS.BOLD}{k}{COLORS.END}. {v[0]}-{v[1]}", end=" ")

            score_choice = self.get_valid_input("\nI choose: ", lambda x: x in score_options.keys())
            our_pts, their_pts = score_options[score_choice]

            self.total_points += our_pts
            self.total_sl_used += self.our_team[our_p]
            self.played_ours.append((our_p, our_pts))
            self.played_theirs.append(their_p)
            
            self.match_history.append({
                "our_player": our_p,
                "their_player": their_p,
                "sl": self.our_team[our_p],
                "won": our_pts >= 2
            })

            if self.total_points >= 8: 
                print(f"\n{COLORS.GREEN}Points Threshold Met!{COLORS.END}")
                break
                
        self.display_playoff_results()

    def display_playoff_results(self):
        print("\n" + "="*60)
        print(f"{COLORS.BOLD}{'🏁 PLAYOFF MATCH COMPLETE':^60}{COLORS.END}")
        print("="*60)
        
        our_total_wins = sum(1 for m in self.match_history if m['won'])
        opponent_total_wins = len(self.match_history) - our_total_wins
        
        print(f"{COLORS.BOLD}{'ID':<4} | {'Our Player':<15} | {'Opponent':<15} | {'SL':<4} | {'Result'}{COLORS.END}")
        print("-" * 60)
        
        for i, m in enumerate(self.match_history, 1):
            status = f"{COLORS.GREEN}✅ WIN{COLORS.END}" if m['won'] else f"{COLORS.RED}❌ LOSS{COLORS.END}"
            print(f"{i:<4} | {COLORS.BOLD}{m['our_player']:<15}{COLORS.END} | {COLORS.BOLD}{m['their_player']:<15}{COLORS.END} | {m['sl']:<4} | {status}")
        
        print("-" * 60)
        
        victory = our_total_wins >= 3
        result_color = COLORS.GREEN if victory else COLORS.RED
        result_text = "VICTORY" if victory else "DEFEAT"

        print(f"FINAL SCORE: {COLORS.BOLD}Anti-Villain League {our_total_wins} - {opponent_total_wins} Satire Squad{COLORS.END}")
        print(f"TOTAL SL:    {COLORS.BOLD}{self.total_sl_used} / 23{COLORS.END}")
        print(f"\n{result_color}{'='*20} {result_text} {'='*20}{COLORS.END}\n")

if __name__ == '__main__':
    APAMatchEngine().run_session()
