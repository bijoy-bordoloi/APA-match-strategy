import {
  Bot,
  CalendarDays,
  Check,
  ChevronDown,
  ClipboardList,
  Gauge,
  History,
  MapPin,
  MessageSquare,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Send,
  Trash2,
  Trophy,
  Users,
  Wifi,
  WifiOff,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import {
  enqueueWrite,
  flushQueue,
  getHistory,
  getSuggestion,
  hasApiBase,
  loadQueue,
  sendChat,
  submitMatch,
} from './api.js';
import {
  DEFAULT_SCHEDULED,
  OPPONENT_TEAMS,
  OUR_TEAM,
  QUICK_QUESTIONS,
  SCORE_OPTIONS,
} from './data/defaultData.js';

const MAX_SL = 23;
const MAX_TURNS = 5;

export default function App() {
  const today = new Date().toISOString().slice(0, 10);
  const [screen, setScreen] = useState('setup');
  const [setup, setSetup] = useState({
    week: '5',
    date: today,
    location: '',
    mode: 'regular',
    first_move: 'throwing',
  });
  const [ourPlayers, setOurPlayers] = useState(
    OUR_TEAM.players.map((player) => ({
      ...player,
      scheduled: DEFAULT_SCHEDULED.includes(player.name),
    })),
  );
  const [opponentName, setOpponentName] = useState(OPPONENT_TEAMS[0].name);
  const [opponentTeamId, setOpponentTeamId] = useState(OPPONENT_TEAMS[0].team_id);
  const [opponentPlayers, setOpponentPlayers] = useState(OPPONENT_TEAMS[0].players);
  const [matchId, setMatchId] = useState(null);
  const [matchStatus, setMatchStatus] = useState('idle');
  const [turns, setTurns] = useState([]);
  const [selectedOur, setSelectedOur] = useState('');
  const [selectedTheir, setSelectedTheir] = useState('');
  const [selectedScore, setSelectedScore] = useState(null);
  const [suggestion, setSuggestion] = useState('');
  const [chatOpen, setChatOpen] = useState(true);
  const [chatMessages, setChatMessages] = useState([]);
  const [chatInput, setChatInput] = useState('');
  const [historyData, setHistoryData] = useState({ matches: [], player_stats: [] });
  const [expandedHistory, setExpandedHistory] = useState(null);
  const [busy, setBusy] = useState('');
  const [notice, setNotice] = useState('');
  const [queueCount, setQueueCount] = useState(loadQueue().length);
  const [online, setOnline] = useState(navigator.onLine);
  const matchStarted = matchStatus === 'active' || matchStatus === 'editing';

  const activeOurPlayers = useMemo(
    () => ourPlayers.filter((player) => player.scheduled && player.name.trim()),
    [ourPlayers],
  );
  const ourRoster = useMemo(() => rosterMap(activeOurPlayers), [activeOurPlayers]);
  const theirRoster = useMemo(() => rosterMap(opponentPlayers), [opponentPlayers]);
  const summary = useMemo(() => summarizeTurns(turns, setup.mode), [turns, setup.mode]);
  const turnNumber = Math.min(turns.length + 1, MAX_TURNS);
  const isThrowing = setup.first_move === 'throwing' ? turnNumber % 2 === 1 : turnNumber % 2 === 0;
  const ourEligible = useMemo(() => getEligible(ourRoster, turns, 'our'), [ourRoster, turns]);
  const theirEligible = useMemo(
    () => getEligible(theirRoster, turns, 'their', { enforceBudget: false }),
    [theirRoster, turns],
  );
  const context = useMemo(
    () =>
      buildMatchContext({
        setup,
        matchId,
        opponentName,
        opponentTeamId,
        ourRoster,
        theirRoster,
        turns,
        summary,
      }),
    [setup, matchId, opponentName, opponentTeamId, ourRoster, theirRoster, turns, summary],
  );

  useEffect(() => {
    const handleOnline = () => setOnline(navigator.onLine);
    const handleQueue = () => setQueueCount(loadQueue().length);
    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOnline);
    window.addEventListener('apa-queue-change', handleQueue);
    const interval = window.setInterval(() => {
      flushQueue().then((remaining) => setQueueCount(remaining.length)).catch(() => {});
    }, 12000);
    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOnline);
      window.removeEventListener('apa-queue-change', handleQueue);
      window.clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    if (summary.complete && screen === 'live') {
      setScreen('summary');
    }
  }, [summary.complete, screen]);

  function updateSetup(key, value) {
    setSetup((current) => ({ ...current, [key]: value }));
  }

  function selectOpponent(teamId) {
    if (teamId === 'custom') {
      setOpponentTeamId('custom-opponent');
      setOpponentName('Custom Opponent');
      setOpponentPlayers([{ name: '', skill_level: 3 }]);
      return;
    }
    const team = OPPONENT_TEAMS.find((item) => item.team_id === teamId);
    if (!team) return;
    setOpponentTeamId(team.team_id);
    setOpponentName(team.name);
    setOpponentPlayers(team.players);
  }

  async function startMatch() {
    setBusy('match');
    setNotice('');
    if (matchStarted) {
      setNotice('A match is already in progress. Submit it before starting another one.');
      setBusy('');
      return;
    }

    try {
      setMatchId(`local-${Date.now()}`);
      setMatchStatus('active');
      setTurns([]);
      setSelectedOur('');
      setSelectedTheir('');
      setSelectedScore(null);
      setSuggestion('');
      setChatMessages([]);
      setScreen('live');
    } finally {
      setBusy('');
    }
  }

  async function askForSuggestion() {
    if (summary.complete) return;
    if (!isThrowing && !selectedTheir) {
      setNotice('Pick the opponent first for a counter recommendation.');
      return;
    }
    setBusy('suggest');
    setNotice('');
    const payload = {
      action: isThrowing ? 'suggest_throw' : 'suggest_counter',
      match_id: matchId,
      eligible_our_players: ourEligible,
      remaining_their_players: theirEligible,
      total_sl_used: summary.our_sl_used,
      opponent_name: selectedTheir || undefined,
      opponent_sl: selectedTheir ? theirRoster[selectedTheir] : undefined,
      is_playoff: setup.mode === 'playoff',
      match_context: context,
    };
    try {
      const response = await getSuggestion(payload);
      const nextSuggestion = response.suggestion || response.result;
      setSuggestion(nextSuggestion);
      if (ourEligible[nextSuggestion]) {
        setSelectedOur(nextSuggestion);
      }
    } catch (error) {
      const fallback = localSuggestion(ourEligible, selectedTheir ? theirRoster[selectedTheir] : null);
      setSuggestion(fallback);
      setSelectedOur(fallback);
      setNotice(`Local suggestion shown. ${error.message}`);
    } finally {
      setBusy('');
    }
  }

  async function saveTurn() {
    if (!selectedOur || !selectedTheir || !selectedScore) {
      setNotice('Select both players and a score.');
      return;
    }

    const nextTurn = {
      turn_num: turns.length + 1,
      our_player_name: selectedOur,
      their_player_name: selectedTheir,
      our_sl_snapshot: ourRoster[selectedOur],
      their_sl_snapshot: theirRoster[selectedTheir],
      our_score: selectedScore.our,
      their_score: selectedScore.their,
      is_our_dp: getPlayCounts(turns, 'our')[selectedOur] === 1,
      is_their_dp: getPlayCounts(turns, 'their')[selectedTheir] === 1,
    };
    const nextTurns = [...turns, nextTurn];
    setTurns(nextTurns);
    setSelectedOur('');
    setSelectedTheir('');
    setSelectedScore(null);
    setSuggestion('');
    setNotice('');
  }

  function updateTurn(index, changes) {
    setTurns((current) => {
      const updated = current.map((turn, turnIndex) => {
        if (turnIndex !== index) return turn;
        const next = { ...turn, ...changes };
        if (changes.our_player_name) {
          next.our_sl_snapshot = ourRoster[changes.our_player_name];
        }
        if (changes.their_player_name) {
          next.their_sl_snapshot = theirRoster[changes.their_player_name];
        }
        if (changes.score_label) {
          const score = SCORE_OPTIONS.find((item) => item.label === changes.score_label);
          if (score) {
            next.our_score = score.our;
            next.their_score = score.their;
          }
          delete next.score_label;
        }
        return next;
      });
      return normalizeTurnMetadata(updated);
    });
    if (matchStatus === 'submitted') {
      setMatchStatus('editing');
    }
  }

  function removeLastTurn() {
    setTurns((current) => normalizeTurnMetadata(current.slice(0, -1)));
    setMatchStatus((current) => (current === 'submitted' ? 'editing' : current));
  }

  async function submitCurrentMatch() {
    if (!summary.complete) {
      setNotice('Complete the match before submitting.');
      return;
    }
    setBusy('submit');
    setNotice('');
    const persistedMatchId = matchId && !String(matchId).startsWith('local-') ? matchId : undefined;
    const payload = {
      match_id: matchStatus === 'editing' ? persistedMatchId : undefined,
      week: setup.week,
      date: setup.date,
      location: setup.location,
      mode: setup.mode,
      first_move: setup.first_move,
      we_throw_first: setup.first_move === 'throwing',
      our_team_id: OUR_TEAM.team_id,
      our_team_name: OUR_TEAM.name,
      opponent_team_id: opponentTeamId,
      opponent_name: opponentName,
      opponent_roster: theirRoster,
      our_roster: ourRoster,
      turns,
      match_context: context,
    };
    try {
      if (!hasApiBase()) {
        throw new Error('API base URL is not configured.');
      }
      const response = await submitMatch(payload);
      const loaded = response.result;
      if (loaded?.match?.match_id) {
        setMatchId(loaded.match.match_id);
        setTurns(loaded.turns || turns);
      }
      const remaining = await flushQueue();
      setQueueCount(remaining.length);
      setMatchStatus('submitted');
      setNotice('Match submitted. It is now available in history.');
    } catch (error) {
      enqueueWrite(payload, '/submit');
      setQueueCount(loadQueue().length);
      setMatchStatus('submitted');
      setNotice(`Match saved locally and queued for submit. ${error.message}`);
    } finally {
      setBusy('');
    }
  }

  function startNewMatch() {
    setMatchId(null);
    setMatchStatus('idle');
    setTurns([]);
    setSelectedOur('');
    setSelectedTheir('');
    setSelectedScore(null);
    setSuggestion('');
    setNotice('');
    setScreen('setup');
  }

  async function submitChat(message = chatInput) {
    const trimmed = message.trim();
    if (!trimmed) return;
    const userMessage = { role: 'user', content: trimmed };
    setChatMessages((current) => [...current, userMessage]);
    setChatInput('');
    setBusy('chat');
    try {
      const response = await sendChat({
        message: trimmed,
        match_context: context,
        history: chatMessages,
      });
      setChatMessages((current) => [...current, { role: 'assistant', content: response.reply || response.result }]);
    } catch (error) {
      setChatMessages((current) => [
        ...current,
        { role: 'assistant', content: `I could not reach the LLM yet: ${error.message}` },
      ]);
    } finally {
      setBusy('');
    }
  }

  async function loadHistoryView() {
    setScreen('history');
    setBusy('history');
    setNotice('');
    try {
      const response = await getHistory();
      setHistoryData(response);
    } catch (error) {
      setNotice(`History unavailable. ${error.message}`);
    } finally {
      setBusy('');
    }
  }

  function reEditMatch(match) {
    const savedOurRoster = match.our_roster || {};
    const savedTheirRoster = match.their_roster || {};
    setSetup({
      week: String(match.week || ''),
      date: match.date || today,
      location: match.location || '',
      mode: match.mode || 'regular',
      first_move: match.we_throw_first ? 'throwing' : 'matching',
    });
    setOurPlayers(
      Object.entries(savedOurRoster).map(([name, skill_level]) => ({
        name,
        skill_level,
        scheduled: true,
      })),
    );
    setOpponentName(match.opponent_team_name || 'Opponent');
    setOpponentTeamId(match.opponent_team_id || 'custom-opponent');
    setOpponentPlayers(
      Object.entries(savedTheirRoster).map(([name, skill_level]) => ({
        name,
        skill_level,
      })),
    );
    setMatchId(match.match_id);
    setTurns(normalizeTurnMetadata(match.turns || []));
    setMatchStatus('editing');
    setSelectedOur('');
    setSelectedTheir('');
    setSelectedScore(null);
    setSuggestion('');
    setNotice('Loaded from history. Submit again to update the saved match.');
    setScreen('summary');
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Anti-Villain League</p>
          <h1>APA 8-Ball Match Strategy</h1>
        </div>
        <div className="status-strip">
          <span className={online ? 'status-pill online' : 'status-pill offline'}>
            {online ? <Wifi size={16} /> : <WifiOff size={16} />}
            {online ? 'Online' : 'Offline'}
          </span>
          <span className="status-pill">
            <Save size={16} />
            {queueCount} queued
          </span>
        </div>
      </header>

      <nav className="tabs" aria-label="Main views">
        <button className={screen === 'setup' ? 'active' : ''} onClick={() => setScreen('setup')}>
          <ClipboardList size={18} />
          Setup
        </button>
        <button className={screen === 'live' ? 'active' : ''} onClick={() => setScreen('live')} disabled={!matchId}>
          <Gauge size={18} />
          Live
        </button>
        <button className={screen === 'summary' ? 'active' : ''} onClick={() => setScreen('summary')} disabled={!turns.length}>
          <Trophy size={18} />
          Summary
        </button>
        <button className={screen === 'history' ? 'active' : ''} onClick={loadHistoryView}>
          <History size={18} />
          History
        </button>
      </nav>

      {notice && <div className="notice">{notice}</div>}

      {screen === 'setup' && (
        <SetupView
          setup={setup}
          updateSetup={updateSetup}
          ourPlayers={ourPlayers}
          setOurPlayers={setOurPlayers}
          opponentName={opponentName}
          opponentTeamId={opponentTeamId}
          opponentPlayers={opponentPlayers}
          setOpponentName={setOpponentName}
          setOpponentPlayers={setOpponentPlayers}
          selectOpponent={selectOpponent}
          startMatch={startMatch}
          matchStarted={matchStarted}
          busy={busy}
        />
      )}

      {screen === 'live' && (
        <LiveView
          context={context}
          summary={summary}
          turns={turns}
          turnNumber={turnNumber}
          isThrowing={isThrowing}
          ourRoster={ourRoster}
          theirRoster={theirRoster}
          ourEligible={ourEligible}
          theirEligible={theirEligible}
          selectedOur={selectedOur}
          selectedTheir={selectedTheir}
          selectedScore={selectedScore}
          suggestion={suggestion}
          chatOpen={chatOpen}
          chatMessages={chatMessages}
          chatInput={chatInput}
          busy={busy}
          setSelectedOur={setSelectedOur}
          setSelectedTheir={setSelectedTheir}
          setSelectedScore={setSelectedScore}
          setChatOpen={setChatOpen}
          setChatInput={setChatInput}
          askForSuggestion={askForSuggestion}
          saveTurn={saveTurn}
          submitChat={submitChat}
        />
      )}

      {screen === 'summary' && (
        <SummaryView
          setup={setup}
          summary={summary}
          turns={turns}
          opponentName={opponentName}
          ourRoster={ourRoster}
          theirRoster={theirRoster}
          matchStatus={matchStatus}
          busy={busy}
          updateTurn={updateTurn}
          removeLastTurn={removeLastTurn}
          submitCurrentMatch={submitCurrentMatch}
          startNewMatch={startNewMatch}
        />
      )}

      {screen === 'history' && (
        <HistoryView
          historyData={historyData}
          busy={busy}
          expandedHistory={expandedHistory}
          setExpandedHistory={setExpandedHistory}
          onReEdit={reEditMatch}
        />
      )}
    </div>
  );
}

function SetupView({
  setup,
  updateSetup,
  ourPlayers,
  setOurPlayers,
  opponentName,
  opponentTeamId,
  opponentPlayers,
  setOpponentName,
  setOpponentPlayers,
  selectOpponent,
  startMatch,
  matchStarted,
  busy,
}) {
  return (
    <main className="setup-grid">
      <section className="tool-surface">
        <div className="section-title">
          <CalendarDays size={20} />
          <h2>Match Setup</h2>
        </div>
        <div className="field-grid">
          <label>
            Week
            <input value={setup.week} onChange={(event) => updateSetup('week', event.target.value)} inputMode="numeric" />
          </label>
          <label>
            Date
            <input type="date" value={setup.date} onChange={(event) => updateSetup('date', event.target.value)} />
          </label>
          <label className="span-2">
            <MapPin size={16} />
            Location
            <input value={setup.location} onChange={(event) => updateSetup('location', event.target.value)} />
          </label>
        </div>
        <SegmentedControl
          label="Mode"
          value={setup.mode}
          options={[
            { value: 'regular', label: 'Regular' },
            { value: 'playoff', label: 'Playoff' },
          ]}
          onChange={(value) => updateSetup('mode', value)}
        />
        <SegmentedControl
          label="First move"
          value={setup.first_move}
          options={[
            { value: 'throwing', label: 'Throwing' },
            { value: 'matching', label: 'Matching' },
          ]}
          onChange={(value) => updateSetup('first_move', value)}
        />
      </section>

      <section className="tool-surface">
        <div className="section-title">
          <Users size={20} />
          <h2>Our Roster</h2>
        </div>
        <RosterEditor players={ourPlayers} setPlayers={setOurPlayers} showScheduled />
      </section>

      <section className="tool-surface">
        <div className="section-title">
          <Users size={20} />
          <h2>Opponent</h2>
        </div>
        <label>
          Team
          <select value={opponentTeamId} onChange={(event) => selectOpponent(event.target.value)}>
            {OPPONENT_TEAMS.map((team) => (
              <option key={team.team_id} value={team.team_id}>
                {team.name}
              </option>
            ))}
            <option value="custom">Create new</option>
          </select>
        </label>
        <label>
          Name
          <input value={opponentName} onChange={(event) => setOpponentName(event.target.value)} />
        </label>
        <RosterEditor players={opponentPlayers} setPlayers={setOpponentPlayers} />
      </section>

      <section className="action-band">
        <div>
          <p className="eyebrow">Ready roster SL</p>
          <strong>{sumRoster(ourPlayers.filter((player) => player.scheduled))} / {MAX_SL}</strong>
        </div>
        <button className="primary-action" onClick={startMatch} disabled={busy === 'match' || matchStarted}>
          <Check size={20} />
          {matchStarted ? 'Match In Progress' : busy === 'match' ? 'Starting' : 'Start Match'}
        </button>
      </section>
    </main>
  );
}

function LiveView({
  context,
  summary,
  turns,
  turnNumber,
  isThrowing,
  ourRoster,
  theirRoster,
  ourEligible,
  theirEligible,
  selectedOur,
  selectedTheir,
  selectedScore,
  suggestion,
  chatOpen,
  chatMessages,
  chatInput,
  busy,
  setSelectedOur,
  setSelectedTheir,
  setSelectedScore,
  setChatOpen,
  setChatInput,
  askForSuggestion,
  saveTurn,
  submitChat,
}) {
  return (
    <main className="match-grid">
      <section className="live-stack">
        <LiveHeader summary={summary} turnNumber={turnNumber} isThrowing={isThrowing} />
        {summary.clinched_by && (
          <div className={summary.clinched_by === 'ours' ? 'clinch-banner win' : 'clinch-banner loss'}>
            {summary.clinched_by === 'ours' ? 'VICTORY clinched' : 'DEFEAT clinched'}
          </div>
        )}

        <section className="tool-surface">
          <div className="section-title split-title">
            <span>
              <Users size={20} />
              <h2>Our Roster</h2>
            </span>
            <button className="ghost-button" onClick={askForSuggestion} disabled={busy === 'suggest' || summary.complete}>
              <Bot size={18} />
              {busy === 'suggest' ? 'Asking' : 'Suggest'}
            </button>
          </div>
          {suggestion && (
            <button className="suggestion-chip" onClick={() => setSelectedOur(suggestion)} disabled={!ourEligible[suggestion]}>
              <Bot size={18} />
              {suggestion}
            </button>
          )}
          <PlayerPicker
            roster={ourRoster}
            eligible={ourEligible}
            turns={turns}
            side="our"
            selected={selectedOur}
            onSelect={setSelectedOur}
          />
        </section>

        <section className="tool-surface">
          <div className="section-title">
            <Users size={20} />
            <h2>Opponent</h2>
          </div>
          <PlayerPicker
            roster={theirRoster}
            eligible={theirEligible}
            turns={turns}
            side="their"
            selected={selectedTheir}
            onSelect={setSelectedTheir}
          />
        </section>

        <section className="tool-surface">
          <div className="section-title">
            <Trophy size={20} />
            <h2>Score</h2>
          </div>
          <div className="score-grid">
            {SCORE_OPTIONS.map((score) => (
              <button
                key={score.label}
                className={selectedScore?.label === score.label ? 'score-button active' : 'score-button'}
                onClick={() => setSelectedScore(score)}
              >
                {score.label}
              </button>
            ))}
          </div>
          <button className="primary-action full-width" onClick={saveTurn} disabled={summary.complete}>
            <Save size={20} />
            Record Turn
          </button>
        </section>
      </section>

      <ChatPanel
        open={chatOpen}
        setOpen={setChatOpen}
        messages={chatMessages}
        input={chatInput}
        setInput={setChatInput}
        submitChat={submitChat}
        busy={busy}
        context={context}
      />
    </main>
  );
}

function LiveHeader({ summary, turnNumber, isThrowing }) {
  const usedPercent = Math.min(100, (summary.our_sl_used / MAX_SL) * 100);
  return (
    <section className="live-header">
      <div>
        <p className="eyebrow">Turn {summary.complete ? Math.max(1, turnNumber - 1) : turnNumber} of {MAX_TURNS}</p>
        <h2>{isThrowing ? 'Throwing' : 'Matching'}</h2>
      </div>
      <div className="score-tally">
        <strong>{summary.our_score}</strong>
        <span>AVL</span>
        <strong>{summary.their_score}</strong>
        <span>Opp</span>
      </div>
      <div className="budget-block">
        <div className="budget-label">
          <span>SL {summary.our_sl_used} / {MAX_SL}</span>
          <span>{MAX_SL - summary.our_sl_used} left</span>
        </div>
        <div className="budget-bar">
          <span style={{ width: `${usedPercent}%` }} />
        </div>
        <div className="dp-row">
          <span>Our DP {summary.our_dp_used ? 'used' : 'open'}</span>
          <span>Their DP {summary.their_dp_used ? 'used' : 'open'}</span>
        </div>
      </div>
    </section>
  );
}

function ChatPanel({ open, setOpen, messages, input, setInput, submitChat, busy }) {
  return (
    <aside className={open ? 'chat-panel open' : 'chat-panel'}>
      <button className="chat-toggle" onClick={() => setOpen(!open)}>
        <MessageSquare size={18} />
        Chat
        <ChevronDown size={18} />
      </button>
      {open && (
        <>
          <div className="quick-row">
            {QUICK_QUESTIONS.map((question) => (
              <button key={question} onClick={() => submitChat(question)}>
                {question}
              </button>
            ))}
          </div>
          <div className="message-list">
            {messages.map((message, index) => (
              <div key={`${message.role}-${index}`} className={`message ${message.role}`}>
                {message.content}
              </div>
            ))}
          </div>
          <form
            className="chat-form"
            onSubmit={(event) => {
              event.preventDefault();
              submitChat();
            }}
          >
            <input value={input} onChange={(event) => setInput(event.target.value)} />
            <button aria-label="Send chat message" title="Send chat message" disabled={busy === 'chat'}>
              <Send size={18} />
            </button>
          </form>
        </>
      )}
    </aside>
  );
}

function SummaryView({
  setup,
  summary,
  turns,
  opponentName,
  ourRoster,
  theirRoster,
  matchStatus,
  busy,
  updateTurn,
  removeLastTurn,
  submitCurrentMatch,
  startNewMatch,
}) {
  const banner = setup.mode === 'playoff'
    ? summary.our_wins >= 3
      ? 'VICTORY'
      : summary.their_wins >= 3
        ? 'DEFEAT'
        : 'IN PROGRESS'
    : summary.result?.replace('_', ' ').toUpperCase() || 'IN PROGRESS';
  const submitLabel = matchStatus === 'editing' ? 'Update Match' : 'Submit Match';
  const submitted = matchStatus === 'submitted';

  return (
    <main className="summary-layout">
      <section className={banner === 'DEFEAT' ? 'result-banner loss' : 'result-banner'}>
        <p className="eyebrow">{opponentName}</p>
        <h2>{banner}</h2>
      </section>
      <section className="stats-row">
        <Metric label="Points" value={`${summary.our_score}-${summary.their_score}`} />
        <Metric label="Wins" value={`${summary.our_wins}-${summary.their_wins}`} />
        <Metric label="SL Used" value={`${summary.our_sl_used}/${MAX_SL}`} />
        <Metric label="Efficiency" value={`${summary.point_efficiency}%`} />
      </section>
      <section className="tool-surface">
        <div className="section-title split-title">
          <span>
            <ClipboardList size={20} />
            <h2>Turn Breakdown</h2>
          </span>
          <button className="ghost-button" onClick={removeLastTurn} disabled={!turns.length || submitted}>
            <RotateCcw size={18} />
            Remove Last
          </button>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Our Player</th>
                <th>Opponent</th>
                <th>SL</th>
                <th>Score</th>
                <th>W/L</th>
              </tr>
            </thead>
            <tbody>
              {turns.map((turn, index) => (
                <tr key={turn.turn_num}>
                  <td>{turn.turn_num}</td>
                  <td>
                    <select
                      value={turn.our_player_name}
                      disabled={submitted}
                      onChange={(event) => updateTurn(index, { our_player_name: event.target.value })}
                    >
                      {Object.keys(ourRoster).map((name) => (
                        <option key={name} value={name}>
                          {name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <select
                      value={turn.their_player_name}
                      disabled={submitted}
                      onChange={(event) => updateTurn(index, { their_player_name: event.target.value })}
                    >
                      {Object.keys(theirRoster).map((name) => (
                        <option key={name} value={name}>
                          {name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>{turn.our_sl_snapshot}-{turn.their_sl_snapshot}</td>
                  <td>
                    <select
                      value={`${turn.our_score}-${turn.their_score}`}
                      disabled={submitted}
                      onChange={(event) => updateTurn(index, { score_label: event.target.value })}
                    >
                      {SCORE_OPTIONS.map((score) => (
                        <option key={score.label} value={score.label}>
                          {score.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>{turn.our_score >= 2 ? 'W' : 'L'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="summary-actions">
          <button className="primary-action" onClick={submitCurrentMatch} disabled={!summary.complete || busy === 'submit' || submitted}>
            <Save size={20} />
            {busy === 'submit' ? 'Submitting' : submitted ? 'Submitted' : submitLabel}
          </button>
          <button onClick={startNewMatch} disabled={busy === 'submit'}>
            <Plus size={20} />
            Start New Match
          </button>
        </div>
      </section>
    </main>
  );
}

function HistoryView({ historyData, busy, expandedHistory, setExpandedHistory, onReEdit }) {
  return (
    <main className="history-layout">
      <section className="tool-surface">
        <div className="section-title split-title">
          <span>
            <History size={20} />
            <h2>Past Matches</h2>
          </span>
          {busy === 'history' && <RefreshCw className="spin" size={18} />}
        </div>
        <div className="history-list">
          {historyData.matches.map((match) => (
            <article key={match.match_id} className="history-item">
              <button onClick={() => setExpandedHistory(expandedHistory === match.match_id ? null : match.match_id)}>
                <span>
                  <strong>{match.date || 'No date'}</strong>
                  {match.opponent_team_name || match.away_team_id}
                </span>
                <span>{match.summary?.our_score}-{match.summary?.their_score}</span>
              </button>
              {expandedHistory === match.match_id && (
                <div className="history-turns">
                  {match.turns.map((turn) => (
                    <p key={turn.turn_num}>
                      {turn.turn_num}. {turn.our_player_name} vs {turn.their_player_name}: {turn.our_score}-{turn.their_score}
                    </p>
                  ))}
                  <button className="ghost-button" onClick={() => onReEdit(match)}>
                    <Pencil size={18} />
                    Re-edit
                  </button>
                </div>
              )}
            </article>
          ))}
          {!historyData.matches.length && busy !== 'history' && <p className="empty-state">No completed matches found.</p>}
        </div>
      </section>
      <section className="tool-surface">
        <div className="section-title">
          <Users size={20} />
          <h2>Player Stats</h2>
        </div>
        <div className="stat-list">
          {historyData.player_stats.map((player) => (
            <div key={player.player_name} className="stat-line">
              <strong>{player.player_name}</strong>
              <span>{player.wins}-{player.losses}</span>
              <span>{player.points} pts</span>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}

function RosterEditor({ players, setPlayers, showScheduled = false }) {
  function update(index, key, value) {
    setPlayers((current) =>
      current.map((player, playerIndex) =>
        playerIndex === index
          ? {
              ...player,
              [key]: key === 'skill_level' ? Number(value) : value,
            }
          : player,
      ),
    );
  }

  function remove(index) {
    setPlayers((current) => current.filter((_, playerIndex) => playerIndex !== index));
  }

  return (
    <div className="roster-editor">
      {players.map((player, index) => (
        <div className="edit-row" key={`${player.name}-${index}`}>
          {showScheduled && (
            <label className="check-label">
              <input
                type="checkbox"
                checked={player.scheduled}
                onChange={(event) => update(index, 'scheduled', event.target.checked)}
              />
              Active
            </label>
          )}
          <input value={player.name} onChange={(event) => update(index, 'name', event.target.value)} />
          <input
            className="sl-input"
            type="number"
            min="1"
            max="7"
            value={player.skill_level}
            onChange={(event) => update(index, 'skill_level', event.target.value)}
          />
          <button aria-label="Remove player" title="Remove player" onClick={() => remove(index)}>
            <Trash2 size={18} />
          </button>
        </div>
      ))}
      <button
        className="ghost-button"
        onClick={() => setPlayers((current) => [...current, { name: '', skill_level: 3, scheduled: true }])}
      >
        <Plus size={18} />
        Add Player
      </button>
    </div>
  );
}

function PlayerPicker({ roster, eligible, turns, side, selected, onSelect }) {
  const counts = getPlayCounts(turns, side);
  const dpUsed = turns.some((turn) => (side === 'our' ? turn.is_our_dp : turn.is_their_dp));
  return (
    <div className="picker-list">
      {Object.entries(roster).map(([name, skill], index) => {
        const isEligible = Boolean(eligible[name]);
        const count = counts[name] || 0;
        const status = !isEligible
          ? count >= 2
            ? '2x played'
            : count === 1 && dpUsed
              ? 'DP used'
              : 'Ineligible'
          : count === 1
            ? 'DP option'
            : 'Eligible';
        return (
          <button
            key={name}
            className={selected === name ? 'player-row selected' : 'player-row'}
            disabled={!isEligible}
            onClick={() => onSelect(name)}
          >
            <span className="row-num">{index + 1}</span>
            <span className="player-main">
              <strong>{name}</strong>
              <small>{status}</small>
            </span>
            <span className="sl-badge">SL {skill}</span>
          </button>
        );
      })}
    </div>
  );
}

function SegmentedControl({ label, value, options, onChange }) {
  return (
    <div className="segmented-field">
      <span>{label}</span>
      <div className="segmented">
        {options.map((option) => (
          <button
            key={option.value}
            className={value === option.value ? 'active' : ''}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function Metric({ label, value }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function rosterMap(players) {
  return players.reduce((acc, player) => {
    const name = player.name.trim();
    if (name) {
      acc[name] = Number(player.skill_level) || 0;
    }
    return acc;
  }, {});
}

function sumRoster(players) {
  return players.reduce((total, player) => total + (Number(player.skill_level) || 0), 0);
}

function getPlayCounts(turns, side) {
  return turns.reduce((acc, turn) => {
    const name = side === 'our' ? turn.our_player_name : turn.their_player_name;
    if (name) acc[name] = (acc[name] || 0) + 1;
    return acc;
  }, {});
}

function normalizeTurnMetadata(turns) {
  const ourCounts = {};
  const theirCounts = {};
  return turns.map((turn, index) => {
    const ourPrior = ourCounts[turn.our_player_name] || 0;
    const theirPrior = theirCounts[turn.their_player_name] || 0;
    ourCounts[turn.our_player_name] = ourPrior + 1;
    theirCounts[turn.their_player_name] = theirPrior + 1;
    return {
      ...turn,
      turn_num: index + 1,
      is_our_dp: ourPrior === 1,
      is_their_dp: theirPrior === 1,
    };
  });
}

function getEligible(roster, turns, side, options = {}) {
  const counts = getPlayCounts(turns, side);
  const dpUsed = turns.some((turn) => (side === 'our' ? turn.is_our_dp : turn.is_their_dp));
  const slUsed = turns.reduce(
    (total, turn) => total + Number(side === 'our' ? turn.our_sl_snapshot : turn.their_sl_snapshot),
    0,
  );
  const room = MAX_SL - slUsed;
  return Object.entries(roster).reduce((acc, [name, sl]) => {
    const count = counts[name] || 0;
    if (count >= 2) return acc;
    if (count === 1 && dpUsed) return acc;
    if (options.enforceBudget !== false && Number(sl) > room) return acc;
    acc[name] = Number(sl);
    return acc;
  }, {});
}

function summarizeTurns(turns, mode) {
  const our_score = turns.reduce((total, turn) => total + Number(turn.our_score), 0);
  const their_score = turns.reduce((total, turn) => total + Number(turn.their_score), 0);
  const our_wins = turns.filter((turn) => Number(turn.our_score) >= 2).length;
  const their_wins = turns.filter((turn) => Number(turn.their_score) >= 2).length;
  const our_sl_used = turns.reduce((total, turn) => total + Number(turn.our_sl_snapshot), 0);
  const their_sl_used = turns.reduce((total, turn) => total + Number(turn.their_sl_snapshot), 0);
  const clinched_by = mode === 'playoff' && our_wins >= 3 ? 'ours' : mode === 'playoff' && their_wins >= 3 ? 'theirs' : null;
  const complete = turns.length >= MAX_TURNS || Boolean(clinched_by);
  const maxPoints = turns.length * 3;
  const point_efficiency = maxPoints ? Math.round((our_score / maxPoints) * 100) : 0;
  return {
    our_score,
    their_score,
    our_wins,
    their_wins,
    our_sl_used,
    their_sl_used,
    our_dp_used: turns.some((turn) => turn.is_our_dp),
    their_dp_used: turns.some((turn) => turn.is_their_dp),
    clinched_by,
    complete,
    point_efficiency,
    result: complete ? (our_score >= their_score ? 'points_win' : 'points_loss') : null,
  };
}

function buildMatchContext({ setup, matchId, opponentName, opponentTeamId, ourRoster, theirRoster, turns, summary }) {
  return {
    match_id: matchId,
    week: setup.week,
    date: setup.date,
    location: setup.location,
    mode: setup.mode,
    match_goal: setup.mode === 'playoff' ? 'first to 3 individual wins' : 'maximize total points over 5 turns',
    our_team_name: OUR_TEAM.name,
    opponent_team_id: opponentTeamId,
    opponent_team_name: opponentName,
    our_roster: ourRoster,
    their_roster: theirRoster,
    turns,
    summary,
    sl_budget_remaining: MAX_SL - summary.our_sl_used,
  };
}

function localSuggestion(eligible, opponentSl) {
  const entries = Object.entries(eligible);
  if (!entries.length) return '';
  if (opponentSl) {
    return entries.sort((a, b) => Math.abs(a[1] - opponentSl) - Math.abs(b[1] - opponentSl))[0][0];
  }
  return entries.sort((a, b) => a[1] - b[1])[Math.floor(entries.length / 2)][0];
}
