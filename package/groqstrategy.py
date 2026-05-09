import requests
from strategies import BaseStrategy
from prompts import COACH_SYSTEM_PROMPT


class GroqStrategy(BaseStrategy):
    def __init__(self, api_key, match_context=None, is_playoff=False):
        super().__init__(is_playoff=is_playoff)
        self.url = "https://api.groq.com/openai/v1/chat/completions"
        self.api_key = api_key
        self.model = "llama-3.1-8b-instant"
        self.match_context = match_context or {}

    def _query_groq(self, user_prompt):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": COACH_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.1
        }

        try:
            response = requests.post(self.url, headers=headers, json=payload, timeout=10)
            result = response.json()

            if 'choices' in result:
                return result['choices'][0]['message']['content'].strip()
            elif 'error' in result:
                return f"Groq API Error: {result['error'].get('message', 'Unknown error')}"

            return f"Unexpected Response: {str(result)}"

        except Exception as e:
            return f"Connection Error: {str(e)}"

    def _build_context_block(self):
        ctx = self.match_context
        mode = "PLAYOFF (goal: win 3 of 5, early exit when done)" if self.is_playoff \
               else "REGULAR SEASON (goal: maximise total points, all 5 matches played)"
        base = f"- Session Mode: {mode}\n"
        if not ctx:
            return base
        return (
            base +
            f"- Opponent Team: {ctx.get('opponent_name', 'Unknown')}\n"
            f"- Full Opponent Roster: {ctx.get('opponent_roster', {})}\n"
            f"- Our Full Scheduled Roster: {ctx.get('avl_scheduled', {})}\n"
            f"- Week: {ctx.get('week', '?')} | Date: {ctx.get('date', '?')} | Location: {ctx.get('location', '?')}\n"
        )

    def suggest_throw(self, eligible_ours, rem_theirs, total_sl_used):
        prompt = f"""
        MATCH CONTEXT:
        {self._build_context_block()}
        CURRENT STATE:
        - AVAILABLE PLAYERS: {eligible_ours}
        - REMAINING OPPONENT PLAYERS: {rem_theirs}
        - TOTAL SL USED: {total_sl_used}/23

        TASK: We are throwing first. Choose ONE name from the AVAILABLE PLAYERS list only.
        """
        return self._query_groq(prompt)

    def suggest_counter(self, eligible_ours, opponent_name, opponent_sl, total_sl_used):
        points_left = 23 - total_sl_used
        prompt = f"""
        MATCH CONTEXT:
        {self._build_context_block()}
        CURRENT STATE:
        - Opponent threw: {opponent_name} (SL {opponent_sl})
        - AVAILABLE PLAYERS: {eligible_ours}
        - SL Left: {points_left}/23

        TASK: Choose ONE name from AVAILABLE PLAYERS to counter {opponent_name}.
        """
        return self._query_groq(prompt)