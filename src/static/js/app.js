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
})();
