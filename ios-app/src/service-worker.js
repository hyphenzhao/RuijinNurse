// RuijinNurse Service Worker — offline asset caching
// Bump version when content changes to invalidate old caches on all devices
const CACHE_NAME = 'ruijin-nurse-v2';

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
  // Delete ALL old caches so stale content is never served again
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(keys.map((key) => caches.delete(key)));
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  // Only cache GET requests
  if (event.request.method !== 'GET') return;

  // Skip chrome-extension:// and other non-http(s) requests
  if (!event.request.url.startsWith('http')) return;

  // Stale-while-revalidate: return cached version immediately (fast),
  // then silently update the cache from network in the background.
  // This prevents stale content from being locked in forever.
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.open(CACHE_NAME).then((cache) => {
        return cache.match(event.request).then((cached) => {
          const fetchPromise = fetch(event.request).then((response) => {
            if (response.ok) {
              cache.put(event.request, response.clone());
            }
            return response;
          }).catch((err) => {
            console.warn('[SW] Fetch failed, using cache:', err);
          });
          // Return cached immediately, or wait for network if no cache
          return cached || fetchPromise;
        });
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
