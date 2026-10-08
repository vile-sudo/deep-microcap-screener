/* Browser Back / Forward for overlays (side panels, pop-up windows) on both dashboards.

   Opening an overlay adds a history entry for the same address, so the browser's Back button closes the
   overlay -- the way it behaves on most sites -- instead of leaving the page underneath. Closing it
   with its own × / Esc / a click outside steps that entry back off, so history never fills with
   entries that do nothing. When code closes an overlay itself (to open another view, say), its entry is
   marked spent and Back skips straight past it.

   Pages call:
     NavOverlay.open(name, close)   after showing an overlay; close() hides it (called on Back)
     NavOverlay.uiClose(name)       after the user closed it themselves
     NavOverlay.dropped(name)       after code closed it
     NavOverlay.pop(event)          first thing in the page's popstate handler; true = handled here
     NavOverlay.state()             the history.state to keep when the page replaces the address */
(function () {
  const stack = [];
  let ignoreNext = false;
  const idx = name => stack.map(s => s.name).lastIndexOf(name);
  window.NavOverlay = {
    open(name, close) {
      const i = idx(name);
      if (i >= 0 && i === stack.length - 1) { stack[i].close = close; return; }   // already the top overlay
      if (i >= 0) stack.splice(i, 1);
      stack.push({name, close});
      try { history.pushState(Object.assign({}, history.state || {}, {ovl: name, ovlDead: false}), "", location.href); } catch (e) {}
    },
    uiClose(name) {
      const i = idx(name);
      if (i < 0) return;
      const top = i === stack.length - 1;
      stack.splice(i, 1);
      if (top && history.state && history.state.ovl === name) {
        ignoreNext = true;
        history.back();
      } else {
        this._markDead();
      }
    },
    dropped(name) {
      const i = idx(name);
      if (i < 0) return;
      stack.splice(i, 1);
      if (history.state && history.state.ovl === name) this._markDead();
    },
    _markDead() {
      try { history.replaceState(Object.assign({}, history.state || {}, {ovlDead: true}), "", location.href); } catch (e) {}
    },
    pop(e) {
      if (ignoreNext) { ignoreNext = false; return true; }
      if (stack.length) {                       // Back with an overlay open: close it, stay on the page
        const top = stack.pop();
        try { top.close(); } catch (err) {}
        // landed on an entry code had already closed (a panel swapped for a pop-up): step past it too
        if (e && e.state && e.state.ovlDead && !stack.length) { ignoreNext = true; history.back(); }
        return true;
      }
      if (e && e.state && e.state.ovl && !e.state.ovlDead) return true;   // Forward onto a closed overlay: nothing to reopen
      if (e && e.state && e.state.ovlDead) { history.back(); return true; } // a spent entry: skip it
      return false;
    },
    state() { return history.state; },
    isOpen(name) { return idx(name) >= 0; },
  };
})();
