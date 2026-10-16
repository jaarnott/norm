/*
 * Norm first-touch attribution. Paste once into the venue website's global
 * header (Squarespace: Settings > Advanced > Code Injection > Header):
 *
 *   <script src="https://bettercallnorm.com/embed/track.js" async></script>
 *
 * Keeps where a visitor first came from (Google click ids gclid / gbraid /
 * wbraid, utm_* tags, the referring site and the landing page) in this
 * site's localStorage for 90 days, so a Norm form they send days later, on
 * any page, can say which ad or campaign brought them (forms.js reads the
 * same key). It has to run on the landing page: the form page alone never
 * sees the ad's click id.
 *
 * First touch wins: later visits never overwrite it, with one exception: a
 * tagged visit (click id or utm tag) replaces an untagged first touch, so
 * "came from google.com" gives way to the ad that actually brought them.
 *
 * No cookies and no network calls; nothing leaves the browser until the
 * visitor sends a form. ES5, no dependencies, one global (NormTrack).
 */
(function (window, document) {
  'use strict';
  /* eslint-disable @typescript-eslint/no-unused-vars -- ES5 has no optional catch binding: catch (e) is required */

  if (window.NormTrack) return;

  var KEY = 'norm_attr_v1';
  var TTL = 90 * 24 * 60 * 60 * 1000;
  var TAGS = ['gclid', 'gbraid', 'wbraid', 'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'];

  function decode(s) {
    try {
      return decodeURIComponent(s.replace(/\+/g, ' '));
    } catch (e) {
      return s;
    }
  }

  /** "?a=1&b=2" -> {a: "1", b: "2"}; the first value of a repeated key wins. */
  function query(search) {
    var out = {};
    var parts = (search || '').replace(/^\?/, '').split('&');
    for (var i = 0; i < parts.length; i++) {
      if (!parts[i]) continue;
      var at = parts[i].indexOf('=');
      var key = decode(at < 0 ? parts[i] : parts[i].slice(0, at));
      if (key && !Object.prototype.hasOwnProperty.call(out, key)) {
        out[key] = at < 0 ? '' : decode(parts[i].slice(at + 1));
      }
    }
    return out;
  }

  function hostOf(url) {
    var m = /^[a-z][a-z0-9+.-]*:\/\/([^\/?#]+)/i.exec(url || '');
    return m ? m[1].toLowerCase() : '';
  }

  function isTagged(rec) {
    for (var i = 0; i < TAGS.length; i++) if (rec[TAGS[i]]) return true;
    return false;
  }

  function read(now) {
    try {
      var rec = JSON.parse(window.localStorage.getItem(KEY) || 'null');
      if (!rec || typeof rec !== 'object' || typeof rec.saved_at !== 'number') return null;
      if (now - rec.saved_at > TTL || rec.saved_at > now + 60000) return null;
      return rec;
    } catch (e) {
      return null; // storage blocked (private mode, sandbox) or corrupt
    }
  }

  function capture() {
    var now = new Date().getTime();
    var loc = window.location;
    var params = query(loc.search);
    var fresh = {};
    var tagged = false;
    for (var i = 0; i < TAGS.length; i++) {
      var v = params[TAGS[i]];
      if (v) {
        fresh[TAGS[i]] = String(v).slice(0, 500);
        tagged = true;
      }
    }
    var stored = read(now);
    if (stored && (isTagged(stored) || !tagged)) return stored;
    fresh.landing_page = String(loc.href).slice(0, 1000);
    var ref = document.referrer || '';
    // Only a referrer from another site says where the visitor came from.
    if (ref && hostOf(ref) && hostOf(ref) !== String(loc.host).toLowerCase()) {
      fresh.referrer = ref.slice(0, 1000);
    }
    fresh.saved_at = now;
    try {
      window.localStorage.setItem(KEY, JSON.stringify(fresh));
    } catch (e) {
      /* storage blocked: the form still reads the current page's tags */
    }
    return fresh;
  }

  /** What a form sends as `tracking`: the stored first touch, without its timestamp. */
  function get() {
    var rec = capture();
    var out = {};
    for (var k in rec) {
      if (Object.prototype.hasOwnProperty.call(rec, k) && k !== 'saved_at' && typeof rec[k] === 'string') out[k] = rec[k];
    }
    return out;
  }

  window.NormTrack = { get: get };
  capture();

  // Sites that change page without a reload (Squarespace's Ajax loading) can
  // land a tagged link mid-session: check again after each navigation.
  try {
    var wrap = function (name) {
      var original = window.history[name];
      if (typeof original !== 'function') return;
      window.history[name] = function () {
        var result = original.apply(this, arguments);
        try { capture(); } catch (e) { /* never break the site's navigation */ }
        return result;
      };
    };
    wrap('pushState');
    wrap('replaceState');
    window.addEventListener('popstate', function () {
      try { capture(); } catch (e) { /* ignore */ }
    });
  } catch (e) {
    /* history not patchable: first-load capture still stands */
  }
})(window, document);
