import openai # Example using OpenAI

class AIAgentStrategy(BaseStrategy):
    def __init__(self, api_key):
        self.api_key = api_key
        openai.api_key = api_key

    def _ask_llm(self, prompt):
        # This sends the match state to the AI
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "system", "content": "You are a world-class APA 8-ball coach."},
                      {"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content

    def suggest_throw(self, eligible_ours, rem_theirs, total_sl_used):
        prompt = f"""
        Current Match State:
        - Our Available Players (Name: SL): {eligible_ours}
        - Their Available Players: {rem_theirs}
        - Total SL Used: {total_sl_used}/23
        
        We have to THROW first. Who is the best bait to lead with? 
        Return only the player name.
        """
        return self._ask_llm(prompt)

    def suggest_counter(self, eligible_ours, their_player_name, their_player_sl, total_sl_used):
        prompt = f"""
        Opponent threw: {their_player_name} (SL {their_player_sl})
        Our Available Players: {eligible_ours}
        Total SL Used: {total_sl_used}/23
        
        Who should we match against them to maximize our points or neutralize their threat?
        Return only the player name.
        """
        return self._ask_llm(prompt)
