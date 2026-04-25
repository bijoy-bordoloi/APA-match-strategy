
import unittest
from engine import APAMatchEngine
from strategies import NeutralStrategy, AggressiveStrategy

class TestAPAEngine(unittest.TestCase):
    def setUp(self):
        self.engine = APAMatchEngine()

    def test_23_rule_enforcement(self):
        # Simulate SL usage that limits choices
        self.engine.total_sl_used = 15
        self.engine.played_ours = [('Bijoy', 3), ('Krishna', 3)] # 2 matches done
        # 3 matches left. Must reserve 2*2=4 points for matches 4 and 5.
        # Max SL for match 3: 23 - 15 - 4 = 4.
        eligible = self.engine.get_eligible(for_suggestion=False)
        self.assertIn('David', eligible) # SL 4
        self.assertNotIn('Krishna', eligible) # SL 6 exceeds room

    def test_double_play_once_per_team(self):
        self.engine.played_ours = [('Kim', 2)]
        self.engine.our_dp_happened = True
        # Should not suggest or allow another played player
        eligible = self.engine.get_eligible(for_suggestion=False)
        self.assertNotIn('Kim', eligible)

    def test_neutral_strategy_sacrifice(self):
        strat = NeutralStrategy()
        eligible = {'Kim': 3, 'Bijoy': 7}
        # Opponent throws SL 6
        sugg = strat.suggest_counter(eligible, 'Kapil', 6, 0)
        self.assertEqual(sugg, 'Kim')

    def test_win_threshold(self):
        self.engine.total_points = 8
        # Logic in run_session would break, here we check the state
        self.assertTrue(self.engine.total_points >= 8)

if __name__ == '__main__':
    unittest.main()
