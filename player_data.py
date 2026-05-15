"""
Neon player stats access for Lambda — uses the Neon HTTP SQL API
so no binary dependencies (psycopg2) are needed.

Public API:
    neon_query(sql, params)           raw query → list[dict]
    get_player(name, member_id)       lookup one player row
    get_sessions(member_id, game)     session rows for a player
    extract_player_names(message)     find player names mentioned in text
    build_player_context(message)     fetch stats for all mentioned players → str
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
from typing import Any

log = logging.getLogger(__name__)

import requests

NEON_SQL_URL: str | None = None
NEON_CONN_STR: str | None = None
_PLAYER_NAME_CACHE: list[str] | None = None


def _init() -> tuple[str, str]:
    global NEON_SQL_URL, NEON_CONN_STR
    if NEON_SQL_URL:
        return NEON_SQL_URL, NEON_CONN_STR

    conn = os.environ.get("NEON_DATABASE_URL", "")
    if not conn:
        raise RuntimeError("NEON_DATABASE_URL is not set")

    # Extract hostname from connection string
    # postgresql://user:pass@hostname/db?...
    match = re.search(r"@([^/]+)/", conn)
    if not match:
        raise RuntimeError(f"Cannot parse hostname from NEON_DATABASE_URL")

    NEON_CONN_STR = conn.split("?")[0]          # strip ?sslmode=require etc.
    NEON_SQL_URL  = f"https://{match.group(1)}/sql"
    return NEON_SQL_URL, NEON_CONN_STR


def neon_query(sql: str, params: list | None = None) -> list[dict[str, Any]]:
    """Execute SQL against Neon and return rows as list of dicts."""
    url, conn_str = _init()
    body: dict[str, Any] = {"query": sql}
    if params:
        body["params"] = params

    resp = requests.post(
        url,
        headers={
            "Content-Type": "application/json",
            "Neon-Connection-String": conn_str,
        },
        data=json.dumps(body),
        timeout=8,
    )
    resp.raise_for_status()
    data = resp.json()
    if "message" in data:
        raise RuntimeError(f"Neon query error: {data['message']}")
    return data.get("rows", [])


# ── Player lookups ────────────────────────────────────────────────────────────

def get_player(name: str | None = None, member_id: str | None = None) -> dict | None:
    if member_id:
        rows = neon_query("SELECT * FROM players WHERE member_id = $1", [member_id])
        return rows[0] if rows else None
    if not name:
        return None
    # 1. Full-name substring match
    rows = neon_query(
        "SELECT * FROM players WHERE display_name ILIKE $1 LIMIT 1",
        [f"%{name}%"],
    )
    if rows:
        return rows[0]
    # 2. All significant words present (handles middle-name differences or minor reordering)
    words = [w for w in name.split() if len(w) >= 3]
    if len(words) >= 2:
        where = " AND ".join(f"display_name ILIKE ${i + 1}" for i in range(len(words)))
        rows = neon_query(
            f"SELECT * FROM players WHERE {where} LIMIT 1",
            [f"%{w}%" for w in words],
        )
        if rows:
            return rows[0]
    return None


def get_sessions(member_id: str, game: str | None = None) -> list[dict]:
    if game:
        return neon_query(
            "SELECT * FROM sessions WHERE member_id = $1 AND game = $2 ORDER BY session_id DESC",
            [member_id, game],
        )
    return neon_query(
        "SELECT * FROM sessions WHERE member_id = $1 ORDER BY session_id DESC",
        [member_id],
    )


def get_apr(member_id: str) -> dict | None:
    """Fetch APR row for a player. Returns None if not yet computed."""
    rows = neon_query(
        "SELECT * FROM player_apr WHERE member_id = $1",
        [member_id],
    )
    return rows[0] if rows else None


def apr_band(score: float) -> str:
    if score >= 90: return "Elite"
    if score >= 75: return "Strong"
    if score >= 60: return "Solid"
    if score >= 45: return "Average"
    if score >= 30: return "Weak"
    return "Low"


def get_apr_for_names(names: list[str]) -> dict[str, dict]:
    """Return {display_name: apr_row} for a list of player names.
    Uses individual lookups — safe for small lists (≤7 eligible players).
    """
    result = {}
    for name in names:
        try:
            player = get_player(name=name)
            if not player:
                continue
            apr = get_apr(player["member_id"])
            if apr:
                result[player["display_name"]] = apr
        except Exception:
            log.debug("APR lookup failed for %r — skipping", name)
    return result


def search_chunks(query_text: str, top_k: int = 5) -> list[dict]:
    """Full-text chunk search using PostgreSQL ILIKE (no embedding needed)."""
    words = [w for w in re.findall(r"\w+", query_text) if len(w) > 3]
    if not words:
        return []
    like = " | ".join(words)
    return neon_query(
        """
        SELECT chunk_id, member_id, text, metadata
        FROM chunks
        WHERE to_tsvector('english', text) @@ to_tsquery('english', $1)
        LIMIT $2
        """,
        [like, top_k],
    )


# ── Player name extraction ────────────────────────────────────────────────────

def _get_all_names() -> list[str]:
    global _PLAYER_NAME_CACHE
    if _PLAYER_NAME_CACHE is None:
        rows = neon_query("SELECT display_name FROM players ORDER BY display_name")
        _PLAYER_NAME_CACHE = [r["display_name"] for r in rows]
    return _PLAYER_NAME_CACHE


def _fuzzy_name_match(display_name: str, msg_lower: str, msg_words: set[str]) -> bool:
    """True if all significant tokens of display_name appear (with fuzzy tolerance) in the message."""
    tokens = [t for t in display_name.lower().split() if len(t) >= 4]
    if not tokens:
        return False
    for token in tokens:
        if token in msg_words:
            continue
        # Allow 1-char typos: SequenceMatcher ratio >= 0.80 covers single insertions/deletions
        if any(
            difflib.SequenceMatcher(None, token, w).ratio() >= 0.80
            for w in msg_words
            if abs(len(w) - len(token)) <= 2
        ):
            continue
        return False
    return True


def extract_player_names(message: str) -> list[str]:
    """Return display names of players mentioned in the message (case-insensitive, typo-tolerant)."""
    msg_lower = message.lower()
    msg_words = set(re.findall(r"\w+", msg_lower))
    matched = []
    for name in _get_all_names():
        if name.lower() in msg_lower or _fuzzy_name_match(name, msg_lower, msg_words):
            matched.append(name)
    return matched


# ── Context builder ───────────────────────────────────────────────────────────

def _fmt_player_context(player: dict, sessions: list[dict], apr: dict | None = None) -> str:
    name  = player.get("display_name", "Unknown")
    lines = [f"=== {name} ==="]

    if apr and apr.get("apr") is not None:
        score = apr["apr"]
        lines.append(
            f"APR: {score:.1f}/100 ({apr_band(score)}) — "
            f"based on {apr.get('match_count', '?')} matches | "
            f"MPS={apr.get('mps', 0):.0f} PPM={apr.get('ppms', 0):.0f} "
            f"PAS={apr.get('pas', 0):.0f} OSS={apr.get('oss', 0):.0f} CS={apr.get('cs', 0):.0f}"
        )

    eb_played = player.get("eb_matches_played") or 0
    nb_played = player.get("nb_matches_played") or 0

    if eb_played:
        lines.append(
            f"8-Ball lifetime: {player.get('eb_matches_won')}/{eb_played} "
            f"({player.get('eb_win_pct')}% win rate) | "
            f"rackless={player.get('eb_rackless', 0)} | "
            f"break&run={player.get('eb_break_and_runs', 0)} | "
            f"8-on-break={player.get('eb_eight_on_breaks', 0)} | "
            f"DSA={player.get('eb_defensive_shot_avg')}"
        )
    if nb_played:
        lines.append(
            f"9-Ball lifetime: {player.get('nb_matches_won')}/{nb_played} "
            f"({player.get('nb_win_pct')}% win rate) | "
            f"break&run={player.get('nb_break_and_runs', 0)}"
        )

    eb_sessions = [s for s in sessions if s.get("game") == "eight_ball"][:3]
    nb_sessions = [s for s in sessions if s.get("game") == "nine_ball"][:2]

    for s in eb_sessions:
        won, played = s.get("matches_won", 0), s.get("matches_played", 0)
        pct = f"{round(won/played*100)}%" if played else "N/A"
        extras = []
        if s.get("rackless"):        extras.append(f"rackless={s['rackless']}")
        if s.get("break_and_runs"):  extras.append(f"BAR={s['break_and_runs']}")
        extra_str = " | " + " | ".join(extras) if extras else ""
        lines.append(
            f"  8B {s.get('session_name')}: {won}/{played} ({pct}) "
            f"for {s.get('team_name')} | PPM={s.get('ppm')}{extra_str}"
        )
    for s in nb_sessions:
        won, played = s.get("matches_won", 0), s.get("matches_played", 0)
        pct = f"{round(won/played*100)}%" if played else "N/A"
        lines.append(
            f"  9B {s.get('session_name')}: {won}/{played} ({pct}) "
            f"for {s.get('team_name')} | PPM={s.get('ppm')}"
        )

    return "\n".join(lines)


def build_player_context(message: str) -> str:
    """
    Find player names mentioned in the message, fetch their stats,
    and return a compact context block for the LLM.
    Returns empty string if no players are found or on error.
    """
    try:
        names = extract_player_names(message)
    except Exception:
        log.exception("extract_player_names failed")
        return ""

    if not names:
        return ""

    blocks = []
    for name in names[:3]:           # cap at 3 players to stay within context budget
        try:
            player = get_player(name=name)
            if not player:
                log.warning("build_player_context: no DB row for %r", name)
                continue
            sessions = get_sessions(player["member_id"])
            apr = None
            try:
                apr = get_apr(player["member_id"])
            except Exception:
                pass  # APR is enrichment — non-fatal
            blocks.append(_fmt_player_context(player, sessions, apr=apr))
        except Exception:
            log.exception("build_player_context: error fetching stats for %r", name)

    return "\n\n".join(blocks)
