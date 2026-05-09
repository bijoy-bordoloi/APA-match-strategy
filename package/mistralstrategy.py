import requests
from strategies import BaseStrategy

class MistralStrategy(BaseStrategy):
    def __init__(self, model_name="mistral"):
        self.url = "http://localhost:11434/api/generate"
        self.model = model_name

    def _query_local_llm(self, prompt):
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "system": "You are an expert APA coach. Focus on the 23-rule."
        }
        try:
            response = requests.post(self.url, json=payload, timeout=60)
            return response.json().get('response', '').strip()
        except Exception as e:
             print(f"\n[DEBUG] AI Connection Failed: {e}") # This will show the error in your terminal
             return "AI Offline"

    def suggest_throw(self, eligible_ours, rem_theirs, total_sl_used):
        prompt = f"THROW suggestion needed. Roster: {eligible_ours}. Total SL: {total_sl_used}/23."
        return self._query_local_llm(prompt)

    def suggest_counter(self, eligible_ours, their_player_name, their_player_sl, total_sl_used):
        prompt = f"COUNTER {their_player_name} (SL {their_player_sl}). Roster: {eligible_ours}. Total SL: {total_sl_used}/23."
        return self._query_local_llm(prompt)
