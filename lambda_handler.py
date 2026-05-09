import json
import os
from engine import APAMatchEngine


def handler(event, context):
    """
    AWS Lambda handler for APA Match Strategy Engine

    event: {
        "week": int,
        "strategy": "aggressive|neutral|groq|mistral",
        "first_move": "throw|match"
    }
    """
    try:
        week = event.get("week", 1)
        strategy_choice = event.get("strategy", "neutral").lower()
        first_move = event.get("first_move", "throw")

        # Initialize engine
        engine = APAMatchEngine(config_path="config.json")
        engine.match_context['week'] = week
        engine.is_playoff = event.get("is_playoff", False)

        # Set strategy
        strategies_map = {
            "aggressive": "1",
            "neutral": "2",
            "groq": "3",
            "mistral": "4"
        }
        strategy_num = strategies_map.get(strategy_choice, "2")

        # Simulate strategy selection
        if strategy_num == "1":
            from strategies import AggressiveStrategy
            engine.strategy = AggressiveStrategy(is_playoff=engine.is_playoff)
        elif strategy_num == "2":
            from strategies import NeutralStrategy
            engine.strategy = NeutralStrategy(is_playoff=engine.is_playoff)
        elif strategy_num == "3":
            from groqstrategy import GroqStrategy
            engine.strategy = GroqStrategy(
                api_key=engine.groq_api_key,
                match_context=engine.match_context,
                is_playoff=engine.is_playoff
            )
        else:
            from mistralstrategy import MistralStrategy
            engine.strategy = MistralStrategy()

        we_throw_first = (first_move.lower() == "throw")

        # Run simulation
        for m_idx in range(1, 6):
            is_throwing = we_throw_first if m_idx % 2 != 0 else not we_throw_first
            strat_eligible = engine.get_eligible(for_suggestion=True)

            if not strat_eligible:
                break

            if is_throwing:
                sugg = engine.strategy.suggest_throw(strat_eligible, engine.their_team, engine.total_sl_used)
                our_p = sugg
            else:
                their_p = list(engine.their_team.keys())[0]
                sugg = engine.strategy.suggest_counter(strat_eligible, their_p, engine.their_team[their_p], engine.total_sl_used)
                our_p = sugg

            if our_p not in engine.our_team:
                our_p = list(strat_eligible.keys())[0]

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Match session processed",
                "week": week,
                "strategy": strategy_choice,
                "match_context": engine.match_context,
                "total_points": engine.total_points,
                "total_sl_used": engine.total_sl_used
            })
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": str(e)
            })
        }
