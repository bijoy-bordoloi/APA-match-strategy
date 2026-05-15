export const OUR_TEAM = {
  team_id: 'anti-villain-league',
  name: 'Anti-Villain League',
  players: [
    { name: 'Bijoy Bordoloi', skill_level: 7 },
    { name: 'Krishnamurthy Ganesan', skill_level: 6 },
    { name: 'Tien Truong', skill_level: 5 },
    { name: 'Mehul Ppaandhii', skill_level: 5 },
    { name: 'David Remer', skill_level: 4 },
    { name: 'Jeff Highbarger', skill_level: 2 },
    { name: 'Kellan Thai', skill_level: 2 },
    { name: 'Kim-Khanh Van', skill_level: 2 },
  ],
};

export const DEFAULT_SCHEDULED = [
  'Bijoy Bordoloi',
  'Krishnamurthy Ganesan',
  'Tien Truong',
  'David Remer',
  'Jeff Highbarger',
];

export const OPPONENT_TEAMS = [
  {
    team_id: 'satire-squad',
    name: 'Satire Squad',
    players: [
      { name: 'Kapil Londhe', skill_level: 6 },
      { name: 'Piyush Zode', skill_level: 6 },
      { name: 'Saksham Sijariya', skill_level: 5 },
      { name: 'Tamil "DR. T" Manickavelu', skill_level: 5 },
      { name: 'Arvind Kumar', skill_level: 4 },
      { name: 'Shahaji Arkile', skill_level: 3 },
    ],
  },
  {
    team_id: 'the-lucky-losers',
    name: 'The Lucky Losers',
    players: [
      { name: 'Todd "The Lucky Loser" Shirley', skill_level: 6 },
      { name: 'William Plunkett', skill_level: 5 },
      { name: 'Noam King', skill_level: 4 },
      { name: 'Tarn Faulkner', skill_level: 4 },
      { name: 'James Wolf', skill_level: 4 },
    ],
  },
  {
    team_id: 'golden-sunrise',
    name: 'Golden Sunrise',
    players: [
      { name: 'Kirk Boyens', skill_level: 6 },
      { name: 'Doug McKnight', skill_level: 5 },
      { name: 'Adam Hunnicutt', skill_level: 4 },
      { name: 'Luis Mazon', skill_level: 3 },
      { name: 'Neng Xue', skill_level: 3 },
      { name: 'Meaghan Jowdy', skill_level: 2 },
    ],
  },
];

export const SCORE_OPTIONS = [
  { label: '3-0', our: 3, their: 0 },
  { label: '2-0', our: 2, their: 0 },
  { label: '2-1', our: 2, their: 1 },
  { label: '1-2', our: 1, their: 2 },
  { label: '0-2', our: 0, their: 2 },
  { label: '0-3', our: 0, their: 3 },
];

export const QUICK_QUESTIONS = [
  'Who should I throw?',
  "What's our win probability?",
  'Summarize the match so far',
  'What did we do in a similar situation?',
];

// Maps Google email → lowercase team name variants for perspective resolution.
// Both spellings of AVL are included because the scraper stores a misspelled variant.
// See APA-25 for the full identity/multi-team solution.
export const USER_TEAMS = {
  'bbordoloi@gmail.com': ['anti-villain league', 'the anti villian league'],
};
