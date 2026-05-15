"""
One-time migration: rename our_*/their_* field names to home_*/away_* on all
METADATA and TURN# items in the DynamoDB table.

Run once:
    python3 migrate_home_away.py [--dry-run]
"""

import argparse
import boto3
from boto3.dynamodb.conditions import Attr

TABLE_NAME = "apa-match-engine"
REGION = "us-west-1"

MATCH_RENAMES = {
    "our_team_name": "home_team_name",
    "opponent_team_name": "away_team_name",
    "our_roster": "home_roster",
    "their_roster": "away_roster",
}
TURN_RENAMES = {
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


def migrate_item(item: dict, renames: dict) -> dict | None:
    """Return updated item with renamed fields, or None if nothing changed."""
    changed = False
    updated = dict(item)
    for old, new in renames.items():
        if old in updated and new not in updated:
            updated[new] = updated.pop(old)
            changed = True
    return updated if changed else None


def run(dry_run: bool = False):
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.Table(TABLE_NAME)

    metadata_updated = 0
    turn_updated = 0
    skipped = 0

    paginator = boto3.client("dynamodb", region_name=REGION).get_paginator("scan")
    pages = paginator.paginate(TableName=TABLE_NAME)

    for page in pages:
        for raw_item in page["Items"]:
            # Deserialize from DynamoDB format
            item = {k: list(v.values())[0] for k, v in raw_item.items()}
            sk = item.get("SK", "")

            if sk == "METADATA":
                renames = MATCH_RENAMES
                kind = "METADATA"
            elif str(sk).startswith("TURN#"):
                renames = TURN_RENAMES
                kind = "TURN"
            else:
                skipped += 1
                continue

            # Check if any old field names exist
            if not any(old in raw_item for old in renames):
                skipped += 1
                continue

            print(f"  [{kind}] PK={item.get('PK')} SK={sk}")
            for old, new in renames.items():
                if old in raw_item:
                    print(f"    {old} → {new}")

            if not dry_run:
                # Use put_item with the full updated item (simpler than update_item expressions)
                updated_item = migrate_item(
                    {k: list(v.values())[0] for k, v in raw_item.items()},
                    renames,
                )
                if updated_item:
                    # Re-serialize for put_item (use high-level resource which handles types)
                    table.put_item(Item=updated_item)

            if kind == "METADATA":
                metadata_updated += 1
            else:
                turn_updated += 1

    mode = "DRY RUN" if dry_run else "APPLIED"
    print(f"\n[{mode}] METADATA updated: {metadata_updated}, TURN updated: {turn_updated}, skipped: {skipped}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
