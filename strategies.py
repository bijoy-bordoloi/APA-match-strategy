# strategies.py

class BaseStrategy:
    """Interface for different team strategies.
    
    is_playoff: True  -> goal is 3 wins (early exit allowed)
                False -> goal is maximum points (all 5 matches played)
    """
    def __init__(self, is_playoff=False):
        self.is_playoff = is_playoff

    def suggest_throw(self, eligible_ours, rem_theirs, total_sl_used):
        raise NotImplementedError

    def suggest_counter(self, eligible_ours, their_player_name, their_player_sl, total_sl_used):
        raise NotImplementedError


class NeutralStrategy(BaseStrategy):
    def suggest_throw(self, eligible_ours, rem_theirs, total_sl_used):
        names = sorted(eligible_ours, key=lambda x: eligible_ours[x])
        if not names:
            return "No suggestion"
        if self.is_playoff:
            # Bait: throw mid-tier to read opponent before committing strong players
            return names[len(names) // 2]
        else:
            # Regular: throw strong to maximise points in every match
            return names[-1]

    def suggest_counter(self, eligible_ours, their_player_name, their_player_sl, total_sl_used):
        if not eligible_ours:
            return "No suggestion"
        if self.is_playoff:
            # Playoff: sacrifice low SL against a strong opponent to preserve stars
            if their_player_sl >= 6:
                return min(eligible_ours, key=lambda x: eligible_ours[x])
            else:
                return max(eligible_ours, key=lambda x: eligible_ours[x])
        else:
            # Regular: match as closely as possible to maximise win probability and points
            return min(eligible_ours, key=lambda x: abs(eligible_ours[x] - their_player_sl))


class AggressiveStrategy(BaseStrategy):
    def suggest_throw(self, eligible_ours, rem_theirs, total_sl_used):
        return max(eligible_ours, key=lambda x: eligible_ours[x]) if eligible_ours else "No suggestion"

    def suggest_counter(self, eligible_ours, their_player_name, their_player_sl, total_sl_used):
        if not eligible_ours:
            return "No suggestion"
        # Aggressive is always best available — playoff or regular
        return max(eligible_ours, key=lambda x: eligible_ours[x])