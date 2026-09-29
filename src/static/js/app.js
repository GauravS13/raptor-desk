// Raptor Desk client behaviour. Plain JavaScript, no eval, loaded from our own origin.
(function () {
  "use strict";

  // Send Django's CSRF token with every htmx request made from a cookie session.
  document.addEventListener("htmx:configRequest", function (event) {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (match) {
      event.detail.headers["X-CSRFToken"] = decodeURIComponent(match[1]);
    }
  });

  // Review form: number keys score the focused criterion, Ctrl+Enter submits,
  // and the time the page is visible is sent as active_seconds.
  document.addEventListener("DOMContentLoaded", function () {
    var form = document.querySelector("[data-review-form]");
    if (!form) {
      return;
    }
    var seconds = 0;
    var field = form.querySelector("[data-active-seconds]");
    window.setInterval(function () {
      if (document.visibilityState === "visible") {
        seconds += 1;
        field.value = String(seconds);
      }
    }, 1000);
    form.addEventListener("keydown", function (event) {
      if (event.ctrlKey && event.key === "Enter") {
        event.preventDefault();
        form.querySelector("[data-submit]").click();
        return;
      }
      if (!/^[0-9]$/.test(event.key) || event.target.tagName === "TEXTAREA") {
        return;
      }
      var group = event.target.closest("[data-criterion]");
      if (!group) {
        return;
      }
      var option = group.querySelector('input[type="radio"][value="' + event.key + '"]');
      if (option) {
        option.checked = true;
        option.focus();
        event.preventDefault();
      }
    });

    // Autosave: post the draft a few seconds after the last change, and on leaving a field.
    var url = form.getAttribute("data-autosave");
    var status = form.querySelector("[data-autosave-status]");
    var timer = null;
    var dirty = false;
    function save() {
      if (!url || !dirty) {
        return;
      }
      dirty = false;
      var body = new FormData(form);
      body.delete("submit");
      fetch(url, { method: "POST", body: body, credentials: "same-origin" })
        .then(function (response) { return response.json().then(function (data) { return [response.ok, data]; }); })
        .then(function (result) {
          if (status) {
            status.textContent = result[0] ? "Draft saved at " + result[1].at + "." : "Not saved: " + result[1].reason;
          }
        })
        .catch(function () {
          dirty = true;
          if (status) {
            status.textContent = "Not saved: connection lost. Your answers are still on this page.";
          }
        });
    }
    form.addEventListener("input", function () {
      dirty = true;
      window.clearTimeout(timer);
      timer = window.setTimeout(save, 4000);
    });
    form.addEventListener("focusout", function () {
      window.clearTimeout(timer);
      save();
    });
  });

  // Pairwise mode: arrow keys choose A, B or "too close".
  document.addEventListener("keydown", function (event) {
    var form = document.querySelector("[data-pairwise-form]");
    if (!form || event.target.tagName === "INPUT" || event.target.tagName === "TEXTAREA") {
      return;
    }
    var button = form.querySelector('[data-key="' + event.key + '"]');
    if (button) {
      event.preventDefault();
      button.click();
    }
  });
})();
