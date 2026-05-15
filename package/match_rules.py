"""APA 8-ball rule helpers shared by Lambda routes and tests."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any


MAX_TURNS = 5
MAX_TEAM_SL = 23
MAX_PLAYER_APPEARANCES = 2
VALID_SCORES = {(3, 0), (2, 0), (2, 1), (1, 2), (0, 2), (0, 3)}


class RuleViolation(ValueError):
    """Raised when a proposed turn violates APA match constraints."""


@dataclass(frozen=True)
class MatchState:
    turn_number: int
    our_score: int
    their_score: int
    our_wins: int
    their_wins: int
    our_sl_used: int
    their_sl_used: int
    our_dp_used: bool
    their_dp_used: bool
    complete: bool
    clinched_by: str | None


def player_id_from_name(name: str) -> str:
    """Stable, readable player id for v1 single-team use."""
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")


def roster_to_players(roster: dict[str, int] | list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalize supported roster payload shapes to player dictionaries."""
    if not roster:
        return []

    if isinstance(roster, dict):
        return [
            {"player_id": player_id_from_name(name), "name": name, "skill_level": int(sl)}
            for name, sl in roster.items()
        ]

    players = []
    for player in roster:
        name = str(player.get("name") or player.get("displayName") or "").strip()
        if not name:
            continue
        players.append(
            {
                "player_id": str(player.get("player_id") or player.get("id") or player_id_from_name(name)),
                "name": name,
                "skill_level": int(player.get("skill_level") or player.get("skillLevel") or 0),
            }
        )
    return players


def players_to_sl_map(players: list[dict[str, Any]] | dict[str, int] | None) -> dict[str, int]:
    """Return a display-name -> SL map from either normalized players or a dict."""
    if not players:
        return {}
    if isinstance(players, dict):
        return {str(name): int(sl) for name, sl in players.items()}
    return {str(player["name"]): int(player["skill_level"]) for player in players}


def score_tuple(score: Any = None, our_score: Any = None, their_score: Any = None) -> tuple[int, int]:
    """Normalize a score payload to an ``(our, their)`` tuple."""
    if score is not None:
        if isinstance(score, str):
            left, right = score.replace(" ", "").split("-", 1)
            our_score, their_score = left, right
        elif isinstance(score, (list, tuple)) and len(score) == 2:
            our_score, their_score = score
        elif isinstance(score, dict):
            our_score = score.get("our_score", score.get("our"))
            their_score = score.get("their_score", score.get("their"))

    if our_score is None or their_score is None:
        raise RuleViolation("Score must include our_score and their_score.")

    normalized = (int(our_score), int(their_score))
    if normalized not in VALID_SCORES:
        valid = ", ".join(f"{ours}-{theirs}" for ours, theirs in sorted(VALID_SCORES, reverse=True))
        raise RuleViolation(f"Invalid APA 8-ball score {normalized[0]}-{normalized[1]}. Valid scores: {valid}.")
    return normalized


def sorted_turns(turns: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return sorted(turns or [], key=lambda item: int(item.get("turn_num", item.get("turn_number", 0))))


def _tget(turn: dict[str, Any], home_key: str, our_key: str, default: Any = 0) -> Any:
    """Read a turn field preferring home_* name, falling back to our_* for older records."""
    v = turn.get(home_key)
    return v if v is not None else turn.get(our_key, default)


def calculate_match_state(turns: list[dict[str, Any]] | None, mode: str = "regular") -> MatchState:
    ordered_turns = sorted_turns(turns)
    our_score = sum(int(_tget(t, "home_score", "our_score")) for t in ordered_turns)
    their_score = sum(int(_tget(t, "away_score", "their_score")) for t in ordered_turns)
    our_wins = sum(1 for t in ordered_turns if int(_tget(t, "home_score", "our_score")) >= 2)
    their_wins = sum(1 for t in ordered_turns if int(_tget(t, "away_score", "their_score")) >= 2)
    our_sl_used = sum(int(_tget(t, "home_sl_snapshot", "our_sl_snapshot")) for t in ordered_turns)
    their_sl_used = sum(int(_tget(t, "away_sl_snapshot", "their_sl_snapshot")) for t in ordered_turns)
    our_dp_used = any(bool(t.get("is_home_dp") or t.get("is_our_dp")) for t in ordered_turns)
    their_dp_used = any(bool(t.get("is_away_dp") or t.get("is_their_dp")) for t in ordered_turns)

    clinched_by = None
    if mode == "playoff":
        if our_wins >= 3:
            clinched_by = "ours"
        elif their_wins >= 3:
            clinched_by = "theirs"

    complete = len(ordered_turns) >= MAX_TURNS or clinched_by is not None
    return MatchState(
        turn_number=len(ordered_turns) + 1,
        our_score=our_score,
        their_score=their_score,
        our_wins=our_wins,
        their_wins=their_wins,
        our_sl_used=our_sl_used,
        their_sl_used=their_sl_used,
        our_dp_used=our_dp_used,
        their_dp_used=their_dp_used,
        complete=complete,
        clinched_by=clinched_by,
    )


def _name_for(turn: dict[str, Any], side: str) -> str:
    home_side = "home" if side == "our" else "away"
    return str(
        turn.get(f"{home_side}_player_name")
        or turn.get(f"{side}_player_name")
        or turn.get(f"{side}_player_id")
        or ""
    )


def play_counts(turns: list[dict[str, Any]] | None, side: str) -> Counter[str]:
    return Counter(name for name in (_name_for(turn, side) for turn in sorted_turns(turns)) if name)


def is_dp_used(turns: list[dict[str, Any]] | None, side: str) -> bool:
    home_flag = "is_home_dp" if side == "our" else "is_away_dp"
    our_flag = "is_our_dp" if side == "our" else "is_their_dp"
    return any(bool(t.get(home_flag) or t.get(our_flag)) for t in sorted_turns(turns))


def eligible_players(
    roster: dict[str, int],
    turns: list[dict[str, Any]] | None,
    *,
    side: str = "our",
    enforce_budget: bool = True,
    fresh_only: bool = False,
) -> dict[str, int]:
    """Return roster members currently legal for the next turn."""
    state = calculate_match_state(turns)
    counts = play_counts(turns, side)
    dp_used = is_dp_used(turns, side)
    sl_used = state.our_sl_used if side == "our" else state.their_sl_used
    room = MAX_TEAM_SL - sl_used

    is_last_turn = state.turn_number >= MAX_TURNS

    eligible: dict[str, int] = {}
    for name, skill_level in roster.items():
        appearances = counts.get(name, 0)
        if fresh_only and appearances > 0:
            continue
        if appearances >= MAX_PLAYER_APPEARANCES:
            continue
        if appearances == 1 and dp_used:
            continue
        # Replay Rule: a second appearance is only permitted on the final turn.
        # It is a forfeit-prevention measure, never a tactical choice.
        # The opponent picks the replay player from all previously-played players.
        # Not available in playoff mode (enforced in validate_turn).
        if appearances == 1 and not is_last_turn:
            continue
        if enforce_budget and int(skill_level) > room:
            continue
        eligible[name] = int(skill_level)
    return eligible


def validate_turn(
    turns: list[dict[str, Any]] | None,
    *,
    mode: str,
    our_player_name: str,
    their_player_name: str,
    our_sl: int,
    their_sl: int,
    our_score: int,
    their_score: int,
    is_our_dp: bool | None = None,
    is_their_dp: bool | None = None,
) -> dict[str, Any]:
    """Validate and normalize a completed turn before persistence."""
    state = calculate_match_state(turns, mode=mode)
    if state.complete:
        raise RuleViolation("This match is already complete.")
    if state.turn_number > MAX_TURNS:
        raise RuleViolation("APA 8-ball matches have at most 5 turns.")

    score_tuple(our_score=our_score, their_score=their_score)

    our_counts = play_counts(turns, "our")
    their_counts = play_counts(turns, "their")
    normalized_our_dp = _validate_player_reuse(
        our_player_name,
        our_counts,
        state.our_dp_used,
        requested_dp=is_our_dp,
        label="our",
        turn_number=state.turn_number,
    )
    normalized_their_dp = _validate_player_reuse(
        their_player_name,
        their_counts,
        state.their_dp_used,
        requested_dp=is_their_dp,
        label="their",
        turn_number=state.turn_number,
    )

    if state.our_sl_used + int(our_sl) > MAX_TEAM_SL:
        raise RuleViolation(f"Our SL total would exceed {MAX_TEAM_SL}.")
    # Opponent SL is not enforced here — we record what actually happened.

    return {
        "turn_num": state.turn_number,
        "our_player_name": our_player_name,
        "their_player_name": their_player_name,
        "our_sl_snapshot": int(our_sl),
        "their_sl_snapshot": int(their_sl),
        "our_score": int(our_score),
        "their_score": int(their_score),
        "is_our_dp": normalized_our_dp,
        "is_their_dp": normalized_their_dp,
    }


def _validate_player_reuse(
    name: str,
    counts: Counter[str],
    dp_used: bool,
    *,
    requested_dp: bool | None,
    label: str,
    turn_number: int = MAX_TURNS,
    mode: str = "regular",
) -> bool:
    appearances = counts.get(name, 0)
    if appearances >= MAX_PLAYER_APPEARANCES:
        raise RuleViolation(f"{name} cannot play a third time for {label} team.")
    if appearances == 0:
        return False
    # Replay Rule is not available in playoff mode
    if mode == "playoff":
        raise RuleViolation(
            f"The Replay Rule is not allowed in playoff matches. "
            f"{name} has already played for {label} team."
        )
    if turn_number < MAX_TURNS:
        raise RuleViolation(
            f"The Replay Rule may only be used in the final match (match {MAX_TURNS}). "
            f"{name} has already played for {label} team."
        )
    if dp_used:
        raise RuleViolation(f"{label.title()} replay has already been used this match.")
    if requested_dp is False:
        raise RuleViolation(
            f"{name} is a replay (second appearance) — mark is_dp to confirm."
        )
    return True


def summarize_match(match: dict[str, Any], turns: list[dict[str, Any]] | None) -> dict[str, Any]:
    mode = str(match.get("mode", "regular"))
    state = calculate_match_state(turns, mode=mode)
    played = len(sorted_turns(turns))
    max_points = played * 3
    efficiency = round((state.our_score / max_points) * 100, 1) if max_points else 0
    result = None
    if state.complete:
        if mode == "playoff":
            result = "victory" if state.our_wins >= 3 else "defeat"
        elif state.our_score > state.their_score:
            result = "points_win"
        elif state.our_score < state.their_score:
            result = "points_loss"
        else:
            result = "tie"

    return {
        "turn_number": min(state.turn_number, MAX_TURNS),
        "our_score": state.our_score,
        "their_score": state.their_score,
        "our_wins": state.our_wins,
        "their_wins": state.their_wins,
        "our_sl_used": state.our_sl_used,
        "their_sl_used": state.their_sl_used,
        "our_dp_used": state.our_dp_used,
        "their_dp_used": state.their_dp_used,
        "complete": state.complete,
        "clinched_by": state.clinched_by,
        "point_efficiency": efficiency,
        "result": result,
    }


def build_llm_context(match: dict[str, Any], turns: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Compact context shape used by /suggest and /chat."""
    summary = summarize_match(match, turns)
    return {
        **match,
        "summary": summary,
        "turns": sorted_turns(turns),
        "sl_budget_remaining": MAX_TEAM_SL - summary["our_sl_used"],
        "match_goal": "first to 3 wins, stop when clinched"
        if match.get("mode") == "playoff"
        else "maximize total points across all 5 turns",
    }
