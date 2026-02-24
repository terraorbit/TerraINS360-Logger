/**
 * TerraINS360 Service Worker v3.0
 * ================================
 * Offline-first caching strategy:
 *   - Static assets: Cache-first (HTML, CSS, JS, icons)
 *   - API reads:     Network-first, fall back to cache
 *   - API writes:    Queue in IndexedDB when offline, sync on reconnect
 */

const CACHE_NAME = 'terrains360-v3';
const STATIC_ASSETS = [
  '/',
  '/static/index.html',
  '/manifest.json',
];

const API_CACHE = 'terrains360-api-v3';
const SYNC_QUEUE_STORE = 'sync-queue';

// ── IndexedDB helpers for sync queue ──

function openSyncDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open('TerraINS360Sync', 1);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(SYNC_QUEUE_STORE)) {
        db.createObjectStore(SYNC_QUEUE_STORE, { keyPath: 'id', autoIncrement: true });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function queueRequest(url, method, body, headers) {
  const db = await openSyncDB();
  const tx = db.transaction(SYNC_QUEUE_STORE, 'readwrite');
  tx.objectStore(SYNC_QUEUE_STORE).add({
    url, method, body, headers,
    timestamp: Date.now(),
    retries: 0,
  });
  return new Promise((resolve, reject) => {
    tx.oncomplete = resolve;
    tx.onerror = reject;
  });
}

async function getQueuedRequests() {
  const db = await openSyncDB();
  const tx = db.transaction(SYNC_QUEUE_STORE, 'readonly');
  const store = tx.objectStore(SYNC_QUEUE_STORE);
  return new Promise((resolve, reject) => {
    const req = store.getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function removeFromQueue(id) {
  const db = await openSyncDB();
  const tx = db.transaction(SYNC_QUEUE_STORE, 'readwrite');
  tx.objectStore(SYNC_QUEUE_STORE).delete(id);
}


// ── Install: cache static assets ──

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});


// ── Activate: clean old caches ──

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k !== CACHE_NAME && k !== API_CACHE)
          .map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});


// ── Fetch strategy ──

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Skip non-GET external requests
  if (url.origin !== self.location.origin) return;

  // API write requests (POST, PUT, DELETE)
  if (url.pathname.startsWith('/api/') && event.request.method !== 'GET') {
    event.respondWith(handleAPIWrite(event.request));
    return;
  }

  // API read requests: network-first
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(handleAPIRead(event.request));
    return;
  }

  // Static assets: cache-first
  event.respondWith(handleStatic(event.request));
});


// ── Static: cache-first, network fallback ──

async function handleStatic(request) {
  const cached = await caches.match(request);
  if (cached) return cached;

  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    // Offline fallback for navigation
    if (request.mode === 'navigate') {
      const fallback = await caches.match('/');
      if (fallback) return fallback;
    }
    return new Response('Offline', { status: 503, statusText: 'Offline' });
  }
}


// ── API read: network-first, cache fallback ──

async function handleAPIRead(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      // Cache successful API responses
      const cache = await caches.open(API_CACHE);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    // Offline: return cached version
    const cached = await caches.match(request);
    if (cached) return cached;

    return new Response(
      JSON.stringify({ error: 'offline', message: 'No cached data available' }),
      { status: 503, headers: { 'Content-Type': 'application/json' } }
    );
  }
}


// ── API write: queue if offline ──

async function handleAPIWrite(request) {
  try {
    const response = await fetch(request.clone());
    return response;
  } catch (err) {
    // Offline: queue the request
    const body = await request.text();
    const headers = {};
    request.headers.forEach((v, k) => { headers[k] = v; });

    await queueRequest(request.url, request.method, body, headers);

    // Notify UI about queued request
    const clients = await self.clients.matchAll();
    clients.forEach(client => {
      client.postMessage({
        type: 'SYNC_QUEUED',
        url: request.url,
        method: request.method,
      });
    });

    return new Response(
      JSON.stringify({
        status: 'queued',
        message: 'Request queued for sync when online',
        offline: true,
      }),
      { status: 202, headers: { 'Content-Type': 'application/json' } }
    );
  }
}


// ── Background sync ──

self.addEventListener('sync', (event) => {
  if (event.tag === 'terrains360-sync') {
    event.waitUntil(processQueue());
  }
});


// ── Online event: process queue ──

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'ONLINE') {
    processQueue();
  }
  if (event.data && event.data.type === 'GET_QUEUE_COUNT') {
    getQueuedRequests().then(items => {
      event.source.postMessage({
        type: 'QUEUE_COUNT',
        count: items.length,
      });
    });
  }
});


async function processQueue() {
  const items = await getQueuedRequests();
  let synced = 0;
  let failed = 0;

  for (const item of items) {
    try {
      const response = await fetch(item.url, {
        method: item.method,
        body: item.body || undefined,
        headers: item.headers || {},
      });

      if (response.ok || response.status < 500) {
        await removeFromQueue(item.id);
        synced++;
      } else {
        failed++;
      }
    } catch (err) {
      failed++;
    }
  }

  // Notify UI
  const clients = await self.clients.matchAll();
  clients.forEach(client => {
    client.postMessage({
      type: 'SYNC_COMPLETE',
      synced, failed,
      remaining: items.length - synced,
    });
  });
}
