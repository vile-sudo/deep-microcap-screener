/* ================================================================
   Header clock -- the current date and time, ticking every second.

   Always India Standard Time, not the device clock: the board is an
   NSE/BSE board, so "11:40" has to mean 11:40 on the exchange whether
   the page is open in Mumbai or on a laptop still set to UTC.

   Deliberately its own file and not part of app.js: a clock should
   keep running even on a day when the board's data fails to load, and
   it needs nothing app.js builds.
   ================================================================ */
(function () {
  var box = document.getElementById('nav-clock');
  var dateEl = document.getElementById('nc-date');
  var timeEl = document.getElementById('nc-time');
  if (!box || !dateEl || !timeEl) return;

  var TZ = 'Asia/Kolkata';
  var fmtDate, fmtTime;
  try {
    fmtDate = new Intl.DateTimeFormat('en-GB', {
      timeZone: TZ, weekday: 'short', day: '2-digit', month: 'short', year: 'numeric'
    });
    /* h23 rather than hour12:false -- some engines render midnight as
       "24:00" for the latter */
    fmtTime = new Intl.DateTimeFormat('en-GB', {
      timeZone: TZ, hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
    });
  } catch (e) {
    return;   /* no IST tz data: stay hidden rather than show a local time as IST */
  }

  /* assembled from parts, so the order and separators are ours and do not
     shift with whatever locale data the browser ships */
  function parts(fmt, d) {
    var o = {};
    try { fmt.formatToParts(d).forEach(function (p) { o[p.type] = p.value; }); } catch (e) {}
    return o;
  }

  function render() {
    var now = new Date();
    var d = parts(fmtDate, now), t = parts(fmtTime, now);
    if (!t.hour) return;
    dateEl.textContent = d.weekday + ', ' + d.day + ' ' + d.month + ' ' + d.year;
    timeEl.textContent = t.hour + ':' + t.minute + ':' + t.second;
    box.hidden = false;
  }

  /* re-aimed at the next whole second every time instead of a flat
     setInterval(1000), which drifts and eventually shows the same second
     twice or skips one */
  function tick() {
    render();
    setTimeout(tick, 1000 - (Date.now() % 1000) + 5);
  }
  tick();

  /* background tabs get their timers throttled to minutes, so the pill can
     be well behind by the time it is looked at again */
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) render();
  });
})();
