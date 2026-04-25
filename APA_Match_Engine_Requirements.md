# Software Requirements Specification (SRS)
## APA 8-Ball Strategic Match Engine (Anti-Villain League)

### 1. System Overview
The **APA 8-Ball Strategic Match Engine** is a specialized command-line application designed to assist the **Anti-Villain League** in tactical decision-making during 8-ball billiards league play. The system manages player rosters, enforces league scoring and skill level constraints, and suggests optimal matchups using pluggable strategy patterns.

### 2. Functional Requirements

#### 2.1 Match Setup & Strategy Selection
- **Roster Management:** The system shall maintain pre-defined dictionaries for the home team (**Anti-Villain League**) and the opponent (**Satire Squad**), mapping player names to their Skill Levels (SL).
- **Strategy Initialization:** Upon session start, the system shall prompt the user to select a strategy profile:
    - **Aggressive (1):** Focuses on maximizing point gain by leading with high SL players.
    - **Neutral (2):** Focuses on tactical flexibility and "neutralizing" high-tier opponents.
- **Initial State:** The system shall ask for the starting turn type for Match 1 (Throwing or Matching).

#### 2.2 Turn & Flow Control
- **Alternating Logic:** The system shall automatically toggle the turn type for every subsequent match.
    - If Match $N$ is "Throwing", Match $N+1$ shall be "Matching".
- **Match Lifecycle:** Each of the 5 matches must process:
    1.  Strategic player suggestion.
    2.  User input for actual player selection.
    3.  User input for opponent player selection.
    4.  Double play verification.
    5.  Match result (score) entry.

#### 2.3 Strategic Calculation Engine
- **The 23-Point Rule:** The system shall enforce the APA team skill level limit. For every suggestion and manual entry, the system must verify:
    - `Current SL Total + Selected Player SL + (Remaining Matches * 2) <= 23`.
- **Suggestion Priority:**
    - Strategic suggestions shall exclusively consider "fresh" players (those who have not yet played).
- **Neutral Strategy Logic:**
    - **Throwing:** Suggest the median SL player to preserve both high and low SL options.
    - **Matching:** If the opponent throws an SL 6+, suggest the lowest SL (Sacrifice). If the opponent throws an SL < 6, suggest the highest SL (Dominance).

#### 2.4 Double Play & Roster Constraints
- **Double Play Limit:** The system shall allow a maximum of one player per team to play exactly twice in a single session.
- **Triple Play Prohibition:** The system shall strictly prevent any player from being entered a third time.
- **Verification Prompts:** If a player name is entered that already exists in the "played" history, the system must ask: `"Is it a double play? yes(1), no(2)"`.
    - If "No" (2), the match entry is aborted and the user is re-prompted for a new name.
    - If "Yes" (1), the player is recorded as the designated double-play for that team.
- **Symmetry:** Both the home and opponent teams are subject to the same double-play constraints.

#### 2.5 Error Handling & Input Validation
- **Null Protection:** The system shall reject empty inputs and re-prompt the user.
- **Roster Verification:** The system shall only accept names that exist in the pre-defined team rosters.
- **Format Integrity:**
    - Turn/Strategy/Double Play choices must be numeric (1 or 2).
    - Match scores must follow the `OurScore,TheirScore` format (e.g., `3,0`).
- **Input Loops:** In case of any validation failure, the system shall provide an error message and repeat the specific prompt until valid data is received.

#### 2.6 Termination and Summary
- **Early Win Detection:** The system shall terminate if the total team points reach **8**, signifying a secured match win.
- **Final Reporting:** At the end of the session, the system shall output a summary of all matches, including player names, double play markers, scores, and final totals.

### 3. Modular Architecture
- **`strategies.py`**: Encapsulates all algorithmic logic for player suggestions. This allows for new strategies to be added without modifying the core engine.
- **`engine.py`**: Manages the match state, input/output loops, and league constraint logic.
