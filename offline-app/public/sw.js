const CACHE = "offline-study-v8";
const MODEL_COMMITS = {
  "Qwen3-0.6B-q4f16_1-MLC": "8c14ce481d4c692769976ad52afea453a102df19",
  "Qwen3-1.7B-q4f16_1-MLC": "80b3abcec6c3b3f5355dc0cc99cc4fb578f192bc",
  "Qwen3-4B-q4f16_1-MLC": "a5c9fab855e3ccbdfed2e7e69683d75f30332161",
};
const SHELL = [
  "./",
  "./index.html",
  "./manifest.webmanifest",
  "./assets/app.js",
  "./assets/index.css",
  "./course-data.enc.json",
  "./icon-192.png",
  "./icon-512.png",
  "./ocr/worker.min.js",
  "./ocr/tesseract-core-simd-lstm.wasm.js",
  "./ocr/tesseract-core-simd-lstm.wasm",
  "./tessdata/eng.traineddata.gz",
  "./tessdata/chi_sim.traineddata.gz",
];
self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).catch(() => {}));
  if (!self.registration.active) self.skipWaiting();
});
self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  const marker = "/model-files/";
  const markerIndex = url.pathname.indexOf(marker);
  if (markerIndex >= 0) {
    const relative = url.pathname.slice(markerIndex + marker.length);
    const [modelId, ...pathParts] = relative.split("/");
    const resolveIndex = pathParts.indexOf("main");
    const commit = MODEL_COMMITS[modelId];
    if (!commit || resolveIndex < 0) return;
    const filename = pathParts.slice(resolveIndex + 1).join("/");
    const directUrl =
      `https://hf-mirror.com/api/resolve-cache/models/mlc-ai/${modelId}/${commit}/${filename}`;
    event.respondWith(fetch(directUrl, { mode: "cors" }));
    return;
  }
  event.respondWith(fetch(event.request).then(response => {
    const copy = response.clone();
    caches.open(CACHE).then(cache => cache.put(event.request, copy));
    return response;
  }).catch(() => caches.match(event.request)));
});

self.addEventListener("message", event => {
  if (event.data?.type === "SKIP_WAITING") self.skipWaiting();
});
