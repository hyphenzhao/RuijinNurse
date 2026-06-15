// RuijinNurse Service Worker — offline asset caching
const CACHE_NAME = 'ruijin-nurse-v1';

// Static assets to cache on install (served by Django at /static/...)
const STATIC_ASSETS = [
  '/static/promotions/css/bootstrap.min.css',
  '/static/promotions/js/jquery-3.5.1.min.js',
  '/static/promotions/js/bootstrap.min.js',
  '/static/promotions/js/marked.min.js',
  '/static/promotions/js/purify.min.js',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS).catch((err) => {
        console.warn('[SW] Pre-cache failed (some assets may be unavailable):', err);
      });
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  // Only cache GET requests
  if (event.request.method !== 'GET') return;

  // Skip chrome-extension:// and other non-http(s) requests
  if (!event.request.url.startsWith('http')) return;

  // Network-first strategy for API calls (SSE streaming can't be cached)
  // Cache-first for static assets
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/static/')) {
    // Cache-first for static assets
    event.respondWith(
      caches.match(event.request).then((cached) => {
        return (
          cached ||
          fetch(event.request).then((response) => {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, clone);
            });
            return response;
          })
        );
      })
    );
  } else {
    // Network-first with no caching for dynamic/API content
    event.respondWith(
      fetch(event.request).catch(() => {
        return caches.match(event.request).then((cached) => {
          return cached || new Response('离线模式 — 数据当前不可用', { status: 503 });
        });
      })
    );
  }
});
