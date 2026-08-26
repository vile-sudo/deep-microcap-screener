"use strict";

/* ================================================================
   Deep Microcap Screener — header quote
   Fills the faint watermark behind the header and the small attributed
   line under the title, and rotates through the set. Deliberately
   standalone: it must still work when the API is down and app.js has
   replaced the board with an error message.
   ================================================================ */

(function () {
  var QUOTES = [
    ["The person that turns over the most rocks wins the game.", "Peter Lynch"],
    ["Know what you own, and know why you own it.", "Peter Lynch"],
    ["Price is what you pay. Value is what you get.", "Warren Buffett"],
    ["Risk comes from not knowing what you are doing.", "Warren Buffett"],
    ["It is far better to buy a wonderful company at a fair price than a fair company at a wonderful price.", "Warren Buffett"],
    ["Time is the friend of the wonderful business, the enemy of the mediocre.", "Warren Buffett"],
    ["Wide diversification is only required when investors do not understand what they are doing.", "Warren Buffett"],
    ["The big money is not in the buying and the selling, but in the waiting.", "Charlie Munger"],
    ["Invert, always invert.", "Charlie Munger"],
    ["An investment operation is one which, upon thorough analysis, promises safety of principal and an adequate return.", "Benjamin Graham"],
    ["In the short run the market is a voting machine, but in the long run it is a weighing machine.", "Benjamin Graham"],
    ["The four most dangerous words in investing are: this time it's different.", "John Templeton"],
    ["In investing, what is comfortable is rarely profitable.", "Robert Arnott"]
  ];

  var bg    = document.getElementById("hqtext");
  var line  = document.getElementById("quoteline");
  var qtext = document.getElementById("qtext");
  var qauth = document.getElementById("qauth");
  if (!bg || !line || !qtext || !qauth) return;

  var bgWrap = bg.parentNode;
  var i = Math.floor(Math.random() * QUOTES.length);
  var timer = null;
  var reduce = false;
  try { reduce = matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) {}

  function paint() {
    var q = QUOTES[i];
    bg.textContent = q[0];
    qtext.textContent = "\u201c" + q[0] + "\u201d";
    qauth.textContent = "  \u2014 " + q[1];
    bgWrap.classList.add("in");
    line.classList.add("in");
  }

  function show(next) {
    if (reduce) { i = next; paint(); return; }
    bgWrap.classList.remove("in");
    line.classList.remove("in");
    setTimeout(function () { i = next; paint(); }, 480);
  }

  function step(delta) {
    show((i + delta + QUOTES.length) % QUOTES.length);
  }

  function schedule() {
    if (reduce) return;
    clearInterval(timer);
    timer = setInterval(function () {
      if (document.hidden) return;      /* do not burn through the set in a background tab */
      step(1);
    }, 15000);
  }

  line.addEventListener("click", function () { step(1); schedule(); });

  paint();
  schedule();
})();
