// MCP Apps view-side bridge (SEP-1865), shared by every Norm ui:// app.
//
// Injected into each app's HTML server-side (see ui_apps.py), so the delivered
// resource is still a single self-contained file — but the protocol lives in
// ONE place. If the handshake needs fixing, it gets fixed here for all apps.
//
// Exposes window.NormApp:
//   onResult(cb)      cb(params) when the host pushes a tool result
//   callTool(n, a)    -> Promise of a tool result (routed via the host)
//   openLink(url)     ask the host to open a URL
//   reportSize()      re-report our height (also automatic via ResizeObserver)
(function () {
  "use strict";

  var nextId = 1;
  var pending = {};
  var resultCb = null;
  var lastResult = null;

  function rpc(method, params) {
    var id = nextId++;
    window.parent.postMessage(
      { jsonrpc: "2.0", id: id, method: method, params: params || {} }, "*");
    return new Promise(function (resolve, reject) {
      pending[id] = { resolve: resolve, reject: reject };
    });
  }
  function notify(method, params) {
    window.parent.postMessage(
      { jsonrpc: "2.0", method: method, params: params || {} }, "*");
  }

  function applyTheme(hostContext) {
    if (hostContext && hostContext.theme) {
      document.documentElement.setAttribute("data-theme", hostContext.theme);
    }
  }

  function deliver(params) {
    lastResult = params;
    if (resultCb) { try { resultCb(params); } catch (e) { /* app error */ } }
    reportSize();
  }

  function reportSize() {
    var el = document.getElementById("root") || document.body;
    notify("ui/notifications/size-changed", {
      width: el.scrollWidth, height: el.scrollHeight,
    });
  }

  window.addEventListener("message", function (ev) {
    var m = ev.data;
    if (!m || m.jsonrpc !== "2.0") return;
    if (m.id != null && pending[m.id]) {
      var p = pending[m.id]; delete pending[m.id];
      if (m.error) p.reject(m.error); else p.resolve(m.result);
      return;
    }
    switch (m.method) {
      case "ui/notifications/tool-result": deliver(m.params || {}); break;
      case "ui/notifications/host-context-changed":
        if (m.params && m.params.hostContext) applyTheme(m.params.hostContext);
        break;
      case "ui/resource-teardown": break;
    }
  });

  // Handshake: initialize -> theme -> initialized. The host holds any tool
  // result until it sees `initialized`, so this order matters.
  rpc("ui/initialize", {
    protocolVersion: "2025-06-18",
    appInfo: { name: "Norm", version: "1.0.0" },
    appCapabilities: {},
  }).then(function (result) {
    if (result && result.hostContext) applyTheme(result.hostContext);
    notify("ui/notifications/initialized", {});
    // Some hosts carry the result on toolInfo rather than pushing it.
    var ti = result && result.hostContext && result.hostContext.toolInfo;
    if (ti && ti.result) deliver(ti.result);
  }).catch(function () { /* no host (e.g. opened directly) — stay idle */ });

  if (window.ResizeObserver) {
    var root = document.getElementById("root") || document.body;
    new ResizeObserver(reportSize).observe(root);
  }

  window.NormApp = {
    onResult: function (cb) {
      resultCb = cb;
      if (lastResult) cb(lastResult); // result may have landed first
    },
    callTool: function (name, args) {
      return rpc("tools/call", { name: name, arguments: args || {} });
    },
    openLink: function (url) { rpc("ui/open-link", { url: url }); },
    reportSize: reportSize,
    // Norm shapes oversized results into an envelope rather than the data.
    // Apps show this message instead of claiming they found nothing.
    truncationMessage: function (d) {
      if (d && typeof d === "object" && (d._too_large || d._truncated) && d.message) {
        return String(d.message);
      }
      return null;
    },
    // Unwrap the `_slimmed` envelope so apps still see their array.
    unwrap: function (d) {
      if (d && typeof d === "object" && d._slimmed && Array.isArray(d.data)) return d.data;
      return d;
    },
  };
})();
