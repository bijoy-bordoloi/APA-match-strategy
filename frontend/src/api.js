const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || window.localStorage.getItem('apaApiBaseUrl') || '';

const QUEUE_KEY = 'apa-match-write-queue';

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
  const result = await request('/history', { method: 'GET' });
  return result.result || result;
}

export async function getRosters(week) {
  const path = week ? `/rosters?week=${week}` : '/rosters';
  const result = await request(path, { method: 'GET' });
  return result.result || result;
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
      ...(options.headers || {}),
    },
  });
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
