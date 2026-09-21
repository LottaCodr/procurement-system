/* Taraba State Procurement Transparency Portal — progressive enhancement only.
 *
 * Rules for this file, in order of importance:
 *   1. Nothing here is required to read, search, filter, download or print the
 *      register. Every page must work with this file blocked, because a
 *      significant share of bidders browse on Opera Mini, on 2G, with
 *      JavaScript disabled for data saving.
 *   2. No network calls. This is a read-only transparency surface; client-side
 *      fetching would add failure modes, not features.
 *   3. No inline script anywhere, so the Content-Security-Policy can stay at
 *      `script-src 'self'` with no unsafe-inline escape hatch.
 *
 * What it adds when it does load: a copy button for hashes and shell snippets
 * (hashes are the one thing users must transcribe exactly), and a swipe hint on
 * tables wider than their container.
 */
(function () {
  "use strict";

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    // Fallback for plain-HTTP intranet deployments and older mobile browsers.
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (err) {
      ok = false;
    }
    document.body.removeChild(area);
    return ok ? Promise.resolve() : Promise.reject(new Error("copy failed"));
  }

  function announce(message) {
    var live = document.getElementById("js-status");
    if (!live) {
      live = document.createElement("p");
      live.id = "js-status";
      live.className = "visually-hidden";
      live.setAttribute("role", "status");
      live.setAttribute("aria-live", "polite");
      document.body.appendChild(live);
    }
    live.textContent = message;
  }

  function wireCopyButtons() {
    var buttons = document.querySelectorAll("[data-copy-target]");
    Array.prototype.forEach.call(buttons, function (button) {
      // Hide until proven useful: a copy control that does nothing is worse
      // than no control at all.
      button.hidden = false;
      button.addEventListener("click", function () {
        var source = document.querySelector(button.getAttribute("data-copy-target"));
        if (!source) return;
        var label = button.getAttribute("data-copy-label") || "value";
        copyText(source.textContent.trim()).then(
          function () {
            button.textContent = "Copied";
            announce(label + " copied to the clipboard");
          },
          function () {
            button.textContent = "Press Ctrl+C";
            announce("Copy failed — select the text and copy it manually");
          }
        );
      });
    });
  }

  function markScrollableTables() {
    var wraps = document.querySelectorAll(".table-wrap");
    Array.prototype.forEach.call(wraps, function (wrap) {
      if (wrap.scrollWidth - wrap.clientWidth > 8) {
        wrap.setAttribute("data-scrollable", "true");
        var hint = wrap.parentNode.querySelector("[data-scroll-hint]");
        if (hint) hint.hidden = false;
      }
    });
  }

  function init() {
    try {
      wireCopyButtons();
      markScrollableTables();
    } catch (err) {
      /* Enhancement failure must never break the page. */
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
