# strategies.py

class BaseStrategy:
    """Interface for different team strategies."""
    def suggest_throw(self, eligible_ours, rem_theirs, total_sl_used):
        raise NotImplementedError

    def suggest_counter(self, eligible_ours, their_player_name, their_player_sl, total_sl_used):
        raise NotImplementedError

class NeutralStrategy(BaseStrategy):
    def suggest_throw(self, eligible_ours, rem_theirs, total_sl_used):
        # Suggest "Bait": Middle SL available
        names = sorted(eligible_ours, key=lambda x: eligible_ours[x])
        return names[len(names)//2] if names else "No suggestion"

    def suggest_counter(self, eligible_ours, their_player_name, their_player_sl, total_sl_used):
        if not eligible_ours: return "No suggestion"
        # High SL Opponent -> Sacrifice Low SL / Low SL Opponent -> Dominate High SL
        if their_player_sl >= 6:
            return min(eligible_ours, key=lambda x: eligible_ours[x])
        else:
            return max(eligible_ours, key=lambda x: eligible_ours[x])

class AggressiveStrategy(BaseStrategy):
    def suggest_throw(self, eligible_ours, rem_theirs, total_sl_used):
        return max(eligible_ours, key=lambda x: eligible_ours[x]) if eligible_ours else "No suggestion"

    def suggest_counter(self, eligible_ours, their_player_name, their_player_sl, total_sl_used):
        return max(eligible_ours, key=lambda x: eligible_ours[x]) if eligible_ours else "No suggestion"
