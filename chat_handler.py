"""Claude (primary) + Groq (fallback) chat and suggestion interface.

The public ``generate_response`` and ``generate_suggestion`` functions are
intentionally standalone. Lambda routes, storage, and the React client are
unaffected by changes inside this module.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request


# ── Anthropic (primary) ───────────────────────────────────────────────────────
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_CLAUDE_MODEL = "claude-haiku-4-5-20251001"

_anthropic_key_cache: str | None = None

# ── Groq (fallback) ───────────────────────────────────────────────────────────
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"

_groq_key_cache: str | None = None


# ── System prompts ────────────────────────────────────────────────────────────
CHAT_SYSTEM_PROMPT = """You are the match strategist for Anti-Villain League in APA 8-ball.
Use the provided match context, APA SL-23 constraints, history, head-to-head notes,
and current score. In regular mode, optimize total points across all five turns.
In playoff mode, optimize getting to three individual wins as quickly and safely as possible.
Be direct and practical for a captain at the table.
IMPORTANT: Never suggest using a double play. A double play only occurs as a last resort
on the final turn when no unplayed scheduled players remain — it is never a tactical choice."""


SUGGEST_SYSTEM_PROMPT = """You are the match strategist for Anti-Villain League in APA 8-ball.
Return exactly one player name and nothing else. The name must come from the eligible
players list. Respect the APA SL-23 total, no third plays, the current mode goal,
and the recorded match history.
IMPORTANT: Never suggest a player who has already played this match. A double play
is only a last-resort fallback on the final turn, not a strategic option."""


# ── Public interface ──────────────────────────────────────────────────────────

def generate_response(message: str, match_context: dict[str, Any], history: list[dict[str, str]] | None) -> str:
    """Generate a freeform chat response for the current match context."""
    enriched = dict(match_context or {})
    try:
        from player_data import build_player_context
        player_ctx = build_player_context(message)
        if player_ctx:
            enriched["player_stats"] = player_ctx
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("player stats enrichment failed: %s", exc)

    system, messages = _build_claude_payload(
        system_prompt=CHAT_SYSTEM_PROMPT,
        message=message,
        match_context=enriched,
        history=history or [],
    )
    try:
        return _query_claude(system, messages, temperature=0.2, max_tokens=1024)
    except RuntimeError:
        groq_messages = _build_groq_messages(CHAT_SYSTEM_PROMPT, message, enriched, history or [])
        return _query_groq(groq_messages, temperature=0.2)


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
    """Ask the LLM for a single player recommendation and coerce to an eligible name."""
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

    system, messages = _build_claude_payload(
        system_prompt=SUGGEST_SYSTEM_PROMPT,
        message=task,
        match_context=prompt_context,
        history=[],
    )
    try:
        raw = _query_claude(system, messages, temperature=0.05, max_tokens=50)
        return _coerce_player_name(raw, eligible_our_players)
    except RuntimeError:
        try:
            groq_messages = _build_groq_messages(SUGGEST_SYSTEM_PROMPT, task, prompt_context, [])
            raw = _query_groq(groq_messages, temperature=0.05)
            return _coerce_player_name(raw, eligible_our_players)
        except RuntimeError:
            return _fallback_suggestion(eligible_our_players, opponent_sl)


# ── Claude ────────────────────────────────────────────────────────────────────

def _build_claude_payload(
    *,
    system_prompt: str,
    message: str,
    match_context: dict[str, Any],
    history: list[dict[str, str]],
) -> tuple[str, list[dict[str, str]]]:
    """Return (system_str, messages_list) for the Anthropic Messages API."""
    context_block = json.dumps(match_context or {}, default=str, indent=2, sort_keys=True)
    system = (
        f"{system_prompt}\n\n"
        f"Current match context — always reference this:\n{context_block}"
    )
    messages: list[dict[str, str]] = []
    for item in history[-12:]:
        role = item.get("role", "user")
        if role not in {"user", "assistant"}:
            continue  # Anthropic only accepts user/assistant in messages
        content = str(item.get("content", "")).strip()
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})
    return system, messages


def _query_claude(
    system: str,
    messages: list[dict[str, str]],
    *,
    temperature: float,
    max_tokens: int,
) -> str:
    api_key = _get_anthropic_api_key()
    payload = {
        "model": os.environ.get("CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": messages,
    }
    req = request.Request(
        ANTHROPIC_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=float(os.environ.get("CLAUDE_TIMEOUT_SECONDS", "20"))) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            msg = json.loads(body).get("error", {}).get("message", body)
        except json.JSONDecodeError:
            msg = body
        raise RuntimeError(f"Claude API error: {msg}") from exc
    if "error" in result:
        raise RuntimeError(f"Claude API error: {result['error'].get('message', result['error'])}")
    return result["content"][0]["text"].strip()


def _get_anthropic_api_key() -> str:
    global _anthropic_key_cache
    if _anthropic_key_cache is not None:
        return _anthropic_key_cache

    local_key = os.environ.get("ANTHROPIC_API_KEY")
    if local_key:
        _anthropic_key_cache = local_key
        return _anthropic_key_cache

    parameter_name = os.environ.get("ANTHROPIC_API_KEY_PARAM", "/apa-match-engine/ANTHROPIC_API_KEY")
    try:
        import boto3
        ssm = boto3.client("ssm")
        response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
        _anthropic_key_cache = response["Parameter"]["Value"]
    except Exception as exc:
        raise RuntimeError(f"Unable to load Anthropic API key from SSM {parameter_name}.") from exc
    return _anthropic_key_cache


# ── Groq (fallback) ───────────────────────────────────────────────────────────

def _build_groq_messages(
    system_prompt: str,
    message: str,
    match_context: dict[str, Any],
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    context_block = json.dumps(match_context or {}, default=str, indent=2, sort_keys=True)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": f"Current match context:\n{context_block}"},
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
    payload = {
        "model": os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL),
        "messages": messages,
        "temperature": temperature,
    }
    req = request.Request(
        GROQ_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "python-urllib/3",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=float(os.environ.get("GROQ_TIMEOUT_SECONDS", "12"))) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            msg = json.loads(body).get("error", {}).get("message", body)
        except json.JSONDecodeError:
            msg = body
        raise RuntimeError(f"Groq API error: {msg}") from exc
    if "error" in result:
        raise RuntimeError(f"Groq API error: {result.get('error', {}).get('message', 'unknown')}")
    return result["choices"][0]["message"]["content"].strip()


def _get_groq_api_key() -> str:
    global _groq_key_cache
    if _groq_key_cache is not None:
        return _groq_key_cache

    local_key = os.environ.get("GROQ_API_KEY")
    if local_key:
        _groq_key_cache = local_key
        return _groq_key_cache

    parameter_name = os.environ.get("GROQ_API_KEY_PARAM", "/apa-match-engine/GROQ_API_KEY")
    try:
        import boto3
        ssm = boto3.client("ssm")
        response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
        _groq_key_cache = response["Parameter"]["Value"]
    except Exception as exc:
        raise RuntimeError(f"Unable to load Groq API key from SSM {parameter_name}.") from exc
    return _groq_key_cache


# ── Shared helpers ────────────────────────────────────────────────────────────

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
