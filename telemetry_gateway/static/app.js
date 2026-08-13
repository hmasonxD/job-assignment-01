const states = new Map();
const grid = document.querySelector('#grid');
const empty = document.querySelector('#empty');
const status = document.querySelector('#connection-status');
const errorBox = document.querySelector('#error');
let stopped = false;
let retryTimer;
let socket;
let resyncing = false;
let queuedUpdates = [];

function stateKey(state) {
  return `${state.deviceId}:${state.metric}`;
}

function isNewerState(incoming, current) {
  return (
    !current ||
    incoming.generation > current.generation ||
    (incoming.generation === current.generation &&
      incoming.sequence > current.sequence)
  );
}

function applyState(state) {
  const key = stateKey(state);
  const current = states.get(key);

  // Apply the same authoritative ordering rule used by the database.
  if (isNewerState(state, current)) {
    states.set(key, state);
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function render() {
  const ordered = [...states.values()].sort(
    (left, right) =>
      left.deviceId.localeCompare(right.deviceId) ||
      left.metric.localeCompare(right.metric)
  );

  empty.classList.toggle('hidden', ordered.length > 0);
  grid.innerHTML = ordered
    .map(
      (state) => `
        <article>
          <div class="card-title">
            <h2>${escapeHtml(state.deviceId)}</h2>
            <span>${escapeHtml(state.metric)}</span>
          </div>
          <strong>${Number(state.value).toFixed(2)}</strong>
          <dl>
            <div><dt>Generation</dt><dd>${state.generation}</dd></div>
            <div><dt>Sequence</dt><dd>${state.sequence}</dd></div>
            <div><dt>Boot</dt><dd title="${escapeHtml(state.bootId)}">${escapeHtml(state.bootId.slice(0, 8))}</dd></div>
            <div><dt>Received</dt><dd>${new Date(state.receivedAt).toLocaleTimeString()}</dd></div>
          </dl>
        </article>
      `
    )
    .join('');
}

function setError(message) {
  errorBox.textContent = message || '';
  errorBox.classList.toggle('hidden', !message);
}

async function loadSnapshot() {
  const response = await fetch('/api/devices');
  if (!response.ok) {
    throw new Error(`Snapshot request failed with ${response.status}.`);
  }

  const body = await response.json();
  states.clear();
  for (const state of body.devices) {
    states.set(stateKey(state), state);
  }
  render();
}

async function resyncSnapshot() {
  resyncing = true;
  queuedUpdates = [];

  try {
    await loadSnapshot();
  } finally {
    // Apply notifications received during the fetch after the snapshot. The
    // ordering check prevents an older notification from regressing state.
    resyncing = false;
    for (const state of queuedUpdates) {
      applyState(state);
    }
    queuedUpdates = [];
    render();
  }
}

function connect() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  socket = new WebSocket(`${protocol}//${window.location.host}/ws`);

  socket.addEventListener('open', async () => {
    status.textContent = 'Realtime connected';
    status.className = 'status online';

    try {
      // Every successful connection reloads the database-backed source of truth.
      await resyncSnapshot();
      setError('');
    } catch (error) {
      setError(error.message);
    }
  });

  socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (message.type !== 'device.state.changed') {
      return;
    }

    if (resyncing) {
      queuedUpdates.push(message.data);
      return;
    }

    applyState(message.data);
    render();
  });

  socket.addEventListener('error', () => {
    setError('Realtime connection failed.');
  });

  socket.addEventListener('close', () => {
    status.textContent = 'Realtime disconnected';
    status.className = 'status offline';
    if (!stopped) {
      retryTimer = window.setTimeout(connect, 1000);
    }
  });
}

async function start() {
  try {
    // Load current state even when the WebSocket server is unavailable.
    await loadSnapshot();
  } catch (error) {
    setError(error.message);
  }

  connect();
}

start();

window.addEventListener('beforeunload', () => {
  stopped = true;
  window.clearTimeout(retryTimer);
  socket?.close();
});
