"""DynamoDB single-table access for the APA match engine."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from match_rules import build_llm_context, player_id_from_name, sorted_turns, summarize_match

try:  # Lambda includes boto3; local rule tests should still import cleanly.
    from boto3.dynamodb.conditions import Key
except ModuleNotFoundError:  # pragma: no cover - exercised only without deps installed.
    Key = None


DEFAULT_TABLE_NAME = "apa-match-engine"
STATUS_INDEX_NAME = "StatusDateIndex"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Field rename map applied at write time so DynamoDB stores neutral home/away names.
# Reads use compat fallbacks (home_* or our_*) for backward compat with existing data.
_MATCH_FIELD_MAP = {
    "our_team_name": "home_team_name",
    "opponent_team_name": "away_team_name",
    "our_roster": "home_roster",
    "their_roster": "away_roster",
}
_TURN_FIELD_MAP = {
    "our_player_name": "home_player_name",
    "our_player_id": "home_player_id",
    "our_score": "home_score",
    "our_sl_snapshot": "home_sl_snapshot",
    "is_our_dp": "is_home_dp",
    "their_player_name": "away_player_name",
    "their_player_id": "away_player_id",
    "their_score": "away_score",
    "their_sl_snapshot": "away_sl_snapshot",
    "is_their_dp": "is_away_dp",
}


def _rename_fields(d: dict[str, Any], field_map: dict[str, str]) -> dict[str, Any]:
    result = dict(d)
    for old, new in field_map.items():
        if old in result and new not in result:
            result[new] = result.pop(old)
    return result


def slugify(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value)).strip("-")
    return "-".join(part for part in slug.split("-") if part) or str(uuid.uuid4())


class MatchRepository:
    """Repository for the table layout described in the requirements."""

    def __init__(self, table_name: str | None = None, table: Any | None = None):
        self.table_name = table_name or os.environ.get("APA_TABLE_NAME", DEFAULT_TABLE_NAME)
        if table is None:
            import boto3

            table = boto3.resource("dynamodb").Table(self.table_name)
        self.table = table

    def put_team(self, team_id: str, name: str, players: list[dict[str, Any]]) -> None:
        team_pk = f"TEAM#{team_id}"
        self.table.put_item(
            Item=_clean_for_dynamo(
                {
                    "PK": team_pk,
                    "SK": "METADATA",
                    "entity_type": "TEAM",
                    "team_id": team_id,
                    "name": name,
                    "updated_at": utc_now(),
                }
            )
        )
        for player in players:
            player_id = str(player.get("player_id") or player_id_from_name(player["name"]))
            self.table.put_item(
                Item=_clean_for_dynamo(
                    {
                        "PK": team_pk,
                        "SK": f"PLAYER#{player_id}",
                        "entity_type": "PLAYER",
                        "team_id": team_id,
                        "player_id": player_id,
                        "name": player["name"],
                        "skill_level": int(player["skill_level"]),
                        "updated_at": utc_now(),
                    }
                )
            )

    def get_team(self, team_id: str) -> dict[str, Any] | None:
        response = self.table.query(KeyConditionExpression=Key("PK").eq(f"TEAM#{team_id}"))
        metadata = None
        players = []
        for item in response.get("Items", []):
            item = _from_dynamo(item)
            if item.get("SK") == "METADATA":
                metadata = item
            elif str(item.get("SK", "")).startswith("PLAYER#"):
                players.append(item)
        if not metadata:
            return None
        return {**metadata, "players": sorted(players, key=lambda player: player["name"])}

    def put_match(self, match: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        match_id = str(match.get("match_id") or uuid.uuid4())
        translated = _rename_fields(match, _MATCH_FIELD_MAP)
        item = {
            **translated,
            "PK": f"MATCH#{match_id}",
            "SK": "METADATA",
            "entity_type": "MATCH",
            "match_id": match_id,
            "status": translated.get("status", "planned"),
            "mode": translated.get("mode", "regular"),
            "created_at": translated.get("created_at", now),
            "updated_at": now,
        }
        self.table.put_item(Item=_clean_for_dynamo(item))
        return _from_dynamo(item)

    def get_match_result(self, match_id: str) -> dict[str, Any] | None:
        """Fetch score/outcome from METADATA only — no turns loaded.

        Returns {our_score, their_score, outcome} for complete matches, else None.
        our_score/their_score are individual games won (e.g. 4 and 1).
        """
        resp = self.table.get_item(Key={"PK": f"MATCH#{match_id}", "SK": "METADATA"})
        raw = resp.get("Item")
        if not raw:
            return None
        item = _from_dynamo(raw)
        if item.get("status") != "complete":
            return None
        our = int(item.get("our_matches_won") or 0)
        their = int(item.get("their_matches_won") or 0)
        outcome = "win" if our > their else ("loss" if their > our else "tie")
        return {"our_score": our, "their_score": their, "outcome": outcome}

    def get_match(self, match_id: str) -> dict[str, Any] | None:
        response = self.table.query(KeyConditionExpression=Key("PK").eq(f"MATCH#{match_id}"))
        metadata = None
        turns = []
        for item in response.get("Items", []):
            item = _from_dynamo(item)
            if item.get("SK") == "METADATA":
                metadata = item
            elif str(item.get("SK", "")).startswith("TURN#"):
                turns.append(item)
        if not metadata:
            return None
        turns = sorted_turns(turns)
        summary = summarize_match(metadata, turns)
        return {
            "match": metadata,
            "turns": turns,
            "summary": summary,
            "match_context": build_llm_context(metadata, turns),
        }

    def put_turn(self, match_id: str, turn: dict[str, Any]) -> dict[str, Any]:
        turn_num = int(turn["turn_num"])
        translated = _rename_fields(turn, _TURN_FIELD_MAP)
        item = {
            **translated,
            "PK": f"MATCH#{match_id}",
            "SK": f"TURN#{turn_num:02d}",
            "entity_type": "TURN",
            "match_id": match_id,
            "updated_at": utc_now(),
        }
        self.table.put_item(Item=_clean_for_dynamo(item))
        return _from_dynamo(item)

    def replace_turns(self, match_id: str, turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Overwrite all turn rows for a match."""
        existing = self.get_match(match_id)
        old_turns = existing["turns"] if existing else []
        with self.table.batch_writer() as batch:
            for turn in old_turns:
                batch.delete_item(Key={"PK": f"MATCH#{match_id}", "SK": turn["SK"]})
            for turn in turns:
                turn_num = int(turn["turn_num"])
                translated = _rename_fields(turn, _TURN_FIELD_MAP)
                item = {
                    **translated,
                    "PK": f"MATCH#{match_id}",
                    "SK": f"TURN#{turn_num:02d}",
                    "entity_type": "TURN",
                    "match_id": match_id,
                    "updated_at": utc_now(),
                }
                batch.put_item(Item=_clean_for_dynamo(item))
        return sorted_turns(turns)

    def delete_match(self, match_id: str) -> None:
        """Delete all DynamoDB items for a match (METADATA + all TURN# rows)."""
        response = self.table.query(KeyConditionExpression=Key("PK").eq(f"MATCH#{match_id}"))
        with self.table.batch_writer() as batch:
            for item in response.get("Items", []):
                batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})

    def set_match_status(self, match_id: str, status: str) -> None:
        self.table.update_item(
            Key={"PK": f"MATCH#{match_id}", "SK": "METADATA"},
            UpdateExpression="SET #status = :status, updated_at = :updated_at",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": status,
                ":updated_at": utc_now(),
            },
        )

    def list_matches(self, status: str | None = "complete", limit: int = 200) -> list[dict[str, Any]]:
        statuses = [status] if status else ["live", "planned", "complete"]
        items = []
        for status_value in statuses:
            response = self.table.query(
                IndexName=STATUS_INDEX_NAME,
                KeyConditionExpression=Key("status").eq(status_value),
                ScanIndexForward=False,
                Limit=limit,
            )
            items.extend(response.get("Items", []))

        matches = [_from_dynamo(item) for item in items]
        return sorted(matches, key=lambda item: str(item.get("date", "")), reverse=True)[:limit]

    def update_h2h(self, our_player_id: str, their_player_id: str, *, won: bool) -> None:
        win_inc = 1 if won else 0
        loss_inc = 0 if won else 1
        self.table.update_item(
            Key={"PK": f"PLAYER#{our_player_id}", "SK": f"H2H#{their_player_id}"},
            UpdateExpression=(
                "SET entity_type = :entity_type, our_player_id = :our_player_id, "
                "opponent_player_id = :their_player_id, updated_at = :updated_at "
                "ADD wins :wins, losses :losses"
            ),
            ExpressionAttributeValues={
                ":entity_type": "H2H",
                ":our_player_id": our_player_id,
                ":their_player_id": their_player_id,
                ":updated_at": utc_now(),
                ":wins": win_inc,
                ":losses": loss_inc,
            },
        )

    def get_player_h2h(self, player_id: str) -> list[dict[str, Any]]:
        response = self.table.query(
            KeyConditionExpression=Key("PK").eq(f"PLAYER#{player_id}") & Key("SK").begins_with("H2H#")
        )
        return [_from_dynamo(item) for item in response.get("Items", [])]


def build_history_context(repository: MatchRepository, limit: int = 8) -> dict[str, Any]:
    """Compact match history summary for LLM context (season record, player form, recent results)."""
    history = build_history_response(repository, status="complete", limit=limit)

    team_wins = sum(
        1 for m in history["matches"]
        if m.get("summary", {}).get("result") in {"points_win", "victory"}
    )
    team_losses = len(history["matches"]) - team_wins

    recent = [
        {
            "opponent": m.get("away_team_name") or m.get("opponent_team_name"),
            "date": m.get("date"),
            "result": m.get("summary", {}).get("result"),
            "our_score": m.get("summary", {}).get("our_score"),
            "their_score": m.get("summary", {}).get("their_score"),
        }
        for m in history["matches"]
    ]

    return {
        "season_record": {"wins": team_wins, "losses": team_losses},
        "player_history": {
            s["player_name"]: {
                "wins": s["wins"],
                "losses": s["losses"],
                "appearances": s["appearances"],
            }
            for s in history["player_stats"]
        },
        "recent_matches": recent,
    }


def build_history_response(repository: MatchRepository, *, status: str = "complete", limit: int = 200) -> dict[str, Any]:
    matches = repository.list_matches(status=status, limit=limit)
    expanded = []
    player_stats: dict[str, dict[str, Any]] = {}

    for match in matches:
        loaded = repository.get_match(match["match_id"])
        if not loaded:
            continue
        turns = loaded["turns"]
        expanded.append(
            {
                **match,
                "turns": turns,
                "summary": loaded["summary"],
            }
        )
        for turn in turns:
            name = turn.get("home_player_name") or turn.get("our_player_name") or turn.get("our_player_id")
            score = int(turn.get("home_score") or turn.get("our_score") or 0)
            if not name:
                continue
            stats = player_stats.setdefault(
                name,
                {
                    "player_name": name,
                    "wins": 0,
                    "losses": 0,
                    "points": 0,
                    "appearances": 0,
                },
            )
            stats["appearances"] += 1
            stats["points"] += score
            if score >= 2:
                stats["wins"] += 1
            else:
                stats["losses"] += 1

    return {
        "matches": expanded,
        "player_stats": sorted(player_stats.values(), key=lambda item: (-item["wins"], item["player_name"])),
    }


def _clean_for_dynamo(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _clean_for_dynamo(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_clean_for_dynamo(item) for item in value]
    if isinstance(value, float):
        return Decimal(str(value))
    return value


def _from_dynamo(value: Any) -> Any:
    if isinstance(value, list):
        return [_from_dynamo(item) for item in value]
    if isinstance(value, dict):
        return {key: _from_dynamo(item) for key, item in value.items()}
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    return value
