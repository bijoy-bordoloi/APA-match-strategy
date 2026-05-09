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
        item = {
            **match,
            "PK": f"MATCH#{match_id}",
            "SK": "METADATA",
            "entity_type": "MATCH",
            "match_id": match_id,
            "status": match.get("status", "planned"),
            "mode": match.get("mode", "regular"),
            "created_at": match.get("created_at", now),
            "updated_at": now,
        }
        self.table.put_item(Item=_clean_for_dynamo(item))
        return _from_dynamo(item)

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
        item = {
            **turn,
            "PK": f"MATCH#{match_id}",
            "SK": f"TURN#{turn_num:02d}",
            "entity_type": "TURN",
            "match_id": match_id,
            "updated_at": utc_now(),
        }
        self.table.put_item(Item=_clean_for_dynamo(item))
        return _from_dynamo(item)

    def replace_turns(self, match_id: str, turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Overwrite all turn rows for a match.

        This powers the summary/history edit flow. It deletes stale TURN rows
        first so a playoff edit from five turns down to three cannot leave
        old rows hanging around in history.
        """
        existing = self.get_match(match_id)
        old_turns = existing["turns"] if existing else []
        with self.table.batch_writer() as batch:
            for turn in old_turns:
                batch.delete_item(Key={"PK": f"MATCH#{match_id}", "SK": turn["SK"]})
            for turn in turns:
                turn_num = int(turn["turn_num"])
                item = {
                    **turn,
                    "PK": f"MATCH#{match_id}",
                    "SK": f"TURN#{turn_num:02d}",
                    "entity_type": "TURN",
                    "match_id": match_id,
                    "updated_at": utc_now(),
                }
                batch.put_item(Item=_clean_for_dynamo(item))
        return sorted_turns(turns)

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

    def list_matches(self, status: str | None = "complete", limit: int = 25) -> list[dict[str, Any]]:
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


def build_history_response(repository: MatchRepository, *, status: str = "complete", limit: int = 25) -> dict[str, Any]:
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
            name = turn.get("our_player_name") or turn.get("our_player_id")
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
            stats["points"] += int(turn.get("our_score", 0))
            if int(turn.get("our_score", 0)) >= 2:
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
