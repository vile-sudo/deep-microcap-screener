/* Service worker for Deep Sweep's desktop notifications ("Desktop notifications" in the India board's
   Alerts settings panel -- see app.js's webPushSubscribe/webPushUnsubscribe). Its only two jobs:
   show a notification when a push arrives, and focus/open the dashboard when it's clicked. It does not
   intercept fetches or cache anything -- there is no offline mode here, just push delivery. */

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

self.addEventListener("push", event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = {title: "Deep Sweep alerts", body: event.data ? event.data.text() : ""}; }
  const title = data.title || "Deep Sweep alerts";
  const options = {
    body: data.body || "",
    icon: "/static/favicon.ico",
    tag: data.tag || "deep-sweep-alert",   // a later push with the same tag replaces the shown one, rather than piling up
    data: {url: data.url || "/"},
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({type: "window", includeUncontrolled: true}).then(list => {
      for (const client of list) {
        if ("focus" in client) { client.focus(); if ("navigate" in client) client.navigate(url); return; }
      }
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});
