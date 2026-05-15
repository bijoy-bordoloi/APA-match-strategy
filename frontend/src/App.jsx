import {
  Bot,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  Flame,
  Gauge,
  History,
  Info,
  LogOut,
  MapPin,
  MessageSquare,
  Minus,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Send,
  Trash2,
  TrendingDown,
  Trophy,
  Users,
  Wifi,
  WifiOff,
  X,
} from 'lucide-react';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  deleteMatch,
  enqueueWrite,
  fetchPlayerProfile,
  flushQueue,
  searchPlayers,
  getHistory,
  getRosters,
  getSuggestion,
  hasApiBase,
  loadQueue,
  sendChat,
  submitMatch,
} from './api.js';
import {
  OPPONENT_TEAMS,
  OUR_TEAM,
  QUICK_QUESTIONS,
  SCORE_OPTIONS,
  USER_TEAMS,
} from './data/defaultData.js';

const MAX_SL = 23;
const MAX_TURNS = 5;

export default function App() {
  const today = new Date().toISOString().slice(0, 10);
  const [authState, setAuthState] = useState('checking');
  const [authDeniedEmail, setAuthDeniedEmail] = useState('');
  const [showAuthOverlay, setShowAuthOverlay] = useState(false);
  const [screen, setScreen] = useState('setup');
  const [setup, setSetup] = useState({
    week: '5',
    date: today,
    location: '',
    mode: 'regular',
    first_move: 'throwing',
  });
  const [ourPlayers, setOurPlayers] = useState(
    OUR_TEAM.players.map((player) => ({ ...player, scheduled: false })),
  );
  const [opponentTeams, setOpponentTeams] = useState(OPPONENT_TEAMS);
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
  const [profileSheet, setProfileSheet] = useState(null);
  const [selectedPlayer, setSelectedPlayer] = useState(null);
  const [prevScreen, setPrevScreen] = useState('history');
  const matchStarted = matchStatus === 'active' || matchStatus === 'editing';

  // --- Auth: boot check (synchronous localStorage read, no network) ---
  useEffect(() => {
    const token = localStorage.getItem('apa-gis-token');
    if (token) {
      try {
        const payload = JSON.parse(atob(token.split('.')[1]));
        if (payload.exp * 1000 > Date.now() + 60_000) {
          setAuthState('authenticated');
          return;
        }
      } catch { /* malformed token — fall through */ }
    }
    localStorage.removeItem('apa-gis-token');
    setAuthState('unauthenticated');
  }, []);

  // --- Auth: GIS initialization ---
  useEffect(() => {
    if (authState === 'authenticated') return;
    const initGis = () => {
      if (!window.google?.accounts?.id) return;
      window.google.accounts.id.initialize({
        client_id: import.meta.env.VITE_GOOGLE_CLIENT_ID,
        callback: handleGisCredential,
        auto_select: true, // enables One Tap silent re-auth
      });
      window.google.accounts.id.renderButton(
        document.getElementById('gis-button-target'),
        { type: 'standard', shape: 'rectangular', theme: 'outline',
          text: 'sign_in_with', size: 'large', width: 312, logo_alignment: 'left' },
      );
      if (authState === 'unauthenticated') {
        window.google.accounts.id.prompt();
      }
    };
    if (window.google?.accounts?.id) {
      initGis();
    } else {
      const t = setTimeout(initGis, 500);
      return () => clearTimeout(t);
    }
  }, [authState]);

  // --- Auth: 401/403 event listeners from api.js ---
  useEffect(() => {
    const onExpired = () => {
      localStorage.removeItem('apa-gis-token');
      // Option A (REVIEW-002 Condition 1): preserve in-memory match state during
      // a mid-match 401 by using a separate overlay flag instead of changing authState.
      if (matchId !== null) {
        setShowAuthOverlay(true);
      } else {
        setAuthState('unauthenticated');
      }
    };
    const onDenied = (e) => {
      localStorage.removeItem('apa-gis-token');
      setAuthDeniedEmail(e.detail?.email || '');
      setAuthState('access_denied');
    };
    window.addEventListener('apa-auth-expired', onExpired);
    window.addEventListener('apa-auth-denied', onDenied);
    return () => {
      window.removeEventListener('apa-auth-expired', onExpired);
      window.removeEventListener('apa-auth-denied', onDenied);
    };
  }, [matchId]);

  async function handleGisCredential(response) {
    const token = response.credential;
    let email = '';
    try { email = JSON.parse(atob(token.split('.')[1])).email; } catch {}

    localStorage.setItem('apa-gis-token', token);
    setAuthState('checking');

    try {
      const res = await fetch(
        `${import.meta.env.VITE_API_BASE_URL}/rosters`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (res.ok) {
        setShowAuthOverlay(false);
        setAuthState('authenticated');
      } else if (res.status === 403) {
        localStorage.removeItem('apa-gis-token');
        setAuthDeniedEmail(email);
        setAuthState('access_denied');
      } else {
        localStorage.removeItem('apa-gis-token');
        setAuthState('unauthenticated');
      }
    } catch {
      localStorage.removeItem('apa-gis-token');
      setAuthState('unauthenticated');
    }
  }

  function handleLogout() {
    if (navigator.onLine && hasApiBase()) {
      flushQueue().finally(clearSession);
    } else {
      clearSession();
    }
  }

  function clearSession() {
    const token = localStorage.getItem('apa-gis-token');
    if (token && window.google?.accounts?.id) {
      try {
        const email = JSON.parse(atob(token.split('.')[1])).email;
        window.google.accounts.id.revoke(email, () => {});
      } catch { /* non-fatal */ }
    }
    localStorage.removeItem('apa-gis-token');
    localStorage.removeItem('apa-match-write-queue');
    window.location.reload();
  }

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

  const lastFetchedWeek = React.useRef(null);
  useEffect(() => {
    if (authState === 'authenticated') {
      lastFetchedWeek.current = null;
    }
  }, [authState]);
  useEffect(() => {
    if (!hasApiBase() || authState !== 'authenticated') return;
    if (setup.week === lastFetchedWeek.current) return;
    lastFetchedWeek.current = setup.week;
    getRosters(setup.week).then((data) => {
      if (data.opponent_teams?.length) {
        setOpponentTeams(data.opponent_teams);
        const matchOpp = data.match_info?.opponent_team_id
          ? data.opponent_teams.find((t) => t.team_id === data.match_info.opponent_team_id)
          : data.opponent_teams[0];
        const opp = matchOpp || data.opponent_teams[0];
        setOpponentTeamId(opp.team_id);
        setOpponentName(opp.name);
        setOpponentPlayers(opp.players);
      }
      if (data.our_team?.players?.length) {
        setOurPlayers(data.our_team.players.map((p) => ({ ...p, scheduled: p.scheduled ?? false })));
      }
      if (data.match_info) {
        setSetup((current) => ({
          ...current,
          week: String(data.match_info.week),
          date: data.match_info.date || current.date,
          location: data.match_info.location || current.location,
        }));
      }
    }).catch(() => {});
  }, [setup.week, authState]);

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
    const team = opponentTeams.find((item) => item.team_id === teamId);
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

  function cancelMatch() {
    if (!window.confirm('Cancel this match? All recorded turns will be lost.')) return;
    startNewMatch();
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

  function goToPlayer(name, sl) {
    setPrevScreen(screen);
    setSelectedPlayer({ name, sl: sl || null });
    setScreen('player');
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

  async function deleteFromHistory(matchId) {
    if (!window.confirm('Delete this match? This cannot be undone.')) return;
    try {
      await deleteMatch(matchId);
      setHistoryData((prev) => ({
        ...prev,
        matches: prev.matches.filter((m) => m.match_id !== matchId),
      }));
    } catch (error) {
      setNotice(`Could not delete match. ${error.message}`);
    }
  }

  if (authState === 'checking') return null;
  if (authState !== 'authenticated') {
    return <SignInScreen deniedEmail={authState === 'access_denied' ? authDeniedEmail : ''} />;
  }

  return (
    <>
      {showAuthOverlay && (
        <div className="auth-overlay">
          <SignInScreen deniedEmail="" />
        </div>
      )}
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
          <button className="ghost-button signout-btn" onClick={handleLogout} aria-label="Sign out">
            <LogOut size={16} /> Sign out
          </button>
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
        <button className={screen === 'player' ? 'active' : ''} onClick={() => { setPrevScreen(screen); setSelectedPlayer(null); setScreen('player'); }}>
          <Users size={18} />
          Players
        </button>
      </nav>

      {notice && <div className="notice">{notice}</div>}

      {screen === 'setup' && (
        <SetupView
          setup={setup}
          updateSetup={updateSetup}
          ourPlayers={ourPlayers}
          setOurPlayers={setOurPlayers}
          opponentTeams={opponentTeams}
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
          cancelMatch={cancelMatch}
          profileSheet={profileSheet}
          setProfileSheet={setProfileSheet}
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
          onStartNew={startNewMatch}
          onDelete={deleteFromHistory}
          onPlayerClick={goToPlayer}
        />
      )}

      {screen === 'player' && (
        <PlayerView
          initialPlayer={selectedPlayer}
          historyData={historyData}
          onBack={() => setScreen(prevScreen || 'history')}
        />
      )}
    </div>
  </>
  );
}

function SetupView({
  setup,
  updateSetup,
  ourPlayers,
  setOurPlayers,
  opponentTeams,
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
            {opponentTeams.map((team) => (
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
        {(() => {
          const scheduledCount = ourPlayers.filter((p) => p.scheduled && p.name.trim()).length;
          return (
            <button className="primary-action" onClick={startMatch} disabled={busy === 'match' || matchStarted || scheduledCount < 4}>
              <Check size={20} />
              {matchStarted ? 'Match In Progress' : busy === 'match' ? 'Starting' : 'Start Match'}
            </button>
          );
        })()}
      </section>
    </main>
  );
}

const FORM_ICON_CONFIGS = {
  hot: { color: '#b06a00', Icon: Flame },
  mid: { color: 'var(--green)', Icon: Minus },
  low: { color: 'var(--red)', Icon: TrendingDown },
};

function getFormBadgeFromCache(name) {
  try {
    const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
    const raw = window.localStorage.getItem(`apa-profile-${slug}`);
    if (!raw) return null;
    return JSON.parse(raw)?.form ?? null;
  } catch {
    return null;
  }
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
  cancelMatch,
  profileSheet,
  setProfileSheet,
}) {
  const canvasRef = useRef(null);
  const svgRef = useRef(null);
  const midColRef = useRef(null);
  const inlineRef = useRef(null);
  const wheelRef = useRef(null);
  const wheelTimer = useRef(null);
  const longPressTimer = useRef(null);
  const ITEM_H = 24;

  function openProfile(name, sl, opponentPlayerId) {
    setProfileSheet({ name, sl, opponentPlayerId, loading: true, data: null, offline: false, stale: false });
    fetchPlayerProfile(name, sl, opponentPlayerId)
      .then(({ result, offline, stale }) => {
        setProfileSheet((current) =>
          current?.name === name
            ? { ...current, loading: false, data: result, offline, stale }
            : current
        );
      })
      .catch(() => {
        setProfileSheet((current) =>
          current?.name === name
            ? { ...current, loading: false, data: null, offline: !navigator.onLine, stale: true }
            : current
        );
      });
  }

  function makeLongPressHandlers(name, sl, opponentPlayerId) {
    return {
      onPointerDown: () => {
        longPressTimer.current = setTimeout(() => openProfile(name, sl, opponentPlayerId), 500);
      },
      onPointerUp: () => clearTimeout(longPressTimer.current),
      onPointerLeave: () => clearTimeout(longPressTimer.current),
      onPointerCancel: () => clearTimeout(longPressTimer.current),
    };
  }

  function abbrev(name) {
    const parts = name.split(' ');
    return parts.length >= 2 && name.length > 14
      ? parts[0][0] + '. ' + parts.slice(1).join(' ')
      : name;
  }

  // Reset wheel position after each turn is recorded
  useEffect(() => {
    if (wheelRef.current) {
      wheelRef.current.scrollTop = 2 * ITEM_H;
    }
  }, [turns.length]);

  // Draw SVG arrows + position score wheel after every render
  useEffect(() => {
    const canvas = canvasRef.current;
    const svg = svgRef.current;
    const midCol = midColRef.current;
    const inline = inlineRef.current;
    if (!canvas || !svg) return;

    const SVG_NS = 'http://www.w3.org/2000/svg';
    const canvasR = canvas.getBoundingClientRect();
    const W = canvasR.width, H = canvasR.height;
    const gapCX = midCol
      ? midCol.getBoundingClientRect().left + midCol.getBoundingClientRect().width / 2 - canvasR.left
      : W / 2;

    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.innerHTML = '';

    function mkEl(tag, attrs) {
      const el = document.createElementNS(SVG_NS, tag);
      for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
      return el;
    }

    const defs = mkEl('defs', {});
    for (const [id, color] of [['ah-win', '#176b4d'], ['ah-loss', '#b23a33'], ['ah-pend', '#b9851d']]) {
      const m = mkEl('marker', { id, markerWidth: '9', markerHeight: '7', refX: '8', refY: '3.5', orient: 'auto' });
      m.appendChild(mkEl('polygon', { points: '0 0,9 3.5,0 7', fill: color }));
      defs.appendChild(m);
    }
    svg.appendChild(defs);

    function anchorByIndex(colClass, index, side) {
      const col = canvas.querySelector(`.${colClass}`);
      if (!col) return null;
      const row = col.querySelectorAll('.p-row')[index];
      if (!row) return null;
      const r = row.getBoundingClientRect();
      return {
        x: side === 'right' ? r.right - canvasR.left : r.left - canvasR.left,
        y: r.top + r.height / 2 - canvasR.top,
      };
    }

    function anchorByClass(colClass, cls, side) {
      const col = canvas.querySelector(`.${colClass}`);
      if (!col) return null;
      const row = col.querySelector(`.${cls}`);
      if (!row) return null;
      const r = row.getBoundingClientRect();
      return {
        x: side === 'right' ? r.right - canvasR.left : r.left - canvasR.left,
        y: r.top + r.height / 2 - canvasR.top,
      };
    }

    function drawArrow({ x1, y1, x2, y2, color, dashed, markerId, badge }) {
      const g = mkEl('g', {});
      g.appendChild(mkEl('line', {
        x1, y1, x2, y2, stroke: color,
        'stroke-width': dashed ? 1.5 : 2,
        ...(dashed ? { 'stroke-dasharray': '5 3' } : {}),
        'marker-end': `url(#${markerId})`,
      }));
      if (badge) {
        const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
        const fg = badge.win ? '#176b4d' : '#b23a33';
        const bg = badge.win ? '#dceee5' : '#f2dddd';
        g.appendChild(mkEl('rect', { x: mx - 15, y: my - 7.5, width: 30, height: 15, rx: 8, fill: bg, stroke: fg, 'stroke-width': 1 }));
        const txt = mkEl('text', { x: mx, y: my + 4.5, 'text-anchor': 'middle', 'font-family': 'Inter,sans-serif', 'font-size': '8', 'font-weight': '900', fill: fg });
        txt.textContent = badge.label;
        g.appendChild(txt);
      }
      svg.appendChild(g);
    }

    turns.forEach((t, i) => {
      const from = anchorByIndex('our-col', i, 'right');
      const to = anchorByIndex('their-col', i, 'left');
      if (!from || !to) return;
      const win = t.our_score >= 2;
      drawArrow({ x1: from.x, y1: from.y, x2: to.x, y2: to.y,
        color: win ? '#176b4d' : '#b23a33',
        markerId: win ? 'ah-win' : 'ah-loss',
        badge: { label: `${t.our_score}-${t.their_score}`, win } });
    });

    if (selectedOur) {
      const from = anchorByClass('our-col', 'sel-ours', 'right');
      if (from) {
        if (selectedTheir) {
          const to = anchorByClass('their-col', 'sel-theirs', 'left');
          if (to) {
            const midY = (from.y + to.y) / 2;
            svg.appendChild(mkEl('line', { x1: from.x, y1: from.y, x2: gapCX, y2: midY, stroke: '#b9851d', 'stroke-width': 1.5, 'stroke-dasharray': '5 3' }));
            svg.appendChild(mkEl('line', { x1: gapCX, y1: midY, x2: to.x, y2: to.y, stroke: '#b9851d', 'stroke-width': 1.5, 'stroke-dasharray': '5 3', 'marker-end': 'url(#ah-pend)' }));
          }
        } else {
          svg.appendChild(mkEl('line', { x1: from.x, y1: from.y, x2: gapCX, y2: from.y, stroke: '#b9851d', 'stroke-width': 1.5, 'stroke-dasharray': '5 3' }));
        }
      }
    }

    if (inline && midCol) {
      if (!selectedOur) { inline.style.display = 'none'; return; }
      const ourRow = canvas.querySelector('.our-col .sel-ours');
      if (!ourRow) { inline.style.display = 'none'; return; }
      const midR = midCol.getBoundingClientRect();
      const rowR = ourRow.getBoundingClientRect();
      let arrowY = rowR.top + rowR.height / 2 - canvasR.top;
      if (selectedTheir) {
        const theirRow = canvas.querySelector('.their-col .sel-theirs');
        if (theirRow) {
          const theirR = theirRow.getBoundingClientRect();
          arrowY = (rowR.top + rowR.height / 2 + theirR.top + theirR.height / 2) / 2 - canvasR.top;
        }
      }
      const wheelH = 72, wheelW = 44;
      const topY = Math.max(38, Math.min(H - wheelH - 4, arrowY - wheelH / 2));
      inline.style.top = topY + 'px';
      inline.style.left = ((midR.width - wheelW) / 2) + 'px';
      inline.style.display = 'block';
    }
  });

  function onWheelScroll() {
    clearTimeout(wheelTimer.current);
    wheelTimer.current = setTimeout(() => {
      if (!wheelRef.current) return;
      const idx = Math.max(0, Math.min(Math.round(wheelRef.current.scrollTop / ITEM_H), SCORE_OPTIONS.length - 1));
      setSelectedScore(SCORE_OPTIONS[idx]);
    }, 80);
  }

  function rosterRows(side) {
    const roster = side === 'our' ? ourRoster : theirRoster;
    const eligible = side === 'our' ? ourEligible : theirEligible;
    const sel = side === 'our' ? selectedOur : selectedTheir;
    const onSelect = side === 'our' ? setSelectedOur : setSelectedTheir;
    const selClass = side === 'our' ? 'sel-ours' : 'sel-theirs';
    const counts = getPlayCounts(turns, side);
    const dpUsed = turns.some((t) => side === 'our' ? t.is_our_dp : t.is_their_dp);
    const nextTurn = turns.length + 1;
    // Current opponent selection gives context for H2H when opening own player profile
    const opponentSelected = side === 'our' ? selectedTheir : selectedOur;
    const opponentRoster = side === 'our' ? theirRoster : ourRoster;
    const rows = [];

    function profileBtn(name, sl) {
      const form = getFormBadgeFromCache(name);
      const cfg = form ? FORM_ICON_CONFIGS[form.badge] : null;
      const Icon = cfg ? cfg.Icon : Info;
      return (
        <button
          key={`info-${name}`}
          className="p-row-info"
          aria-label={`View ${name} profile`}
          title={cfg ? `${form.badge.toUpperCase()} — View ${name} profile` : `View ${name} profile`}
          style={cfg ? { color: cfg.color } : undefined}
          onPointerDown={(e) => { e.stopPropagation(); }}
          onClick={(e) => {
            e.stopPropagation();
            const oppId = opponentSelected
              ? opponentSelected.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')
              : undefined;
            openProfile(name, sl, oppId);
          }}
        >
          <Icon size={14} />
        </button>
      );
    }

    // Completed turns in match order — use div (disabled button blocks pointer events)
    turns.forEach((t, i) => {
      const name = side === 'our' ? t.our_player_name : t.their_player_name;
      const sl = side === 'our' ? t.our_sl_snapshot : t.their_sl_snapshot;
      const win = side === 'our' ? t.our_score >= 2 : t.their_score >= 2;
      rows.push(
        <div key={`played-${i}`} className="p-row played" data-name={name}
          {...makeLongPressHandlers(name, sl)}>
          <span className={`t-badge ${win ? 'win' : 'loss'}`}>T{i + 1}</span>
          <span className="p-name dim">{abbrev(name)}</span>
          <span className="pl-sl">SL {sl}</span>
          {profileBtn(name, sl)}
        </div>,
      );
    });

    // In-progress (currently selected)
    if (sel && turns.length < MAX_TURNS) {
      const sl = roster[sel] ?? 0;
      rows.push(
        <button key="inprogress" className={`p-row ${selClass}`} data-name={sel}
          onClick={() => onSelect('')}
          {...makeLongPressHandlers(sel, sl)}>
          <span className="t-badge live">T{nextTurn}</span>
          <span className="p-name">{abbrev(sel)}</span>
          <span className="pl-sl">SL {sl || '?'}</span>
          {profileBtn(sel, sl)}
        </button>,
      );
    }

    // Eligible and ineligible remaining
    Object.keys(roster).forEach((name) => {
      if (name === sel) return;
      const count = counts[name] || 0;
      if (count >= 2) return;
      if (count === 1 && dpUsed) return;
      const sl = roster[name];
      const oppId = opponentSelected
        ? opponentSelected.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')
        : undefined;
      if (count === 1) {
        rows.push(
          <button key={`dp-${name}`} className="p-row" data-name={name}
            onClick={() => onSelect(name)}
            {...makeLongPressHandlers(name, sl, oppId)}>
            <span className="t-badge dp">DP</span>
            <span className="p-name">{abbrev(name)}</span>
            <span className="pl-sl">SL {sl}</span>
            {profileBtn(name, sl)}
          </button>,
        );
        return;
      }
      if (eligible[name]) {
        rows.push(
          <button key={`elig-${name}`} className="p-row" data-name={name}
            onClick={() => onSelect(name)}
            {...makeLongPressHandlers(name, sl, oppId)}>
            <span className="t-badge" />
            <span className="p-name">{abbrev(name)}</span>
            <span className="pl-sl">SL {sl}</span>
            {profileBtn(name, sl)}
          </button>,
        );
      } else {
        rows.push(
          <button key={`inelig-${name}`} className="p-row ineligible" data-name={name}
            {...makeLongPressHandlers(name, sl, oppId)}>
            <span className="t-badge" />
            <span className="p-name dim">{abbrev(name)}</span>
            <span className="pl-sl">SL {sl}</span>
            {profileBtn(name, sl)}
          </button>,
        );
      }
    });

    return rows;
  }

  return (
    <main className="match-grid">
      <section className="live-stack">
        <LiveHeader summary={summary} turnNumber={turnNumber} isThrowing={isThrowing} />
        {summary.clinched_by && (
          <div className={summary.clinched_by === 'ours' ? 'clinch-banner win' : 'clinch-banner loss'}>
            {summary.clinched_by === 'ours' ? 'VICTORY clinched' : 'DEFEAT clinched'}
          </div>
        )}

        <div className="matchup-canvas" ref={canvasRef}>
          <div className="matchup-col our-col">
            <div className="col-head">AVL</div>
            {rosterRows('our')}
          </div>
          <div className="mid-col" ref={midColRef}>
            <div className="score-inline" ref={inlineRef} style={{ display: 'none' }}>
              <div className="score-inline-band" />
              <div className="score-inline-scroller" ref={wheelRef} onScroll={onWheelScroll}>
                <div style={{ height: ITEM_H, flexShrink: 0 }} />
                {SCORE_OPTIONS.map((s, i) => {
                  const active = selectedScore?.label === s.label;
                  return (
                    <div key={s.label}
                      className={`score-inline-item${active ? ` active ${s.our >= 2 ? 'win' : 'loss'}` : ''}`}
                      onClick={() => {
                        wheelRef.current?.scrollTo({ top: i * ITEM_H, behavior: 'smooth' });
                        setSelectedScore(s);
                      }}
                    >
                      {s.label}
                    </div>
                  );
                })}
                <div style={{ height: ITEM_H, flexShrink: 0 }} />
              </div>
            </div>
          </div>
          <div className="matchup-col their-col">
            <div className="col-head">Opp</div>
            {rosterRows('their')}
          </div>
          <svg ref={svgRef} className="arrow-svg" />
        </div>

        <div className="live-action-row">
          <button className="primary-action" onClick={saveTurn}
            disabled={!selectedOur || !selectedTheir || !selectedScore || summary.complete}>
            <Save size={20} />
            Record Turn {Math.min(turns.length + 1, MAX_TURNS)}
          </button>
          <button className="ghost-button" onClick={askForSuggestion}
            disabled={busy === 'suggest' || summary.complete}>
            <Bot size={18} />
            {busy === 'suggest' ? 'Asking' : 'Suggest'}
          </button>
        </div>

        {suggestion && (
          <div className="suggestion-chip-wrap">
            <button className="suggestion-chip" onClick={() => setSelectedOur(suggestion)}
              disabled={!ourEligible[suggestion]}>
              <Bot size={18} />
              {suggestion}
            </button>
            <button
              className="suggestion-chip-info"
              aria-label={`View ${suggestion} profile`}
              title={`View ${suggestion} profile`}
              onClick={() => {
                const sl = ourRoster[suggestion] ?? 0;
                const oppId = selectedTheir
                  ? selectedTheir.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')
                  : undefined;
                openProfile(suggestion, sl, oppId);
              }}
            >
              <Info size={16} />
            </button>
          </div>
        )}

        <div className="cancel-band">
          <button className="ghost-button danger" onClick={cancelMatch}>
            <X size={18} />
            Cancel Match
          </button>
        </div>
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

      {profileSheet && (
        <PlayerProfileSheet
          sheet={profileSheet}
          onClose={() => setProfileSheet(null)}
        />
      )}
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

function FormBadge({ badge, reliable }) {
  const configs = {
    hot: { label: 'HOT', bg: '#fff0c2', color: '#7a5200', Icon: Flame },
    mid: { label: 'MID', bg: 'var(--green-soft)', color: 'var(--green)', Icon: Minus },
    low: { label: 'LOW', bg: 'var(--red-soft)', color: 'var(--red)', Icon: TrendingDown },
  };
  const cfg = configs[badge] || configs.mid;
  const { Icon } = cfg;
  return (
    <span
      className="form-badge"
      style={{
        background: cfg.bg,
        color: cfg.color,
        opacity: reliable === false ? 0.6 : 1,
      }}
      title={reliable === false ? 'Based on fewer than 5 recent matches' : undefined}
    >
      <Icon size={14} />
      {cfg.label}
    </span>
  );
}

function ProfileSkeleton() {
  return (
    <div className="profile-skeleton">
      <div className="skel-line wide" />
      <div className="skel-line medium" />
      <div className="skel-row">
        <div className="skel-block" />
        <div className="skel-block" />
        <div className="skel-block" />
      </div>
      <div className="skel-line medium" />
      <div className="skel-line short" />
      <div className="skel-line short" />
    </div>
  );
}

function PlayerProfileSheet({ sheet, onClose }) {
  const { name, sl, loading, data, offline, stale } = sheet;

  function relativeDate(isoStr) {
    if (!isoStr) return '';
    const diff = Date.now() - new Date(isoStr).getTime();
    const days = Math.floor(diff / 86400000);
    if (days === 0) return 'today';
    if (days === 1) return 'yesterday';
    return `${days} days ago`;
  }

  return (
    <>
      <div className="profile-overlay" onClick={onClose} />
      <div className="profile-sheet" role="dialog" aria-modal="true" aria-label={`${name} profile`}>
        <div className="profile-drag-handle" />

        {(offline || stale) && data && (
          <div className="profile-offline-banner">
            Offline — data from {relativeDate(data.cached_at)}
          </div>
        )}
        {offline && !data && (
          <div className="profile-offline-banner">
            <WifiOff size={14} />
            No cached data available. Connect to the internet and try again.
          </div>
        )}

        <div className="profile-zone-a">
          <div className="profile-identity">
            {data?.form && (
              <FormBadge badge={data.form.badge} reliable={data.form.reliable} />
            )}
            <span className="profile-name">{name}</span>
            <span className="pl-sl">SL {sl}</span>
            <button className="profile-close" onClick={onClose} aria-label="Close profile">
              <X size={18} />
            </button>
          </div>
          {loading && !data && <ProfileSkeleton />}
          {data?.narrative && (
            <p className="profile-narrative">{data.narrative}</p>
          )}
        </div>

        {data && (
          <>
            <div className="profile-divider" />
            <div className="profile-zone-b">
              <div className="profile-metric">
                <strong>{data.player.eb_win_pct != null ? `${Math.round(data.player.eb_win_pct)}%` : '—'}</strong>
                <span>Win %</span>
              </div>
              <div className="profile-metric">
                <strong>{data.player.eb_matches_played ?? '—'}</strong>
                <span>Matches</span>
              </div>
              <div className="profile-metric">
                <strong>{data.player.avg_opponent_sl != null ? Number(data.player.avg_opponent_sl).toFixed(1) : '—'}</strong>
                <span>Avg Opp SL</span>
              </div>
            </div>

            {sheet.opponentPlayerId !== undefined && (
              <>
                <div className="profile-divider" />
                <div className="profile-zone-c">
                  <span className="profile-section-label">H2H vs opponent</span>
                  {data.h2h ? (
                    <span className="profile-h2h">
                      <span className="h2h-dot" />
                      {data.h2h.wins}W – {data.h2h.losses}L
                    </span>
                  ) : (
                    <span className="profile-h2h-none">No history vs this player</span>
                  )}
                </div>
              </>
            )}

            {data.recent_sessions?.length > 0 && (
              <>
                <div className="profile-divider" />
                <div className="profile-zone-d">
                  <span className="profile-section-label">Recent Sessions</span>
                  {data.recent_sessions.map((s, i) => (
                    <div key={i} className="profile-session-row">
                      <span className="session-name">{s.session_name || '—'}</span>
                      <span className="session-record">
                        {s.matches_won}W–{s.matches_played - s.matches_won}L
                      </span>
                      {s.team_name && <span className="session-team">{s.team_name}</span>}
                    </div>
                  ))}
                </div>
              </>
            )}
          </>
        )}
      </div>
    </>
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

const HISTORY_PAGE_SIZE = 20;

function getUserEmail() {
  try {
    const token = localStorage.getItem('apa-gis-token');
    return token ? JSON.parse(atob(token.split('.')[1])).email : null;
  } catch { return null; }
}

// Returns { flipped, involved }.
// flipped=true means stored 'our_*' fields belong to the other team — swap for display.
// involved=false means neither side is the user's team — show match neutrally.
function resolveMatchPerspective(match, myTeams) {
  if (!myTeams?.length) return { flipped: false, involved: false };
  const home = (match.home_team_name || match.our_team_name || '').toLowerCase();
  const away = (match.away_team_name || match.opponent_team_name || '').toLowerCase();
  if (myTeams.includes(home)) return { flipped: false, involved: true };
  if (myTeams.includes(away)) return { flipped: true, involved: true };
  return { flipped: false, involved: false };
}

function buildPlayerStats(matches, myTeams) {
  const stats = {};
  for (const match of matches) {
    const { flipped, involved } = resolveMatchPerspective(match, myTeams);
    if (!involved) continue;
    const sc = match.source_context || {};
    const turns = Array.isArray(match.turns) && match.turns.length > 0
      ? match.turns : (sc.turns || []);
    for (const turn of turns) {
      const name = flipped
        ? (turn.away_player_name ?? turn.their_player_name)
        : (turn.home_player_name ?? turn.our_player_name);
      const ourScore = Number(flipped
        ? (turn.away_score ?? turn.their_score ?? 0)
        : (turn.home_score ?? turn.our_score ?? 0));
      if (!name) continue;
      const s = stats[name] ?? (stats[name] = { player_name: name, wins: 0, losses: 0, points: 0, appearances: 0 });
      s.appearances += 1;
      s.points += ourScore;
      if (ourScore >= 2) s.wins += 1; else s.losses += 1;
    }
  }
  return Object.values(stats).sort((a, b) => b.wins - a.wins || a.player_name.localeCompare(b.player_name));
}

function truncTeam(name, max = 14) {
  if (!name) return '?';
  return name.length > max ? name.slice(0, max - 1) + '…' : name;
}

function computeLocalPlayerStats(name, historyData) {
  if (!name || !historyData?.matches) return null;
  let appearances = 0, wins = 0, losses = 0;
  const recentTurns = [];
  for (const match of historyData.matches) {
    const sc = match.source_context || {};
    const turns = Array.isArray(match.turns) && match.turns.length > 0 ? match.turns : (sc.turns || []);
    const summary = match.summary || sc.summary || {};
    const homeTeam = match.home_team_name || match.our_team_name || 'Home';
    const awayTeam = match.away_team_name || match.opponent_team_name || 'Away';
    const homeTotal = summary.our_score ?? '?';
    const awayTotal = summary.their_score ?? '?';

    for (const turn of turns) {
      const homePl = turn.home_player_name ?? turn.our_player_name;
      const awayPl = turn.away_player_name ?? turn.their_player_name;
      const homeScore = Number(turn.home_score ?? turn.our_score ?? 0);
      const awayScore = Number(turn.away_score ?? turn.their_score ?? 0);

      const isHome = homePl === name;
      const isAway = awayPl === name;
      if (!isHome && !isAway) continue;

      appearances++;
      const playerScore = isHome ? homeScore : awayScore;
      const oppScore = isHome ? awayScore : homeScore;
      if (playerScore >= 2) wins++; else losses++;

      recentTurns.push({
        date: match.date,
        playerTeam: truncTeam(isHome ? homeTeam : awayTeam),
        oppPlayer: isHome ? awayPl : homePl,
        oppTeam: truncTeam(isHome ? awayTeam : homeTeam),
        turnScore: `${playerScore}-${oppScore}`,
        teamScore: isHome ? `${homeTotal}-${awayTotal}` : `${awayTotal}-${homeTotal}`,
        won: playerScore >= 2,
      });
    }
  }
  return { appearances, wins, losses, recentTurns: recentTurns.slice(0, 10) };
}

function PlayerView({ initialPlayer, historyData, onBack }) {
  const [query, setQuery] = useState(initialPlayer?.name || '');
  const [profile, setProfile] = useState(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileError, setProfileError] = useState('');
  const [activePlayer, setActivePlayer] = useState(initialPlayer || null);

  const localStats = useMemo(
    () => computeLocalPlayerStats(activePlayer?.name, historyData),
    [activePlayer?.name, historyData]
  );

  useEffect(() => {
    if (!activePlayer) return;
    setProfileLoading(true);
    setProfileError('');
    setProfile(null);
    fetchPlayerProfile(activePlayer.name, activePlayer.sl || null)
      .then(({ result }) => setProfile(result))
      .catch((err) => setProfileError(err.message || 'Failed to load profile'))
      .finally(() => setProfileLoading(false));
  }, [activePlayer?.name]);

  function handleSearch(e) {
    e.preventDefault();
    if (!query.trim()) return;
    setActivePlayer({ name: query.trim(), sl: null });
  }

  return (
    <main className="history-layout">
      <section className="tool-surface player-view">
      <div className="player-view-header">
        <button className="ghost-button player-back-btn" onClick={onBack}>
          <ChevronLeft size={18} /> Back
        </button>
        <h2 className="player-view-title">Player Profile</h2>
      </div>

      <form className="player-search-bar" onSubmit={handleSearch}>
        <Search size={16} className="player-search-icon" />
        <input
          className="player-search-input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name…"
        />
        {query && (
          <button type="button" className="filter-clear" onClick={() => { setQuery(''); setActivePlayer(null); setProfile(null); }}>
            <X size={14} />
          </button>
        )}
        <button type="submit" className="player-search-btn">Go</button>
      </form>

      {profileError && <p className="notice">{profileError}</p>}
      {profileLoading && <ProfileSkeleton />}

      {activePlayer && !profileLoading && profile && (
        <div className="player-profile-full">
          <div className="profile-zone-a">
            <div className="profile-identity">
              {profile.form && <FormBadge badge={profile.form.badge} reliable={profile.form.reliable} />}
              <span className="profile-name">{activePlayer.name}</span>
              {activePlayer.sl ? <span className="pl-sl">SL {activePlayer.sl}</span> : null}
            </div>
            {profile.narrative && <p className="profile-narrative">{profile.narrative}</p>}
          </div>

          <div className="profile-divider" />
          <div className="profile-zone-b">
            <div className="profile-metric">
              <strong>{profile.player.eb_win_pct != null ? `${Math.round(profile.player.eb_win_pct)}%` : '—'}</strong>
              <span>Win %</span>
            </div>
            <div className="profile-metric">
              <strong>{profile.player.eb_matches_played ?? '—'}</strong>
              <span>Matches</span>
            </div>
            <div className="profile-metric">
              <strong>{profile.player.avg_opponent_sl != null ? Number(profile.player.avg_opponent_sl).toFixed(1) : '—'}</strong>
              <span>Avg Opp SL</span>
            </div>
            <div className="profile-metric">
              <strong>{profile.player.eb_rackless ?? '—'}</strong>
              <span>Rackless</span>
            </div>
            <div className="profile-metric">
              <strong>{profile.player.eb_break_and_runs ?? '—'}</strong>
              <span>B&amp;Rs</span>
            </div>
            <div className="profile-metric">
              <strong>{profile.player.eb_defensive_shot_avg != null ? Number(profile.player.eb_defensive_shot_avg).toFixed(2) : '—'}</strong>
              <span>DSA</span>
            </div>
          </div>

          {profile.player.apr && (
            <>
              <div className="profile-divider" />
              <div className="profile-zone-c">
                <span className="profile-section-label">APR Score</span>
                <div className="profile-zone-b" style={{ marginTop: 8 }}>
                  <div className="profile-metric">
                    <strong>{profile.player.apr.score}</strong>
                    <span>APR ({profile.player.apr.band})</span>
                  </div>
                  <div className="profile-metric">
                    <strong>{profile.player.apr.mps}</strong>
                    <span>MPS</span>
                  </div>
                  <div className="profile-metric">
                    <strong>{profile.player.apr.ppms}</strong>
                    <span>PPMS</span>
                  </div>
                  <div className="profile-metric">
                    <strong>{profile.player.apr.pas}</strong>
                    <span>PAS</span>
                  </div>
                </div>
              </div>
            </>
          )}

          {profile.recent_sessions?.length > 0 && (
            <>
              <div className="profile-divider" />
              <div className="profile-zone-d">
                <span className="profile-section-label">APA Sessions</span>
                {profile.recent_sessions.map((s, i) => (
                  <div key={i} className="profile-session-row">
                    <span className="session-name">{s.session_name || '—'}</span>
                    <span className="session-record">{s.matches_won}W–{s.matches_played - s.matches_won}L</span>
                    {s.team_name && <span className="session-team">{s.team_name}</span>}
                  </div>
                ))}
              </div>
            </>
          )}

          {localStats && localStats.appearances > 0 && (
            <>
              <div className="profile-divider" />
              <div className="profile-zone-d">
                <span className="profile-section-label">AVL Record (this app)</span>
                <div className="profile-zone-b" style={{ marginTop: 8 }}>
                  <div className="profile-metric">
                    <strong>{localStats.appearances}</strong>
                    <span>Turns</span>
                  </div>
                  <div className="profile-metric">
                    <strong>{localStats.wins}</strong>
                    <span>Wins</span>
                  </div>
                  <div className="profile-metric">
                    <strong>{localStats.losses}</strong>
                    <span>Losses</span>
                  </div>
                  <div className="profile-metric">
                    <strong>{localStats.appearances > 0 ? `${Math.round(localStats.wins / localStats.appearances * 100)}%` : '—'}</strong>
                    <span>Win %</span>
                  </div>
                </div>
                {localStats.recentTurns.length > 0 && (
                  <div className="player-recent-matches">
                    {localStats.recentTurns.map((t, i) => (
                      <div key={i} className="player-recent-row">
                        <span className="player-recent-date">{formatMatchDate(t.date)}</span>
                        <span className="player-recent-matchup">
                          <span className="prm-player">{activePlayer.name}</span>
                          <span className="prm-team">({t.playerTeam})</span>
                          {' vs '}
                          <span className="prm-player">{t.oppPlayer || '?'}</span>
                          <span className="prm-team">({t.oppTeam})</span>
                        </span>
                        <span className={`player-recent-score${t.won ? ' win' : ''}`}>
                          {t.turnScore}
                          <span className="prm-team-score"> ({t.teamScore})</span>
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}

      {activePlayer && !profileLoading && !profile && !profileError && (
        <p className="empty-state">No Neon data found for "{activePlayer.name}".</p>
      )}
      </section>
    </main>
  );
}

function HistoryView({ historyData, busy, expandedHistory, setExpandedHistory, onReEdit, onStartNew, onDelete, onPlayerClick }) {
  const [divisionFilter, setDivisionFilter] = useState('');
  const [teamFilter, setTeamFilter] = useState('');
  const [playerFilter, setPlayerFilter] = useState('');
  const [page, setPage] = useState(0);

  const matches = historyData.matches;

  const myTeams = (() => {
    const email = getUserEmail();
    return email ? (USER_TEAMS[email] || []) : [];
  })();
  const playerStats = buildPlayerStats(matches, myTeams);

  const uniqueDivisions = [...new Set(matches.map((m) => m.division_name).filter(Boolean))].sort();
  const uniqueTeams = [...new Set(
    matches.flatMap((m) => [
      m.home_team_name || m.our_team_name,
      m.away_team_name || m.opponent_team_name,
    ]).filter(Boolean)
  )].sort();
  const uniquePlayers = [...new Set(
    matches.flatMap((m) => {
      const sc = m.source_context || {};
      const turns = Array.isArray(m.turns) && m.turns.length > 0 ? m.turns : (sc.turns || []);
      return turns.flatMap((t) => [
        t.home_player_name ?? t.our_player_name,
        t.away_player_name ?? t.their_player_name,
      ]).filter(Boolean);
    })
  )].sort();

  function resetPage() { setPage(0); setExpandedHistory(null); }

  const filteredMatches = matches.filter((m) => {
    if (divisionFilter && m.division_name !== divisionFilter) return false;
    if (teamFilter) {
      const q = teamFilter.toLowerCase();
      const home = (m.home_team_name || m.our_team_name || '').toLowerCase();
      const away = (m.away_team_name || m.opponent_team_name || '').toLowerCase();
      if (!home.includes(q) && !away.includes(q)) return false;
    }
    if (playerFilter) {
      const sc = m.source_context || {};
      const turns = Array.isArray(m.turns) && m.turns.length > 0 ? m.turns : (sc.turns || []);
      const played = turns.some((t) =>
        (t.home_player_name ?? t.our_player_name) === playerFilter ||
        (t.away_player_name ?? t.their_player_name) === playerFilter
      );
      if (!played) return false;
    }
    return true;
  });

  const totalPages = Math.ceil(filteredMatches.length / HISTORY_PAGE_SIZE);
  const pagedMatches = filteredMatches.slice(page * HISTORY_PAGE_SIZE, (page + 1) * HISTORY_PAGE_SIZE);

  const hasFilters = divisionFilter || teamFilter || playerFilter;

  return (
    <main className="history-layout">
      <section className="tool-surface">
        <div className="section-title split-title">
          <span>
            <History size={20} />
            <h2>Season History</h2>
          </span>
          {busy === 'history' && <RefreshCw className="spin" size={18} />}
        </div>

        <div className="history-filters">
          {uniqueDivisions.length > 1 && (
            <div className="history-filter-row">
              <label className="filter-label">Division</label>
              <select value={divisionFilter} onChange={(e) => { setDivisionFilter(e.target.value); resetPage(); }}>
                <option value="">All</option>
                {uniqueDivisions.map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
              {divisionFilter && (
                <button className="filter-clear" onClick={() => { setDivisionFilter(''); resetPage(); }}><X size={14} /></button>
              )}
            </div>
          )}
          {uniqueTeams.length > 1 && (
            <div className="history-filter-row">
              <label className="filter-label">Team</label>
              <select value={teamFilter} onChange={(e) => { setTeamFilter(e.target.value); resetPage(); }}>
                <option value="">All</option>
                {uniqueTeams.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              {teamFilter && (
                <button className="filter-clear" onClick={() => { setTeamFilter(''); resetPage(); }}><X size={14} /></button>
              )}
            </div>
          )}
          {uniquePlayers.length > 1 && (
            <div className="history-filter-row">
              <label className="filter-label">Player</label>
              <select value={playerFilter} onChange={(e) => { setPlayerFilter(e.target.value); resetPage(); }}>
                <option value="">All</option>
                {uniquePlayers.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
              {playerFilter && (
                <button className="filter-clear" onClick={() => { setPlayerFilter(''); resetPage(); }}><X size={14} /></button>
              )}
            </div>
          )}
        </div>

        <div className="history-list">
          {pagedMatches.map((match) => (
            <HistoryCard
              key={match.match_id}
              match={match}
              myTeams={myTeams}
              expanded={expandedHistory === match.match_id}
              onToggle={() => setExpandedHistory(expandedHistory === match.match_id ? null : match.match_id)}
              onEdit={() => onReEdit(match)}
              onDelete={() => onDelete(match.match_id)}
              onPlayerClick={onPlayerClick}
            />
          ))}
          {!filteredMatches.length && busy !== 'history' && (
            <p className="empty-state">
              {hasFilters ? 'No matches match the selected filters.' : 'No completed matches found.'}
            </p>
          )}
        </div>

        {totalPages > 1 && (
          <div className="history-paging">
            <button onClick={() => setPage((p) => p - 1)} disabled={page === 0}>
              <ChevronLeft size={16} />
            </button>
            <span>{page + 1} / {totalPages}</span>
            <button onClick={() => setPage((p) => p + 1)} disabled={page >= totalPages - 1}>
              <ChevronRight size={16} />
            </button>
          </div>
        )}
      </section>

      <section className="tool-surface">
        <div className="section-title">
          <Users size={20} />
          <h2>Player Stats</h2>
        </div>
        <div className="stat-list">
          {playerStats.map((player) => (
            <div key={player.player_name} className="stat-line">
              <strong>{player.player_name}</strong>
              <span>{player.wins}-{player.losses}</span>
              <span>{player.points} pts</span>
            </div>
          ))}
        </div>
      </section>

      <button className="history-add-btn" onClick={onStartNew}>
        <Plus size={20} />
        Manually Add Match
      </button>
    </main>
  );
}

function HistoryCard({ match, myTeams, expanded, onToggle, onEdit, onDelete, onPlayerClick }) {
  // TURN# DynamoDB writes fail when the submit payload violates a backend rule
  // (e.g. opponent SL > 23). In those cases the actual data lives in
  // source_context, which is always stored regardless of turn validation.
  const sc = match.source_context || {};
  const hasTurns = Array.isArray(match.turns) && match.turns.length > 0;
  const turns = hasTurns ? match.turns : (sc.turns || []);
  const summary = hasTurns ? (match.summary || {}) : (sc.summary || match.summary || {});

  const { flipped, involved } = resolveMatchPerspective(match, myTeams);

  // Raw stored values (always from home/stored perspective)
  // Support both home/away (new) and our/their (old source_context) field names
  const homeTeam = match.home_team_name || match.our_team_name || 'Home';
  const awayTeam = match.away_team_name || match.opponent_team_name || 'Away';
  const rawHomeScore = summary.our_score ?? match.our_points_total ?? 0;
  const rawAwayScore = summary.their_score ?? match.their_points_total ?? 0;
  const rawHomeWins = summary.our_wins ?? match.our_matches_won ?? 0;
  const rawAwayWins = summary.their_wins ?? match.their_matches_won ?? 0;

  // Display values — swap if stored home perspective belongs to the opposing team
  const displayOurScore = flipped ? rawAwayScore : rawHomeScore;
  const displayTheirScore = flipped ? rawHomeScore : rawAwayScore;
  const won = flipped ? rawAwayWins > rawHomeWins : rawHomeWins > rawAwayWins;
  const scoreStr = `${displayOurScore}-${displayTheirScore}`;

  const ourTeam = flipped ? awayTeam : homeTeam;
  const opponent = flipped ? homeTeam : awayTeam;
  const submittedDate = match.updated_at || match.created_at;

  return (
    <article className="history-card">
      <div className="history-card-header">
        <div className="history-card-dates">
          <p className="eyebrow">{formatMatchDate(match.date)}</p>
          {submittedDate && (
            <p className="history-submitted-date">Submitted {formatSubmittedDate(submittedDate)}</p>
          )}
        </div>
        <div className="history-card-main">
          <span className="history-opponent">{ourTeam} vs {opponent}</span>
          <span className="history-card-result">
            {involved && (
              <span className={`result-badge${won ? '' : ' loss'}`}>{won ? 'W' : 'L'}</span>
            )}
            <strong className="history-score">{scoreStr}</strong>
          </span>
        </div>
      </div>
      <div className="history-card-actions">
        <button className="history-detail-btn" onClick={onToggle}>
          <ChevronDown size={14} style={expanded ? { transform: 'rotate(180deg)' } : undefined} />
          {expanded ? 'Hide Detail' : 'View Detail'}
        </button>
        <button className="ghost-button history-edit-btn" onClick={onEdit}>
          <Pencil size={14} />
          Edit
        </button>
        <button className="ghost-button history-delete-btn" onClick={onDelete}>
          <Trash2 size={14} />
          Delete
        </button>
      </div>
      {expanded && (
        <div className="history-turns">
          {turns.length ? turns.map((turn) => {
            const homePl = turn.home_player_name ?? turn.our_player_name;
            const awayPl = turn.away_player_name ?? turn.their_player_name;
            const homeScore = Number(turn.home_score ?? turn.our_score ?? 0);
            const awayScore = Number(turn.away_score ?? turn.their_score ?? 0);
            const homeSl = Number(turn.home_sl_snapshot ?? turn.our_sl_snapshot ?? 0);
            const awaySl = Number(turn.away_sl_snapshot ?? turn.their_sl_snapshot ?? 0);
            const ourPlayer = flipped ? awayPl : homePl;
            const theirPlayer = flipped ? homePl : awayPl;
            const ourSl = flipped ? awaySl : homeSl;
            const theirSl = flipped ? homeSl : awaySl;
            const ourTurnScore = flipped ? awayScore : homeScore;
            const theirTurnScore = flipped ? homeScore : awayScore;
            const turnWon = ourTurnScore >= 2;
            return (
              <div key={turn.turn_num} className="turn-row">
                <span className={`t-badge ${turnWon ? 'win' : 'loss'}`}>T{turn.turn_num}</span>
                <span className="turn-players">
                  {onPlayerClick && ourPlayer ? (
                    <button className="turn-player-link" onClick={() => onPlayerClick(ourPlayer, ourSl)}>{ourPlayer}</button>
                  ) : ourPlayer}
                  {' vs '}
                  {onPlayerClick && theirPlayer ? (
                    <button className="turn-player-link" onClick={() => onPlayerClick(theirPlayer, theirSl)}>{theirPlayer}</button>
                  ) : theirPlayer}
                </span>
                <span className={`turn-score${turnWon ? ' win' : ''}`}>
                  {ourTurnScore}-{theirTurnScore}
                </span>
              </div>
            );
          }) : <p className="empty-state">No turn details recorded.</p>}
        </div>
      )}
    </article>
  );
}

function formatMatchDate(dateStr) {
  if (!dateStr) return '—';
  const MONTHS = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  const [year, month, day] = dateStr.split('-');
  return `${MONTHS[Number(month) - 1]} ${day}, ${year}`;
}

function formatSubmittedDate(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  if (isNaN(d)) return '';
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`;
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

function SignInScreen({ deniedEmail }) {
  const truncated = deniedEmail && deniedEmail.length > 28
    ? deniedEmail.slice(0, 28) + '…'
    : deniedEmail;

  return (
    <div style={{ minHeight: '100dvh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg, #f5f3ee)' }}>
      <div style={{
        maxWidth: 360, width: '100%', margin: 'auto', padding: 24,
        background: 'var(--paper)', border: '1px solid var(--line)',
        borderRadius: 8, boxShadow: 'var(--shadow)',
      }}>
        <p style={{ margin: '0 0 4px', fontSize: '0.7rem', fontWeight: 850, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--gold)' }}>
          Anti-Villain League
        </p>
        <h1 style={{ margin: '0 0 8px', fontSize: '1.55rem', fontWeight: 700, color: 'var(--ink)' }}>
          Match Engine
        </h1>
        <p style={{ margin: '0 0 20px', fontSize: '0.875rem', color: 'var(--ink)', opacity: 0.7 }}>
          Sign in with your Google account to access captain tools.
        </p>
        {deniedEmail && (
          <div style={{
            marginBottom: 16, padding: '10px 12px',
            background: 'var(--red-soft)', border: '1px solid var(--red)',
            borderRadius: 8, color: 'var(--red)', fontWeight: 750, fontSize: '0.85rem',
          }}>
            Access denied. {truncated} is not on the captain list. Contact the captain to request access.
          </div>
        )}
        <div id="gis-button-target" />
      </div>
    </div>
  );
}
