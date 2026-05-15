const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || window.localStorage.getItem('apaApiBaseUrl') || '';

const QUEUE_KEY = 'apa-match-write-queue';

function getToken() {
  return window.localStorage.getItem('apa-gis-token');
}

export function hasApiBase() {
  return Boolean(API_BASE_URL);
}

export async function createOrLoadMatch(payload) {
  return postJson('/match', payload);
}

export async function getSuggestion(payload) {
  return postJson('/suggest', payload);
}

export async function sendChat(payload) {
  return postJson('/chat', payload);
}

export async function recordResult(payload) {
  return postJson('/result', payload);
}

export async function submitMatch(payload) {
  return postJson('/submit', payload);
}

export async function getHistory() {
  const result = await request('/history?limit=200', { method: 'GET' });
  return result.result || result;
}

export async function deleteMatch(matchId) {
  return request('/match', { method: 'DELETE', body: JSON.stringify({ match_id: matchId }) });
}

export async function getRosters(week) {
  const path = week ? `/rosters?week=${week}` : '/rosters';
  const result = await request(path, { method: 'GET' });
  return result.result || result;
}

const PROFILE_CACHE_PREFIX = 'apa-profile-';
const PROFILE_CACHE_TTL_MS = 24 * 60 * 60 * 1000;

export async function fetchPlayerProfile(name, playerSl, opponentPlayerId) {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
  const cacheKey = `${PROFILE_CACHE_PREFIX}${slug}`;

  const isOnline = navigator.onLine;
  let cached = null;
  try {
    const raw = window.localStorage.getItem(cacheKey);
    if (raw) cached = JSON.parse(raw);
  } catch {
    cached = null;
  }

  const isStale = cached
    ? Date.now() - new Date(cached.cached_at).getTime() > PROFILE_CACHE_TTL_MS
    : true;

  if (!isOnline || !hasApiBase()) {
    if (cached) return { result: cached, offline: true, stale: isStale };
    return { result: null, offline: true, stale: true };
  }

  const params = new URLSearchParams({ name, player_sl: playerSl });
  if (opponentPlayerId) params.set('opponent_player_id', opponentPlayerId);

  try {
    const json = await request(`/players/profile?${params}`, { method: 'GET' });
    const profile = json.result;
    try {
      window.localStorage.setItem(cacheKey, JSON.stringify(profile));
    } catch {
      // localStorage full — non-fatal
    }
    return { result: profile, offline: false, stale: false };
  } catch (err) {
    if (cached) return { result: cached, offline: false, stale: isStale, fetchError: err.message };
    throw err;
  }
}

export function enqueueWrite(payload, path = '/result') {
  const queue = loadQueue();
  queue.push({
    id: crypto.randomUUID(),
    path,
    payload,
    queued_at: new Date().toISOString(),
  });
  saveQueue(queue);
  return queue;
}

export function loadQueue() {
  try {
    return JSON.parse(window.localStorage.getItem(QUEUE_KEY) || '[]');
  } catch {
    return [];
  }
}

export async function flushQueue() {
  if (!hasApiBase() || !navigator.onLine) {
    return loadQueue();
  }
  const queue = loadQueue();
  const remaining = [];
  for (const item of queue) {
    try {
      await postJson(item.path, item.payload);
    } catch {
      remaining.push(item);
    }
  }
  saveQueue(remaining);
  return remaining;
}

async function postJson(path, payload) {
  return request(path, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

async function request(path, options) {
  if (!API_BASE_URL) {
    throw new Error('API base URL is not configured.');
  }
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(getToken() ? { 'Authorization': `Bearer ${getToken()}` } : {}),
      ...(options.headers || {}),
    },
  });
  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent('apa-auth-expired'));
    throw new Error('Unauthorized');
  }
  if (response.status === 403) {
    const j = await response.json().catch(() => ({}));
    window.dispatchEvent(new CustomEvent('apa-auth-denied', { detail: { email: j?.error_detail?.email || '' } }));
    throw new Error('Access denied');
  }
  const json = await response.json().catch(() => ({}));
  if (!response.ok || json.error) {
    throw new Error(json.error || `Request failed with ${response.status}`);
  }
  return json;
}

function saveQueue(queue) {
  window.localStorage.setItem(QUEUE_KEY, JSON.stringify(queue));
  window.dispatchEvent(new CustomEvent('apa-queue-change', { detail: queue.length }));
}
