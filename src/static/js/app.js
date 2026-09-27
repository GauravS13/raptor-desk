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
  });
})();
