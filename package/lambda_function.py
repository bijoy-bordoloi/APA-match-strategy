"""APA Match Engine — API Gateway Lambda backend.

Routes implemented for the mobile React app:
  POST /match           create or load a match session
  POST /suggest         get a throw/counter recommendation
  POST /chat            freeform LLM chat on match context
  POST /result          record a completed turn
  POST /submit          create/update a complete match result
  GET  /history         list past matches and player stats
  GET  /rosters         rosters, schedule, and match info from S3
  GET  /division        full season schedule, all teams, and results
  GET|POST /players     look up a player's stats from Neon
  POST /players/search  full-text or leaderboard search
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from chat_handler import generate_response, generate_suggestion
from data_access import MatchRepository, build_history_response, slugify
from match_rules import (
    RuleViolation,
    build_llm_context,
    eligible_players,
    player_id_from_name,
    players_to_sl_map,
    roster_to_players,
    score_tuple,
    summarize_match,
    validate_turn,
)


logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Compact APA 8-ball rules injected into every LLM call (~100 tokens).
# Full rules reference lives in configurations/rules-reference.txt.
_APA_RULES_COMPACT = (
    "APA 8-Ball SL-23 format: 5 players/team, 5 turns/night. "
    "SL-23 budget: sl_used + next_sl + (remaining_turns×2) ≤ 23. "
    "Turn scores: 3-0, 2-0, 2-1, 1-2, 0-2, 0-3 (points, not games). "
    "Regular season: maximize total points (win night at 8+/10). "
    "Playoff: first team to 3 individual wins. "
    "Replay Rule (double play): forfeit-prevention ONLY — never tactical. "
    "Only on the final turn (turn 5) in regular season when no fresh players remain. "
    "Not available in playoffs. OPPONENT picks the replay player. "
    "One DP per team per night. No player may play a third time."
)


class NotFound(ValueError):
    pass


def _build_strategy(strategy_name: str, is_playoff: bool, match_context: dict[str, Any]):
    if strategy_name == "aggressive":
        from strategies import AggressiveStrategy

        return AggressiveStrategy(is_playoff=is_playoff)
    if strategy_name == "neutral":
        from strategies import NeutralStrategy

        return NeutralStrategy(is_playoff=is_playoff)
    raise ValueError(f"Unknown strategy: {strategy_name!r}")



def handle_match(body: dict[str, Any], repository: MatchRepository) -> dict[str, Any]:
    match_id = body.get("match_id")
    if match_id and not body.get("create_new"):
        loaded = repository.get_match(str(match_id))
        if loaded:
            return {"result": loaded, "error": None}

    match_context = body.get("match_context", {})
    our_roster_source = (
        body.get("our_roster")
        or body.get("our_team")
        or match_context.get("our_roster")
        or match_context.get("avl_scheduled")
        or match_context.get("full_avl_roster")
    )
    opponent_roster_source = (
        body.get("opponent_roster")
        or body.get("their_roster")
        or body.get("their_team")
        or match_context.get("opponent_roster")
    )

    our_players = roster_to_players(our_roster_source)
    opponent_players = roster_to_players(opponent_roster_source)

    opponent_team = body.get("opponent_team") if isinstance(body.get("opponent_team"), dict) else {}
    opponent_name = (
        body.get("opponent_name")
        or opponent_team.get("name")
        or match_context.get("opponent_name")
        or "Opponent"
    )
    our_team_name = body.get("our_team_name") or "Anti-Villain League"

    our_team_id = str(body.get("our_team_id") or "anti-villain-league")
    opponent_team_id = str(body.get("opponent_team_id") or opponent_team.get("team_id") or slugify(opponent_name))

    repository.put_team(our_team_id, our_team_name, our_players)
    repository.put_team(opponent_team_id, opponent_name, opponent_players)

    first_move = str(body.get("first_move", "")).lower()
    we_throw_first = bool(body.get("we_throw_first", first_move in {"throwing", "throw", "first"}))
    mode = body.get("mode") or ("playoff" if body.get("is_playoff") else "regular")

    match = repository.put_match(
        {
            "match_id": str(match_id) if match_id else None,
            "week": body.get("week") or match_context.get("week"),
            "date": body.get("date") or match_context.get("date"),
            "location": body.get("location") or match_context.get("location"),
            "home_team_id": body.get("home_team_id") or our_team_id,
            "away_team_id": body.get("away_team_id") or opponent_team_id,
            "our_team_id": our_team_id,
            "opponent_team_id": opponent_team_id,
            "our_team_name": our_team_name,
            "opponent_team_name": opponent_name,
            "mode": mode,
            "status": body.get("status", "live"),
            "we_throw_first": we_throw_first,
            "our_roster": players_to_sl_map(our_players),
            "their_roster": players_to_sl_map(opponent_players),
            "source_context": match_context,
        }
    )

    loaded = repository.get_match(match["match_id"])
    return {"result": loaded, "error": None}


def handle_suggest(body: dict[str, Any], repository: MatchRepository) -> dict[str, Any]:
    action = body["action"]
    match_context = _context_from_body_or_match(body, repository)
    eligible_ours = body.get("eligible_our_players") or body.get("eligible_ours")
    remaining_theirs = body.get("remaining_their_players") or body.get("rem_theirs")

    # Always compute eligible_ours server-side with fresh_only=True so the LLM
    # never sees a DP candidate as a suggestion option.
    eligible_ours = eligible_players(
        match_context.get("our_roster", {}),
        match_context.get("turns", []),
        side="our",
        fresh_only=True,
    )
    if not remaining_theirs:
        remaining_theirs = eligible_players(
            match_context.get("their_roster", {}),
            match_context.get("turns", []),
            side="their",
            enforce_budget=False,
        )

    total_sl_used = int(
        body.get("total_sl_used", match_context.get("summary", {}).get("our_sl_used", 0))
    )
    strategy_name = body.get("strategy", "groq")

    if strategy_name in {"aggressive", "neutral"}:
        strategy = _build_strategy(strategy_name, match_context.get("mode") == "playoff", match_context)
        if action == "suggest_throw":
            suggestion = strategy.suggest_throw(eligible_ours, remaining_theirs, total_sl_used)
        elif action == "suggest_counter":
            suggestion = strategy.suggest_counter(
                eligible_ours,
                body["opponent_name"],
                int(body["opponent_sl"]),
                total_sl_used,
            )
        else:
            raise ValueError(f"Unknown action: {action!r}")
    else:
        enriched_context = _enrich_with_history(_enrich_with_h2h(match_context, repository), repository)
        try:
            from player_data import get_apr_for_names, apr_band
            apr_map = get_apr_for_names(list(eligible_ours.keys()))
            if apr_map:
                enriched_context["apr_scores"] = {
                    name: {
                        "score": round(row["apr"], 1),
                        "band": apr_band(row["apr"]),
                        "match_count": row.get("match_count"),
                    }
                    for name, row in apr_map.items()
                    if row.get("apr") is not None
                }
                logger.info("APR enriched for suggest: %s", list(enriched_context["apr_scores"].keys()))
        except Exception as exc:
            logger.info("APR enrichment skipped for suggest: %s", exc)

        enriched_context["eligibility_rules"] = _APA_RULES_COMPACT
        suggestion = generate_suggestion(
            action=action,
            eligible_our_players={name: int(sl) for name, sl in eligible_ours.items()},
            remaining_their_players={name: int(sl) for name, sl in remaining_theirs.items()},
            total_sl_used=total_sl_used,
            opponent_name=body.get("opponent_name"),
            opponent_sl=body.get("opponent_sl"),
            match_context=enriched_context,
        )

    return {"suggestion": suggestion, "result": suggestion, "error": None}


def handle_chat(body: dict[str, Any], repository: MatchRepository) -> dict[str, Any]:
    message = str(body["message"]).strip()
    if not message:
        raise ValueError("message is required")

    match_context = _context_from_body_or_match(body, repository)
    match_context = _enrich_with_h2h(match_context, repository)
    match_context = _enrich_with_history(match_context, repository)
    match_context["apa_rules"] = _APA_RULES_COMPACT
    try:
        from player_data import get_apr_for_names, apr_band
        all_names = (
            list((match_context.get("our_roster") or {}).keys())
            + list((match_context.get("their_roster") or {}).keys())
        )
        if all_names:
            apr_map = get_apr_for_names(all_names)
            if apr_map:
                match_context["roster_apr"] = {
                    name: {
                        "score": round(row["apr"], 1),
                        "band": apr_band(row["apr"]),
                        "match_count": row.get("match_count"),
                    }
                    for name, row in apr_map.items()
                    if row.get("apr") is not None
                }
    except Exception as exc:
        logger.info("Roster APR enrichment skipped for chat: %s", exc)
    reply = generate_response(message, match_context, body.get("history", []))
    return {"reply": reply, "result": reply, "error": None}


def handle_result(body: dict[str, Any], repository: MatchRepository) -> dict[str, Any]:
    match_id = str(body["match_id"])
    loaded = repository.get_match(match_id)
    if not loaded:
        raise NotFound(f"Match {match_id} not found")

    turn_body = body.get("turn", body)
    match = loaded["match"]
    turns = loaded["turns"]
    our_score, their_score = score_tuple(
        score=turn_body.get("score"),
        our_score=turn_body.get("our_score"),
        their_score=turn_body.get("their_score"),
    )

    our_name = _player_name(turn_body, match, side="our")
    their_name = _player_name(turn_body, match, side="their")
    our_sl = _skill_level(turn_body, match, our_name, side="our")
    their_sl = _skill_level(turn_body, match, their_name, side="their")

    normalized = validate_turn(
        turns,
        mode=match.get("mode", "regular"),
        our_player_name=our_name,
        their_player_name=their_name,
        our_sl=our_sl,
        their_sl=their_sl,
        our_score=our_score,
        their_score=their_score,
        is_our_dp=turn_body.get("is_our_dp"),
        is_their_dp=turn_body.get("is_their_dp"),
    )
    normalized.update(
        {
            "our_player_id": str(turn_body.get("our_player_id") or player_id_from_name(our_name)),
            "their_player_id": str(turn_body.get("their_player_id") or player_id_from_name(their_name)),
        }
    )
    saved_turn = repository.put_turn(match_id, normalized)
    repository.update_h2h(
        saved_turn.get("home_player_id") or saved_turn.get("our_player_id"),
        saved_turn.get("away_player_id") or saved_turn.get("their_player_id"),
        won=int(saved_turn.get("home_score") or saved_turn.get("our_score") or 0) >= 2,
    )

    reloaded = repository.get_match(match_id)
    if not reloaded:
        raise NotFound(f"Match {match_id} not found after update")
    repository.set_match_status(match_id, "complete" if reloaded["summary"]["complete"] else "live")
    reloaded = repository.get_match(match_id)
    return {"result": reloaded, "turn": saved_turn, "error": None}


def handle_submit(body: dict[str, Any], repository: MatchRepository) -> dict[str, Any]:
    match_body = {**body.get("match", {}), **body}
    turns_body = body.get("turns") or body.get("match", {}).get("turns") or []
    if not turns_body:
        raise ValueError("turns are required")

    match_context = match_body.get("match_context", {})
    our_roster_source = (
        match_body.get("our_roster")
        or match_body.get("our_team")
        or match_context.get("our_roster")
        or match_context.get("avl_scheduled")
        or match_context.get("full_avl_roster")
    )
    opponent_roster_source = (
        match_body.get("opponent_roster")
        or match_body.get("their_roster")
        or match_body.get("their_team")
        or match_context.get("their_roster")
        or match_context.get("opponent_roster")
    )
    our_players = roster_to_players(our_roster_source)
    opponent_players = roster_to_players(opponent_roster_source)

    opponent_team = match_body.get("opponent_team") if isinstance(match_body.get("opponent_team"), dict) else {}
    opponent_name = (
        match_body.get("opponent_name")
        or match_body.get("opponent_team_name")
        or opponent_team.get("name")
        or match_context.get("opponent_team_name")
        or match_context.get("opponent_name")
        or "Opponent"
    )
    our_team_name = match_body.get("our_team_name") or "Anti-Villain League"
    our_team_id = str(match_body.get("our_team_id") or "anti-villain-league")
    opponent_team_id = str(match_body.get("opponent_team_id") or opponent_team.get("team_id") or slugify(opponent_name))

    repository.put_team(our_team_id, our_team_name, our_players)
    repository.put_team(opponent_team_id, opponent_name, opponent_players)

    first_move = str(match_body.get("first_move", "")).lower()
    we_throw_first = bool(match_body.get("we_throw_first", first_move in {"throwing", "throw", "first"}))
    mode = match_body.get("mode") or ("playoff" if match_body.get("is_playoff") else "regular")
    match = repository.put_match(
        {
            "match_id": match_body.get("match_id"),
            "week": match_body.get("week") or match_context.get("week"),
            "date": match_body.get("date") or match_context.get("date"),
            "location": match_body.get("location") or match_context.get("location"),
            "home_team_id": match_body.get("home_team_id") or our_team_id,
            "away_team_id": match_body.get("away_team_id") or opponent_team_id,
            "our_team_id": our_team_id,
            "opponent_team_id": opponent_team_id,
            "our_team_name": our_team_name,
            "opponent_team_name": opponent_name,
            "mode": mode,
            "status": "complete",
            "we_throw_first": we_throw_first,
            "our_roster": players_to_sl_map(our_players),
            "their_roster": players_to_sl_map(opponent_players),
            "source_context": match_context,
        }
    )

    normalized_turns: list[dict[str, Any]] = []
    for turn_body in sorted(turns_body, key=lambda turn: int(turn.get("turn_num", 0))):
        our_score, their_score = score_tuple(
            score=turn_body.get("score"),
            our_score=turn_body.get("our_score"),
            their_score=turn_body.get("their_score"),
        )
        our_name = _player_name(turn_body, match, side="our")
        their_name = _player_name(turn_body, match, side="their")
        our_sl = _skill_level(turn_body, match, our_name, side="our")
        their_sl = _skill_level(turn_body, match, their_name, side="their")
        normalized = validate_turn(
            normalized_turns,
            mode=mode,
            our_player_name=our_name,
            their_player_name=their_name,
            our_sl=our_sl,
            their_sl=their_sl,
            our_score=our_score,
            their_score=their_score,
            is_our_dp=turn_body.get("is_our_dp"),
            is_their_dp=turn_body.get("is_their_dp"),
        )
        normalized.update(
            {
                "our_player_id": str(turn_body.get("our_player_id") or player_id_from_name(our_name)),
                "their_player_id": str(turn_body.get("their_player_id") or player_id_from_name(their_name)),
            }
        )
        normalized_turns.append(normalized)

    summary = summarize_match(match, normalized_turns)
    if not summary["complete"]:
        raise RuleViolation("A match can only be submitted after it is complete.")

    repository.replace_turns(match["match_id"], normalized_turns)
    repository.set_match_status(match["match_id"], "complete")
    loaded = repository.get_match(match["match_id"])
    return {"result": loaded, "error": None}


def handle_history(_body: dict[str, Any], repository: MatchRepository, query: dict[str, str]) -> dict[str, Any]:
    status = query.get("status", "complete")
    limit = int(query.get("limit", "200"))
    return {"result": build_history_response(repository, status=status, limit=limit), "error": None}


def handle_delete_match(body: dict[str, Any], repository: MatchRepository) -> dict[str, Any]:
    match_id = str(body.get("match_id") or "")
    if not match_id:
        raise ValueError("match_id is required")
    repository.delete_match(match_id)
    return {"result": {"deleted": match_id}, "error": None}


def handle_rosters(_body: dict[str, Any], _repository: MatchRepository, query: dict[str, str]) -> dict[str, Any]:
    from config_loader import get_matches_data, get_rosters_data, get_schedule_csv
    from datetime import datetime, timezone

    raw = get_rosters_data()
    teams = raw[0]["data"]["division"]["teams"]

    our_team = None
    opponent_teams = []

    for team in teams:
        if team.get("isBye"):
            continue
        players = [
            {"name": p["displayName"], "skill_level": p["skillLevel"]}
            for p in team.get("roster", [])
            if p["skillLevel"] > 0
        ]
        if "anti vill" in team["name"].lower():
            our_team = {
                "team_id": "anti-villain-league",
                "name": "Anti-Villain League",
                "players": players,
            }
        else:
            opponent_teams.append({
                "team_id": slugify(team["name"]),
                "name": team["name"],
                "players": players,
            })

    # Resolve week: use query param or auto-detect from today's date
    all_matches_data = get_matches_data()
    team_item = next((item for item in all_matches_data if item.get("data", {}).get("team", {}).get("matches")), None)
    all_matches = team_item["data"]["team"]["matches"] if team_item else []
    playable = [m for m in all_matches if not m.get("isBye") and m.get("startTime")]

    week_param = query.get("week")
    if week_param:
        week = int(week_param)
    else:
        now = datetime.now(timezone.utc)
        closest = min(playable, key=lambda m: abs((datetime.fromisoformat(m["startTime"]) - now).total_seconds()), default=None)
        week = closest["week"] if closest else None

    match_info = None
    if week is not None:
        match = next((m for m in playable if m["week"] == week), None)
        if match:
            is_away = "anti vill" in match["away"]["name"].lower()
            opp = match["home"] if is_away else match["away"]
            start = datetime.fromisoformat(match["startTime"])
            match_info = {
                "week": week,
                "date": start.strftime("%Y-%m-%d"),
                "location": match.get("location", {}).get("name", ""),
                "home_team": match["home"]["name"],
                "away_team": match["away"]["name"],
                "opponent_name": opp["name"],
                "opponent_team_id": slugify(opp["name"]),
            }

        # Mark scheduled players from AVL-schedule.csv
        if our_team:
            csv_rows = get_schedule_csv()
            # Build first-name → display-name map; prefix-match handles
            # shortened CSV names (e.g. "Kim" → "Kim-Khanh Van")
            first_to_display = {p["name"].split()[0]: p["name"] for p in our_team["players"]}

            def resolve_csv_name(cell: str) -> str | None:
                if cell in first_to_display:
                    return first_to_display[cell]
                cell_lower = cell.lower()
                for first, display in first_to_display.items():
                    if first.lower().startswith(cell_lower) or cell_lower.startswith(first.lower()):
                        return display
                return None

            scheduled_names: set[str] = set()
            week_rows = [r for r in csv_rows if r.get("Week") == str(week)]
            for row in week_rows[:5]:
                cell = str(row.get("8 ball", "")).strip()
                if cell and cell.lower() not in ("", "nan"):
                    resolved = resolve_csv_name(cell)
                    if resolved:
                        scheduled_names.add(resolved)
            for player in our_team["players"]:
                player["scheduled"] = player["name"] in scheduled_names

    return {"result": {"our_team": our_team, "opponent_teams": opponent_teams, "match_info": match_info}, "error": None}


def handle_division(_body: dict[str, Any], repository: MatchRepository, query: dict[str, str]) -> dict[str, Any]:
    from config_loader import get_matches_data, get_rosters_data, get_schedule_csv
    from datetime import datetime, timezone

    # ── Build roster lookup ──────────────────────────────────────────────────
    raw = get_rosters_data()
    teams_raw = raw[0]["data"]["division"]["teams"]

    our_team_players: list[dict[str, Any]] = []
    teams_out: list[dict[str, Any]] = []

    for team in teams_raw:
        if team.get("isBye"):
            continue
        players = [
            {"name": p["displayName"], "skill_level": p["skillLevel"]}
            for p in team.get("roster", [])
            if p["skillLevel"] > 0
        ]
        if "anti vill" in team["name"].lower():
            our_team_players = players
        else:
            teams_out.append({
                "team_id": slugify(team["name"]),
                "name": team["name"],
                "players": players,
            })

    # First-name prefix map for CSV resolution
    first_to_display = {p["name"].split()[0]: p["name"] for p in our_team_players}

    def resolve_csv_name(cell: str) -> str | None:
        if cell in first_to_display:
            return first_to_display[cell]
        cell_lower = cell.lower()
        for first, display in first_to_display.items():
            if first.lower().startswith(cell_lower) or cell_lower.startswith(first.lower()):
                return display
        return None

    # ── Build schedule list ──────────────────────────────────────────────────
    all_matches_data = get_matches_data()
    team_item = next((item for item in all_matches_data if item.get("data", {}).get("team", {}).get("matches")), None)
    all_matches: list[dict[str, Any]] = team_item["data"]["team"]["matches"] if team_item else []

    csv_rows = get_schedule_csv()

    now = datetime.now(timezone.utc)

    # Auto-detect current week: first future match (>= now); fall back to last
    # past match when all weeks are done.
    playable = [m for m in all_matches if not m.get("isBye") and m.get("startTime")]
    future = [m for m in playable if datetime.fromisoformat(m["startTime"]) >= now]
    next_match = (
        min(future, key=lambda m: datetime.fromisoformat(m["startTime"]))
        if future
        else max(playable, key=lambda m: datetime.fromisoformat(m["startTime"]), default=None)
    )
    current_week: int | None = next_match["week"] if next_match else None

    # ── Assemble schedule entries ────────────────────────────────────────────
    schedule_out: list[dict[str, Any]] = []
    for match in all_matches:
        week = match["week"]
        is_bye = bool(match.get("isBye"))
        is_playoff = bool(match.get("isPlayoff"))
        status = match.get("status", "UNPLAYED")
        start_iso = match.get("startTime")
        date_str = datetime.fromisoformat(start_iso).strftime("%Y-%m-%d") if start_iso else ""

        if is_bye:
            schedule_out.append({
                "week": week,
                "date": date_str,
                "opponent": "Bye",
                "opponent_team_id": None,
                "apa_match_id": None,
                "location": "",
                "is_home": True,
                "is_bye": True,
                "is_playoff": is_playoff,
                "status": status,
                "scheduled_players": [],
                "result": None,
            })
            continue

        away_info = match.get("away") or {}
        home_info = match.get("home") or {}
        is_away = "anti vill" in away_info.get("name", "").lower()
        opp_raw = home_info if is_away else away_info
        is_home = not is_away
        opp_name = opp_raw.get("name", "")
        if not opp_name:
            continue
        opp_team_id = slugify(opp_name)
        location = (match.get("location") or {}).get("name", "")

        # Scheduled players from CSV for this week
        scheduled_players: list[str] = []
        week_rows = [r for r in csv_rows if r.get("Week") == str(week)]
        for row in week_rows[:5]:
            cell = str(row.get("8 ball", "")).strip()
            if cell and cell.lower() not in ("", "nan"):
                resolved = resolve_csv_name(cell)
                if resolved:
                    scheduled_players.append(resolved)

        # Fetch result directly by APA match ID — only for past matches
        result_entry = None
        apa_match_id = str(match.get("id", ""))
        match_dt = datetime.fromisoformat(start_iso) if start_iso else None
        if apa_match_id and match_dt is not None and match_dt < now:
            try:
                result_entry = repository.get_match_result(apa_match_id)
            except Exception:
                pass

        schedule_out.append({
            "week": week,
            "date": date_str,
            "opponent": opp_name,
            "opponent_team_id": opp_team_id,
            "apa_match_id": str(match.get("id", "")),
            "location": location,
            "is_home": is_home,
            "is_bye": False,
            "is_playoff": is_playoff,
            "status": status,
            "scheduled_players": scheduled_players,
            "result": result_entry,
        })

    return {
        "result": {
            "current_week": current_week,
            "schedule": schedule_out,
            "teams": teams_out,
        },
        "error": None,
    }


def handle_players(body: dict[str, Any], query: dict[str, str]) -> dict[str, Any]:
    from player_data import get_player, get_sessions

    name      = query.get("name") or body.get("name")
    member_id = query.get("member_id") or body.get("member_id")

    if not name and not member_id:
        raise ValueError("Provide 'name' or 'member_id' as a query param or body field")

    player = get_player(name=name, member_id=member_id)
    if not player:
        raise NotFound(f"Player not found: {name or member_id}")

    sessions = get_sessions(player["member_id"])
    return {"result": {"player": player, "sessions": sessions}, "error": None}


def handle_players_profile(body: dict[str, Any], query: dict[str, str], repository: MatchRepository) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timezone
    from player_data import get_player, get_sessions, get_apr, apr_band

    name = query.get("name") or body.get("name")
    member_id = query.get("member_id") or body.get("member_id")
    player_sl = query.get("player_sl") or body.get("player_sl")
    opponent_player_id = query.get("opponent_player_id") or body.get("opponent_player_id")

    if not name and not member_id:
        raise ValueError("Provide 'name' or 'member_id'")

    player_sl_param = int(player_sl) if player_sl is not None else None

    # SL peer baseline constants from spec Section 3
    _PEER_BASELINE = {2: 0.38, 3: 0.42, 4: 0.47, 5: 0.52, 6: 0.57, 7: 0.63}

    futures = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures["player"] = executor.submit(get_player, name=name, member_id=member_id)

        def _fetch_sessions(nm, mid):
            p = get_player(name=nm, member_id=mid)
            if not p:
                return None, [], None
            sessions = get_sessions(p["member_id"], game="eight_ball")
            try:
                apr_row = get_apr(p["member_id"])
            except Exception:
                apr_row = None
            return p, sessions, apr_row

        futures["sessions"] = executor.submit(_fetch_sessions, name, member_id)

        if opponent_player_id:
            def _fetch_h2h(our_name, opp_id):
                if not our_name:
                    return None
                our_id = player_id_from_name(our_name)
                h2h_rows = repository.get_player_h2h(our_id)
                target_sk = f"H2H#{opp_id}"
                for row in h2h_rows:
                    if row.get("SK") == target_sk:
                        return row
                return None
            futures["h2h"] = executor.submit(_fetch_h2h, name, opponent_player_id)

        results = {key: f.result() for key, f in futures.items()}

    player = results["player"]
    if player is None:
        return {"result": {"player": None, "form": None, "narrative": None, "recent_sessions": [], "neon_found": False}, "error": None}

    player_sl = player_sl_param if player_sl_param is not None else int(player.get("skill_level") or 5)
    peer_baseline = _PEER_BASELINE.get(player_sl, 0.50)

    _player_obj, sessions, apr_row = results["sessions"]

    # Form badge: accumulate recent matches from sessions (most recent first)
    recent_wins = 0
    recent_played = 0
    recent_sessions_out = []

    for s in sessions:
        won = int(s.get("matches_won") or 0)
        played = int(s.get("matches_played") or 0)
        if recent_played < 20:
            take = min(played, 20 - recent_played)
            recent_wins += round(won / played * take) if played else 0
            recent_played += take
        recent_sessions_out.append({
            "session_name": s.get("session_name") or s.get("session_id"),
            "matches_won": won,
            "matches_played": played,
            "team_name": s.get("team_name"),
        })

    # Cap output to 5 sessions
    recent_sessions_out = recent_sessions_out[:5]

    recent_win_rate = (recent_wins / recent_played) if recent_played else 0.0
    delta = recent_win_rate - peer_baseline
    if delta > 0.10:
        badge = "hot"
    elif delta < -0.10:
        badge = "low"
    else:
        badge = "mid"
    reliable = recent_played >= 5

    eb_win_pct = float(player.get("eb_win_pct") or 0)
    narrative_fallback = (
        f"{recent_wins} wins in last {recent_played}. "
        f"Win rate {round(eb_win_pct)}% vs {round(peer_baseline * 100)}% peer avg."
    )
    try:
        from chat_handler import _query_groq
        prompt = (
            f"In 1-2 sentences, describe {player.get('display_name', name)}'s recent form for an APA 8-ball team captain. "
            f"Recent record: {recent_wins}/{recent_played}. Badge: {badge}. "
            f"Key stats: lifetime win%={round(eb_win_pct)}%, matches={player.get('eb_matches_played', 0)}, "
            f"DSA={player.get('eb_defensive_shot_avg', 'N/A')}."
        )
        messages = [
            {"role": "system", "content": "You are a concise APA pool league analyst. Return exactly 1-2 sentences."},
            {"role": "user", "content": prompt},
        ]
        narrative = _query_groq(messages, temperature=0.2)
    except Exception as exc:  # noqa: BLE001 - narrative is helpful, not route-critical
        logger.info("Groq narrative skipped for profile: %s", exc)
        narrative = narrative_fallback

    h2h = None
    if opponent_player_id:
        h2h_row = results.get("h2h")
        if h2h_row:
            h2h = {
                "opponent_player_id": opponent_player_id,
                "wins": int(h2h_row.get("wins") or 0),
                "losses": int(h2h_row.get("losses") or 0),
            }
        else:
            h2h = None  # record exists param but no DynamoDB entry

    apr_payload: dict[str, Any] | None = None
    if apr_row and apr_row.get("apr") is not None:
        apr_score = apr_row["apr"]
        apr_payload = {
            "score": round(apr_score, 1),
            "band": apr_band(apr_score),
            "match_count": apr_row.get("match_count"),
            "mps": round(apr_row.get("mps") or 0, 1),
            "ppms": round(apr_row.get("ppms") or 0, 1),
            "pas": round(apr_row.get("pas") or 0, 1),
            "oss": round(apr_row.get("oss") or 0, 1),
            "cs": round(apr_row.get("cs") or 0, 1),
        }

    result_payload: dict[str, Any] = {
        "player": {
            "display_name": player.get("display_name"),
            "member_id": player.get("member_id"),
            "skill_level": player_sl,
            "eb_win_pct": eb_win_pct,
            "eb_matches_played": player.get("eb_matches_played"),
            "eb_matches_won": player.get("eb_matches_won"),
            "eb_rackless": player.get("eb_rackless"),
            "eb_break_and_runs": player.get("eb_break_and_runs"),
            "eb_defensive_shot_avg": player.get("eb_defensive_shot_avg"),
            "avg_opponent_sl": player.get("avg_opponent_sl"),
            "apr": apr_payload,
        },
        "form": {
            "badge": badge,
            "recent_win_rate": round(recent_win_rate, 4),
            "recent_played": recent_played,
            "peer_baseline": peer_baseline,
            "delta": round(delta, 4),
            "reliable": reliable,
        },
        "narrative": narrative,
        "recent_sessions": recent_sessions_out,
        "neon_found": True,
        "cached_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if opponent_player_id is not None:
        result_payload["h2h"] = h2h

    return {"result": result_payload, "error": None}


def handle_players_search(body: dict[str, Any]) -> dict[str, Any]:
    from player_data import search_chunks, neon_query

    query = str(body.get("query", "")).strip()
    if not query:
        raise ValueError("'query' field is required")

    # Structured leaderboard shortcuts
    q_lower = query.lower()
    if "rackless" in q_lower:
        rows = neon_query(
            "SELECT display_name, eb_rackless, eb_win_pct, eb_matches_played "
            "FROM players WHERE eb_rackless > 0 ORDER BY eb_rackless DESC LIMIT 10"
        )
        return {"result": {"type": "leaderboard", "field": "rackless", "rows": rows}, "error": None}

    if "break" in q_lower and ("run" in q_lower or "and" in q_lower):
        rows = neon_query(
            "SELECT display_name, eb_break_and_runs, nb_break_and_runs "
            "FROM players WHERE eb_break_and_runs > 0 OR nb_break_and_runs > 0 "
            "ORDER BY (COALESCE(eb_break_and_runs,0)+COALESCE(nb_break_and_runs,0)) DESC LIMIT 10"
        )
        return {"result": {"type": "leaderboard", "field": "break_and_runs", "rows": rows}, "error": None}

    if "win" in q_lower and ("rate" in q_lower or "pct" in q_lower or "percent" in q_lower or "best" in q_lower):
        rows = neon_query(
            "SELECT display_name, eb_matches_won, eb_matches_played, eb_win_pct "
            "FROM players WHERE eb_matches_played >= 10 ORDER BY eb_win_pct DESC LIMIT 10"
        )
        return {"result": {"type": "leaderboard", "field": "win_pct", "rows": rows}, "error": None}

    # Fall back to full-text chunk search
    chunks = search_chunks(query, top_k=int(body.get("top_k", 5)))
    return {"result": {"type": "chunks", "chunks": chunks}, "error": None}



# Lambda timeout is set to >= 15 s (see engineer-decisions.md STORY-015).
# tokeninfo call gets 5 s; remaining budget covers route handlers.
_TOKENINFO_TIMEOUT_SECONDS = 5


def _validate_auth(event: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    auth_header = headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return None, _response(401, {"result": None, "error": "Unauthorized"})

    token = auth_header[len("Bearer "):]
    try:
        url = f"https://oauth2.googleapis.com/tokeninfo?id_token={urllib.parse.quote(token, safe='')}"
        with urllib.request.urlopen(url, timeout=_TOKENINFO_TIMEOUT_SECONDS) as resp:
            tokeninfo = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None, _response(401, {"result": None, "error": "Unauthorized"})

    expected_client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    if tokeninfo.get("aud") != expected_client_id:
        return None, _response(401, {"result": None, "error": "Unauthorized"})

    if int(tokeninfo.get("exp", 0)) <= int(time.time()):
        return None, _response(401, {"result": None, "error": "Unauthorized"})

    from config_loader import load_config
    allowlist = load_config("authorized-users.json")
    if not allowlist:
        logger.warning("authorized-users.json is empty — no one is authorized")
        return None, _response(401, {"result": None, "error": "Unauthorized"})

    email = tokeninfo.get("email", "")
    allowed_emails = [u["email"].lower() for u in allowlist]
    if email.lower() not in allowed_emails:
        return None, _response(403, {"result": None, "error": "Access denied"})

    return email, None


def lambda_handler(event, context):  # noqa: ARG001
    try:
        method, path = _method_and_path(event)
        if method == "OPTIONS":
            return _response(200, {})

        _, auth_error = _validate_auth(event)
        if auth_error:
            return auth_error

        body = _parse_body(event)
        query = event.get("queryStringParameters") or {}
        repository = MatchRepository()

        logger.info("Invoking %s %s", method, path)
        if method == "POST" and path == "/match":
            result = handle_match(body, repository)
        elif method == "POST" and path == "/suggest":
            result = handle_suggest(body, repository)
        elif method == "POST" and path == "/chat":
            result = handle_chat(body, repository)
        elif method == "POST" and path == "/result":
            result = handle_result(body, repository)
        elif method == "POST" and path == "/submit":
            result = handle_submit(body, repository)
        elif method == "GET" and path == "/history":
            result = handle_history(body, repository, query)
        elif method == "DELETE" and path == "/match":
            result = handle_delete_match(body, repository)
        elif method == "GET" and path == "/rosters":
            result = handle_rosters(body, repository, query)
        elif method == "GET" and path == "/division":
            result = handle_division(body, repository, query)
        elif method in {"GET", "POST"} and path == "/players":
            result = handle_players(body, query)
        elif method == "GET" and path == "/players/profile":
            result = handle_players_profile(body, query, repository)
        elif method == "POST" and path == "/players/search":
            result = handle_players_search(body)
        else:
            return _response(404, {"result": None, "error": f"Unknown route: {method} {path}"})

        return _response(200, result)

    except NotFound as exc:
        return _response(404, {"result": None, "error": str(exc)})
    except (KeyError, ValueError, RuleViolation) as exc:
        logger.warning("Client error: %s", exc)
        return _response(400, {"result": None, "error": str(exc)})
    except RuntimeError as exc:
        logger.warning("Dependency error: %s", exc)
        return _response(502, {"result": None, "error": str(exc)})
    except Exception:  # noqa: BLE001
        logger.exception("Unhandled error in lambda_handler")
        return _response(500, {"result": None, "error": "Internal server error"})


def _method_and_path(event: dict[str, Any]) -> tuple[str, str]:
    request_context = event.get("requestContext", {})
    http_context = request_context.get("http", {})
    method = event.get("httpMethod") or http_context.get("method") or "GET"
    path = event.get("path") or event.get("rawPath") or "/"
    return method.upper(), path


def _parse_body(event: dict[str, Any]) -> dict[str, Any]:
    raw_body = event.get("body") or "{}"
    if isinstance(raw_body, dict):
        return raw_body
    if event.get("isBase64Encoded"):
        import base64

        raw_body = base64.b64decode(raw_body).decode("utf-8")
    return json.loads(raw_body)


def _context_from_body_or_match(body: dict[str, Any], repository: MatchRepository) -> dict[str, Any]:
    if body.get("match_context"):
        return body["match_context"]
    if body.get("match_id"):
        loaded = repository.get_match(str(body["match_id"]))
        if not loaded:
            raise NotFound(f"Match {body['match_id']} not found")
        return loaded["match_context"]
    return {}


def _enrich_with_history(match_context: dict[str, Any], repository: MatchRepository) -> dict[str, Any]:
    enriched = dict(match_context or {})
    try:
        from data_access import build_history_context
        enriched["match_history"] = build_history_context(repository)
    except Exception as exc:  # noqa: BLE001 - history is helpful, not route-critical.
        logger.info("Skipping history enrichment: %s", exc)
    return enriched


def _enrich_with_h2h(match_context: dict[str, Any], repository: MatchRepository) -> dict[str, Any]:
    enriched = dict(match_context or {})
    h2h = dict(enriched.get("head_to_head", {}))

    # Only surface H2H records against players on the current opponent's roster.
    # Without this filter the LLM hallucinates matchups against players from past matches.
    their_roster = enriched.get("their_roster") or {}
    opponent_ids = {player_id_from_name(name) for name in their_roster}

    for player_name in (enriched.get("our_roster") or {}).keys():
        player_id = player_id_from_name(player_name)
        try:
            records = repository.get_player_h2h(player_id)
            if opponent_ids:
                records = [
                    r for r in records
                    if str(r.get("SK", "")).replace("H2H#", "") in opponent_ids
                ]
            h2h[player_name] = records
        except Exception as exc:  # noqa: BLE001 - h2h is helpful, not route-critical.
            logger.info("Skipping H2H enrichment for %s: %s", player_name, exc)
    enriched["head_to_head"] = h2h
    return enriched


def _player_name(turn_body: dict[str, Any], match: dict[str, Any], *, side: str) -> str:
    key_prefixes = ["our"] if side == "our" else ["their", "opponent"]
    for prefix in key_prefixes:
        value = turn_body.get(f"{prefix}_player_name") or turn_body.get(f"{prefix}_player")
        if value:
            return str(value)

    player_id = None
    for prefix in key_prefixes:
        player_id = turn_body.get(f"{prefix}_player_id")
        if player_id:
            break
    roster = (match.get("home_roster" if side == "our" else "away_roster")
              or match.get("our_roster" if side == "our" else "their_roster", {}))
    if player_id:
        expected_id = str(player_id)
        for name in roster:
            if player_id_from_name(name) == expected_id:
                return name
    raise ValueError(f"{side}_player_name is required")


def _skill_level(turn_body: dict[str, Any], match: dict[str, Any], name: str, *, side: str) -> int:
    if side == "our":
        explicit = turn_body.get("our_sl_snapshot") or turn_body.get("our_sl")
        roster = match.get("home_roster") or match.get("our_roster", {})
    else:
        explicit = turn_body.get("their_sl_snapshot") or turn_body.get("their_sl") or turn_body.get("opponent_sl")
        roster = match.get("away_roster") or match.get("their_roster", {})
    if explicit is not None:
        return int(explicit)
    if name not in roster:
        raise ValueError(f"Skill level for {name} is required")
    return int(roster[name])


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
        },
        "body": json.dumps(body, default=str),
    }
