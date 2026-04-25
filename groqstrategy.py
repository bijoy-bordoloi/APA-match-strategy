import requests
from strategies import BaseStrategy
from prompts import COACH_SYSTEM_PROMPT  # Import the prompt here

class GroqStrategy(BaseStrategy):
    def __init__(self, api_key):
        self.url = "https://api.groq.com/openai/v1/chat/completions"
        self.api_key = api_key
        self.model = "llama-3.1-8b-instant"

    def _query_groq(self, user_prompt):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": COACH_SYSTEM_PROMPT}, # Use the imported prompt
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.1
        }
        
        try:
            response = requests.post(self.url, headers=headers, json=payload, timeout=10)
            result = response.json()
            return result['choices'][0]['message']['content'].strip()
        except Exception as e:
            return f"Error: {str(e)}"

    def suggest_throw(self, eligible_ours, rem_theirs, total_sl_used):
    
        prompt = f"""
        MATCH STATE:
        - AVAILABLE PLAYERS: {eligible_ours} 
        - TOTAL SL USED: {total_sl_used}/23
    
        TASK: We are throwing first. Choose ONE name from the AVAILABLE PLAYERS list only.
        """
        return self._query_groq(prompt)

    def suggest_counter(self, eligible_ours, opponent_name, opponent_sl, total_sl_used):
        points_left = 23 - total_sl_used
        prompt = f"Opponent threw: {opponent_name} (SL {opponent_sl}). Available: {eligible_ours}. SL Left: {points_left}."
        return self._query_groq(prompt)

