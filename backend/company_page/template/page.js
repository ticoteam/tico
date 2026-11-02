(function () {
  var d = document;
  function reveal() {
    var target = location.hash && d.getElementById(location.hash.slice(1));
    if (!target) return;
    for (var e = target.querySelector("details") || target; e; e = e.parentElement) if (e.tagName === "DETAILS") e.open = true;
    target.scrollIntoView();
  }
  addEventListener("hashchange", reveal);
  reveal();

  var closed = [];
  addEventListener("beforeprint", function () {
    closed = [].filter.call(d.querySelectorAll("details"), function (e) { return !e.open; });
    closed.forEach(function (e) { e.open = true; });
  });
  addEventListener("afterprint", function () { closed.forEach(function (e) { e.open = false; }); });

  d.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest("a[data-older]");
    if (!link) return;
    event.preventDefault();
    var url = new URL(link.getAttribute("href"), location.href);
    if (url.origin !== location.origin) return;
    fetch(url, { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) throw r.status;
      return r.text();
    }).then(function (text) {
      var next = new DOMParser().parseFromString(text, "text/html");
      var list = d.getElementById("update-list");
      next.querySelectorAll("#update-list > article").forEach(function (a) {
        var open = a.querySelector("details[open]");
        if (open) open.open = false;
        list.appendChild(d.adoptNode(a));
      });
      var more = next.querySelector("a[data-older]");
      if (more) link.setAttribute("href", more.getAttribute("href"));
      else link.parentElement.remove();
    }).catch(function () { location.href = url; });
  });
})();
