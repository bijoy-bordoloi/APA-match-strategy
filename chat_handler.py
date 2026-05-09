"""Groq chat and suggestion interface.

The public ``generate_response(message, match_context, history)`` function is
intentionally standalone. A future MCP/tool-call loop can replace its internals
without changing Lambda routes, storage, or the React client.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.1-8b-instant"

_api_key_cache: str | None = None


CHAT_SYSTEM_PROMPT = """You are the match strategist for Anti-Villain League in APA 8-ball.
Use the provided match context, APA SL-23 constraints, double-play flags, history,
head-to-head notes, and current score. In regular mode, optimize total points
across all five turns. In playoff mode, optimize getting to three individual wins
as quickly and safely as possible. Be direct and practical for a captain at the table."""


SUGGEST_SYSTEM_PROMPT = """You are the match strategist for Anti-Villain League in APA 8-ball.
Return exactly one player name and nothing else. The name must come from the eligible
players list. Respect the APA SL-23 total, one double-play per team, no third plays,
the current mode goal, and the recorded match history."""


def generate_response(message: str, match_context: dict[str, Any], history: list[dict[str, str]] | None) -> str:
    """Generate a freeform chat response for the current match context."""
    messages = _build_messages(
        system_prompt=CHAT_SYSTEM_PROMPT,
        message=message,
        match_context=match_context,
        history=history or [],
    )
    return _query_groq(messages, temperature=0.2)


def generate_suggestion(
    *,
    action: str,
    eligible_our_players: dict[str, int],
    remaining_their_players: dict[str, int],
    total_sl_used: int,
    match_context: dict[str, Any],
    opponent_name: str | None = None,
    opponent_sl: int | None = None,
) -> str:
    """Ask Groq for a single player recommendation and coerce to an eligible name."""
    if not eligible_our_players:
        return "No eligible player"

    if action == "suggest_throw":
        task = (
            "We are throwing first this turn. Choose one player from eligible_our_players "
            "to throw against the opponent's remaining roster."
        )
    elif action == "suggest_counter":
        task = (
            f"The opponent threw {opponent_name} at SL {opponent_sl}. Choose one player "
            "from eligible_our_players to counter."
        )
    else:
        raise ValueError(f"Unknown suggestion action: {action!r}")

    prompt_context = {
        **(match_context or {}),
        "eligible_our_players": eligible_our_players,
        "remaining_their_players": remaining_their_players,
        "total_sl_used": total_sl_used,
        "opponent_name": opponent_name,
        "opponent_sl": opponent_sl,
        "task": task,
    }
    messages = _build_messages(
        system_prompt=SUGGEST_SYSTEM_PROMPT,
        message=task,
        match_context=prompt_context,
        history=[],
    )
    try:
        raw = _query_groq(messages, temperature=0.05)
    except RuntimeError:
        return _fallback_suggestion(eligible_our_players, opponent_sl)
    return _coerce_player_name(raw, eligible_our_players)


def _build_messages(
    *,
    system_prompt: str,
    message: str,
    match_context: dict[str, Any],
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    context_block = json.dumps(match_context or {}, default=str, indent=2, sort_keys=True)
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "system",
            "content": (
                "Every answer must account for this full current match context:\n"
                f"{context_block}"
            ),
        },
    ]
    for item in history[-12:]:
        role = item.get("role", "user")
        if role not in {"user", "assistant", "system"}:
            role = "user"
        content = str(item.get("content", "")).strip()
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})
    return messages


def _query_groq(messages: list[dict[str, str]], *, temperature: float) -> str:
    api_key = _get_groq_api_key()
    if not api_key:
        raise RuntimeError("Groq API key is not configured.")

    payload = {
        "model": os.environ.get("GROQ_MODEL", DEFAULT_MODEL),
        "messages": messages,
        "temperature": temperature,
    }
    encoded_payload = json.dumps(payload).encode("utf-8")
    groq_request = request.Request(
        GROQ_URL,
        data=encoded_payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(groq_request, timeout=float(os.environ.get("GROQ_TIMEOUT_SECONDS", "12"))) as response:
            result = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            result = json.loads(body)
            message = result.get("error", {}).get("message", body)
        except json.JSONDecodeError:
            message = body
        raise RuntimeError(f"Groq API error: {message}") from exc
    if "error" in result:
        message = result.get("error", {}).get("message", "Unknown Groq API error")
        raise RuntimeError(f"Groq API error: {message}")
    return result["choices"][0]["message"]["content"].strip()


def _get_groq_api_key() -> str:
    global _api_key_cache
    if _api_key_cache is not None:
        return _api_key_cache

    local_key = os.environ.get("GROQ_API_KEY")
    if local_key:
        _api_key_cache = local_key
        return _api_key_cache

    parameter_name = os.environ.get("GROQ_API_KEY_PARAM", "/apa-match-engine/GROQ_API_KEY")
    try:
        import boto3

        ssm = boto3.client("ssm")
        response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
        _api_key_cache = response["Parameter"]["Value"]
    except Exception as exc:  # noqa: BLE001 - Lambda route converts this to JSON.
        raise RuntimeError(f"Unable to load Groq API key from SSM parameter {parameter_name}.") from exc
    return _api_key_cache


def _coerce_player_name(raw: str, eligible_players: dict[str, int]) -> str:
    cleaned = raw.strip().strip('"').strip("'").splitlines()[0].strip()
    for name in eligible_players:
        if cleaned.casefold() == name.casefold():
            return name

    raw_folded = raw.casefold()
    mentioned = [name for name in eligible_players if name.casefold() in raw_folded]
    if mentioned:
        return mentioned[0]

    return _fallback_suggestion(eligible_players)


def _fallback_suggestion(eligible_players: dict[str, int], opponent_sl: int | None = None) -> str:
    if not eligible_players:
        return "No eligible player"
    if opponent_sl is not None:
        return min(eligible_players, key=lambda name: abs(int(eligible_players[name]) - int(opponent_sl)))
    return sorted(eligible_players.items(), key=lambda item: (item[1], item[0]))[len(eligible_players) // 2][0]
