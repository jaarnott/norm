/*
 * Norm web form embed.
 *
 *   <div data-norm-form="your-form-slug"></div>
 *   <script src="https://bettercallnorm.com/embed/forms.js" async></script>
 *
 * Renders the form inline, so it takes the page's typeface unless the form's
 * theme sets one. Add data-norm-form-mode="iframe" to load the hosted form
 * (/f/<slug>) in a frame instead, sized to fit, where the site's CSS can't
 * reach it. The hosted page uses this same file, so both behave alike.
 *
 * Talking to Norm: the schema is a plain GET and the submit a POST with a
 * text/plain body (JSON inside), no credentials and no custom headers. Both
 * are CORS "simple" requests, so a venue site on any domain needs no
 * preflight. The schema's form_token lets Norm time the fill: a submit less
 * than 2 seconds after the schema is treated as a script, so a person whose
 * browser autofilled everything waits out the remainder here instead of
 * losing their enquiry.
 *
 * Attribution: sends the first touch track.js stored on this site (utm_*,
 * Google click ids, referrer, landing page); without track.js, this page's
 * own tags.
 *
 * ES5, no dependencies, one global (NormForms). Styles are prefixed .nf and
 * themed through CSS custom properties set on the form's own element. Theme
 * values are never written into a stylesheet.
 */
(function (window, document) {
  'use strict';
  /* eslint-disable @typescript-eslint/no-unused-vars -- ES5 has no optional catch binding: catch (e) is required */

  if (window.NormForms) {
    if (typeof window.NormForms.scan === 'function') window.NormForms.scan();
    return;
  }

  var VERSION = '1';
  var TRACK_KEY = 'norm_attr_v1';
  var TRACK_TTL = 90 * 24 * 60 * 60 * 1000;
  var TAGS = ['gclid', 'gbraid', 'wbraid', 'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'];
  // Norm counts a submit within 2s of the schema as a script (forms.MIN_FILL).
  var MIN_FILL_MS = 2500;
  // The form_token lasts 48h (forms.FORM_TOKEN_TTL); refresh it a little early.
  var TOKEN_REFRESH_MS = 47 * 60 * 60 * 1000;
  // Norm's answer caps (forms._MAX_CHARS), checked before any format check.
  var LIMITS = { name: 200, email: 254, phone: 40, text: 500, long_text: 5000, date: 10, time: 8 };
  var NAME_PART = 100;
  var EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
  var TIME = /^(\d{2}):(\d{2})(:(\d{2}))?$/;
  var MAX_DATES = 20;
  var RADIO_MAX = 6;
  var STYLE_ID = 'norm-forms-style';
  var hasOwn = Object.prototype.hasOwnProperty;
  var uid = 0;

  // ---------------------------------------------------------------------
  // Where Norm is: the origin this script was loaded from.
  // ---------------------------------------------------------------------

  function originOf(url) {
    var m = /^(https?:\/\/[^\/?#]+)/i.exec(url || '');
    return m ? m[1] : '';
  }

  function scriptSrc() {
    if (document.currentScript && document.currentScript.src) return document.currentScript.src;
    var scripts = document.getElementsByTagName('script');
    for (var i = scripts.length - 1; i >= 0; i--) {
      if (/\/embed\/forms\.js(\?|#|$)/.test(scripts[i].src || '')) return scripts[i].src;
    }
    return '';
  }

  var ORIGIN = originOf(scriptSrc()) || originOf(window.location && window.location.href);

  // ---------------------------------------------------------------------
  // Small helpers
  // ---------------------------------------------------------------------

  function trim(v) {
    return v === null || v === undefined ? '' : String(v).replace(/^\s+|\s+$/g, '');
  }

  function keys(obj) {
    var out = [];
    for (var k in obj) if (hasOwn.call(obj, k)) out.push(k);
    return out;
  }

  function isObject(v) {
    return v !== null && typeof v === 'object' && !Array.isArray(v);
  }

  function indexOf(list, item) {
    for (var i = 0; i < list.length; i++) if (list[i] === item) return i;
    return -1;
  }

  function optValue(o) {
    if (isObject(o)) return String(o.value !== undefined && o.value !== null ? o.value : (o.label !== undefined && o.label !== null ? o.label : ''));
    return String(o);
  }

  function optLabel(o) {
    if (isObject(o) && o.label !== undefined && o.label !== null) return String(o.label);
    return optValue(o);
  }

  function optionValues(f) {
    var out = [];
    var opts = Array.isArray(f.options) ? f.options : [];
    for (var i = 0; i < opts.length; i++) out.push(optValue(opts[i]));
    return out;
  }

  function isDate(v) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v);
    if (!m) return false;
    var y = +m[1], mo = +m[2], d = +m[3];
    var dt = new Date(Date.UTC(y, mo - 1, d));
    return dt.getUTCFullYear() === y && dt.getUTCMonth() === mo - 1 && dt.getUTCDate() === d;
  }

  function isTime(v) {
    var m = TIME.exec(v);
    return !!m && +m[1] < 24 && +m[2] < 60 && (!m[4] || +m[4] < 60);
  }

  function todayLocal() {
    var d = new Date();
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }

  // ---------------------------------------------------------------------
  // Answers: what is sent, and the checks Norm makes (forms.validate_answers)
  // ---------------------------------------------------------------------

  /**
   * One field's answer as it is sent, or undefined when blank (blank answers
   * are left out, which Norm reads as "not answered"). `raw` is what the
   * inputs hold: strings, {first, last}, {start, end}, a list of strings, or
   * a boolean for a lone tick box.
   */
  function answer(f, raw) {
    switch (f.type) {
      case 'name': {
        var first = trim(isObject(raw) ? raw.first : raw);
        var last = trim(isObject(raw) ? raw.last : '');
        return first || last ? { first: first, last: last } : undefined;
      }
      case 'number': {
        var s = trim(raw);
        return s ? Number(s) : undefined;
      }
      case 'dates': {
        var list = Array.isArray(raw) ? raw : [raw];
        var out = [];
        for (var i = 0; i < list.length; i++) {
          var d = trim(list[i]);
          if (d && indexOf(out, d) < 0) out.push(d);
        }
        return out.length ? out : undefined;
      }
      case 'time_range': {
        var start = trim(isObject(raw) ? raw.start : '');
        var end = trim(isObject(raw) ? (raw.end !== undefined ? raw.end : raw.finish) : '');
        return start || end ? { start: start, end: end } : undefined;
      }
      case 'checkbox': {
        if (!optionValues(f).length) return raw === true;
        var picked = Array.isArray(raw) ? raw : [];
        var vals = [];
        for (var j = 0; j < picked.length; j++) if (indexOf(vals, String(picked[j])) < 0) vals.push(String(picked[j]));
        return vals.length ? vals : undefined;
      }
      default: {
        var text = trim(raw);
        return text ? text : undefined;
      }
    }
  }

  /** What's wrong with one answer, in Norm's own words, or null. */
  function problem(f, raw) {
    var v = answer(f, raw);
    var label = f.label || f.key;
    if (v === undefined) return f.required ? label + ' is required.' : null;
    function bad(text) { return label + ' ' + text + '.'; }
    switch (f.type) {
      case 'name':
        if (v.first.length > NAME_PART || v.last.length > NAME_PART) return bad('is too long');
        return trim(v.first + ' ' + v.last).length > LIMITS.name ? bad('is too long') : null;
      case 'text':
      case 'long_text':
        return v.length > LIMITS[f.type] ? bad('is too long') : null;
      case 'phone':
        if (v.length > LIMITS.phone) return bad('is too long');
        return /\d/.test(v) ? null : bad('is not a phone number');
      case 'email':
        if (v.length > LIMITS.email) return bad('is too long');
        return EMAIL.test(v.toLowerCase()) ? null : bad('is not an email address');
      case 'number': {
        if (!isFinite(v) || Math.abs(v) > 1e9) return bad('must be a number');
        var limits = isObject(f.options) ? f.options : {};
        if (limits.min !== undefined && limits.min !== null && limits.min !== '' && v < parseFloat(limits.min)) return bad('is too small');
        if (limits.max !== undefined && limits.max !== null && limits.max !== '' && v > parseFloat(limits.max)) return bad('is too large');
        return null;
      }
      case 'date':
        if (v.length > LIMITS.date) return bad('is too long');
        return isDate(v) ? null : bad('needs a date like 2026-12-04');
      case 'dates': {
        if (v.length > MAX_DATES) return bad('has too many dates');
        for (var i = 0; i < v.length; i++) {
          if (v[i].length > LIMITS.date) return bad('is too long');
          if (!isDate(v[i])) return bad('needs a date like 2026-12-04');
        }
        return null;
      }
      case 'time_range':
        if (v.start.length > LIMITS.time || v.end.length > LIMITS.time) return bad('is too long');
        if ((v.start && !isTime(v.start)) || (v.end && !isTime(v.end))) return bad('needs times like 19:30');
        return f.required && !(v.start && v.end) ? bad('needs a start and an end time') : null;
      case 'choice': {
        var allowed = optionValues(f);
        return allowed.length && indexOf(allowed, v) < 0 ? bad('is not one of the choices') : null;
      }
      case 'checkbox': {
        if (v === true || v === false) return f.required && !v ? bad('must be ticked') : null;
        var opts = optionValues(f);
        for (var j = 0; j < v.length; j++) if (indexOf(opts, v[j]) < 0) return bad('is not one of the choices');
        return null;
      }
      default:
        return null;
    }
  }

  /** {field key: message} for every answer Norm would refuse. */
  function validate(fields, values) {
    var errors = {};
    for (var i = 0; i < (fields || []).length; i++) {
      var f = fields[i];
      if (f.type === 'section') continue;
      var msg = problem(f, values ? values[f.key] : undefined);
      if (msg) errors[f.key] = msg;
    }
    return errors;
  }

  /** The submit's `payload`: answered fields only, plus the honeypot. */
  function serialise(fields, values, honeypot, honeypotValue) {
    var payload = {};
    for (var i = 0; i < (fields || []).length; i++) {
      var f = fields[i];
      if (f.type === 'section') continue;
      var v = answer(f, values ? values[f.key] : undefined);
      if (v !== undefined) payload[f.key] = v;
    }
    if (honeypot) payload[honeypot] = honeypotValue ? String(honeypotValue) : '';
    return payload;
  }

  // ---------------------------------------------------------------------
  // Attribution (the same rules as track.js, which shares the key)
  // ---------------------------------------------------------------------

  function decode(s) {
    try { return decodeURIComponent(s.replace(/\+/g, ' ')); } catch (e) { return s; }
  }

  function query(search) {
    var out = {};
    var parts = (search || '').replace(/^\?/, '').split('&');
    for (var i = 0; i < parts.length; i++) {
      if (!parts[i]) continue;
      var at = parts[i].indexOf('=');
      var key = decode(at < 0 ? parts[i] : parts[i].slice(0, at));
      if (key && !hasOwn.call(out, key)) out[key] = at < 0 ? '' : decode(parts[i].slice(at + 1));
    }
    return out;
  }

  function hostOf(url) {
    var m = /^[a-z][a-z0-9+.-]*:\/\/([^\/?#]+)/i.exec(url || '');
    return m ? m[1].toLowerCase() : '';
  }

  function readTrack(now) {
    try {
      var rec = JSON.parse(window.localStorage.getItem(TRACK_KEY) || 'null');
      if (!rec || typeof rec !== 'object' || typeof rec.saved_at !== 'number') return null;
      if (now - rec.saved_at > TRACK_TTL || rec.saved_at > now + 60000) return null;
      return rec;
    } catch (e) {
      return null;
    }
  }

  function tagged(rec) {
    for (var i = 0; i < TAGS.length; i++) if (rec[TAGS[i]]) return true;
    return false;
  }

  /**
   * The visitor's first touch: what track.js stored (or this page's tags,
   * stored the same way), as the submit's `tracking` object.
   */
  function attribution() {
    var now = new Date().getTime();
    var loc = window.location;
    var params = query(loc.search);
    var fresh = {};
    var isTagged = false;
    for (var i = 0; i < TAGS.length; i++) {
      var v = params[TAGS[i]];
      if (v) {
        fresh[TAGS[i]] = String(v).slice(0, 500);
        isTagged = true;
      }
    }
    var rec = readTrack(now);
    if (!rec || (isTagged && !tagged(rec))) {
      fresh.landing_page = String(loc.href).slice(0, 1000);
      var ref = document.referrer || '';
      if (ref && hostOf(ref) && hostOf(ref) !== String(loc.host).toLowerCase()) fresh.referrer = ref.slice(0, 1000);
      fresh.saved_at = now;
      try { window.localStorage.setItem(TRACK_KEY, JSON.stringify(fresh)); } catch (e) { /* storage blocked */ }
      rec = fresh;
    }
    var out = {};
    for (var k in rec) if (hasOwn.call(rec, k) && k !== 'saved_at' && typeof rec[k] === 'string' && rec[k]) out[k] = rec[k];
    return out;
  }

  // ---------------------------------------------------------------------
  // Theme: form.theme -> CSS custom properties on the form element
  // ---------------------------------------------------------------------

  // key: [custom properties, a property to validate the value with, unit
  // for bare numbers]. Keys arrive as Norm's snake_case or as Orbit's
  // camelCase style profile (imported forms); both normalise to snake_case.
  // The first block are shorthands, applied before the specific keys so a
  // specific key wins.
  var THEME_ALIASES = {
    primary: [['--nf-button-bg', '--nf-accent'], 'color'],
    brand_color: [['--nf-button-bg', '--nf-accent'], 'color'],
    accent: [['--nf-accent'], 'color'],
    text: [['--nf-fg'], 'color'],
    font: [['--nf-font'], 'font-family'],
    radius: [['--nf-input-radius', '--nf-button-radius'], 'border-radius', 'px']
  };
  var THEME_KEYS = {
    background: [['--nf-bg'], 'background-color'],
    foreground: [['--nf-fg'], 'color'],
    muted_foreground: [['--nf-muted'], 'color'],
    heading_font: [['--nf-heading-font'], 'font-family'],
    heading_color: [['--nf-heading-color'], 'color'],
    heading_size: [['--nf-heading-size'], 'font-size', 'px'],
    heading_weight: [['--nf-heading-weight'], 'font-weight'],
    heading_transform: [['--nf-heading-transform'], 'text-transform'],
    body_font: [['--nf-font'], 'font-family'],
    body_size: [['--nf-size'], 'font-size', 'px'],
    label_color: [['--nf-label-color'], 'color'],
    label_size: [['--nf-label-size'], 'font-size', 'px'],
    label_weight: [['--nf-label-weight'], 'font-weight'],
    label_transform: [['--nf-label-transform'], 'text-transform'],
    input_background: [['--nf-input-bg'], 'background-color'],
    input_border_color: [['--nf-input-border'], 'border-color'],
    input_border_width: [['--nf-input-border-width'], 'border-width', 'px'],
    input_radius: [['--nf-input-radius'], 'border-radius', 'px'],
    textarea_radius: [['--nf-textarea-radius'], 'border-radius', 'px'],
    input_text_color: [['--nf-input-color'], 'color'],
    input_placeholder_color: [['--nf-placeholder'], 'color'],
    input_focus_color: [['--nf-accent'], 'color'],
    input_padding_x: [['--nf-input-px'], 'padding-left', 'px'],
    input_padding_y: [['--nf-input-py'], 'padding-top', 'px'],
    checkbox_size: [['--nf-check-size'], 'width', 'px'],
    button_background: [['--nf-button-bg'], 'background-color'],
    button_text_color: [['--nf-button-color'], 'color'],
    button_border_color: [['--nf-button-border'], 'border-color'],
    button_border_width: [['--nf-button-border-width'], 'border-width', 'px'],
    button_radius: [['--nf-button-radius'], 'border-radius', 'px'],
    button_transform: [['--nf-button-transform'], 'text-transform'],
    button_weight: [['--nf-button-weight'], 'font-weight'],
    button_font: [['--nf-button-font'], 'font-family'],
    button_padding_x: [['--nf-button-px'], 'padding-left', 'px'],
    button_padding_y: [['--nf-button-py'], 'padding-top', 'px'],
    section_title_font: [['--nf-section-font'], 'font-family'],
    section_title_color: [['--nf-section-color'], 'color'],
    section_title_size: [['--nf-section-size'], 'font-size', 'px'],
    section_title_weight: [['--nf-section-weight'], 'font-weight'],
    section_title_spacing: [['--nf-section-spacing'], 'letter-spacing', 'em'],
    section_title_margin_top: [['--nf-section-mt'], 'margin-top', 'px'],
    section_title_margin_bottom: [['--nf-section-mb'], 'margin-bottom', 'px'],
    max_width: [['--nf-max-width'], 'max-width', 'px'],
    field_gap: [['--nf-gap'], 'margin-top', 'px']
  };

  function snake(key) {
    return String(key).replace(/([a-z0-9])([A-Z])/g, '$1_$2').toLowerCase();
  }

  var GENERIC_FONT = /(^|,)\s*(serif|sans-serif|monospace|cursive|fantasy|system-ui|ui-sans-serif|ui-serif|inherit)\s*$/i;

  function cssValue(raw, unit, prop) {
    if (typeof raw === 'number') return isFinite(raw) ? (unit ? raw + unit : String(raw)) : null;
    if (typeof raw !== 'string') return null;
    var v = trim(raw);
    // Plain values only: nothing that could end a declaration or a rule.
    if (!v || v.length > 200 || /[;{}<>\\]/.test(v)) return null;
    if (unit && /^-?\d+(\.\d+)?$/.test(v)) return v + unit;
    // A brand font the page doesn't have would fall back to the browser's
    // serif: end the list on a plain sans instead.
    if (prop === 'font-family' && !GENERIC_FONT.test(v)) return v + ', sans-serif';
    return v;
  }

  /** [[custom property, value, property to check it with], ...] for a theme. */
  function themeVars(theme) {
    var out = [];
    if (!isObject(theme)) return out;
    var byKey = {};
    var ks = keys(theme);
    for (var i = 0; i < ks.length; i++) byKey[snake(ks[i])] = theme[ks[i]];
    var passes = [THEME_ALIASES, THEME_KEYS];
    for (var p = 0; p < passes.length; p++) {
      for (var key in passes[p]) {
        if (!hasOwn.call(passes[p], key) || !hasOwn.call(byKey, key)) continue;
        var spec = passes[p][key];
        var value = cssValue(byKey[key], spec[2], spec[1]);
        if (value === null) continue;
        for (var j = 0; j < spec[0].length; j++) out.push([spec[0][j], value, spec[1]]);
      }
    }
    return out;
  }

  function supports(prop, value) {
    try {
      return !(window.CSS && typeof window.CSS.supports === 'function') || window.CSS.supports(prop, value);
    } catch (e) {
      return false;
    }
  }

  function applyTheme(el, theme) {
    var vars = themeVars(theme);
    var filled = false;
    for (var i = 0; i < vars.length; i++) {
      if (!supports(vars[i][2], vars[i][1])) continue;
      el.style.setProperty(vars[i][0], vars[i][1]);
      if (vars[i][0] === '--nf-bg' && !/^(transparent|none|rgba\(.*,\s*0\)|hsla\(.*,\s*0\))$/i.test(vars[i][1])) filled = true;
    }
    if (filled) addClass(el, 'nf--filled');
  }

  // ---------------------------------------------------------------------
  // Styles (one sheet, every rule under .nf; values come from custom
  // properties with plain defaults)
  // ---------------------------------------------------------------------

  var CSS_TEXT = [
    '.nf{box-sizing:border-box;max-width:var(--nf-max-width,640px);color:var(--nf-fg,inherit);font-family:var(--nf-font,inherit);font-size:var(--nf-size,inherit);line-height:1.5;text-align:left;background:var(--nf-bg,transparent)}',
    '.nf--filled{padding:24px 20px;border-radius:12px}',
    '.nf *,.nf *::before,.nf *::after{box-sizing:border-box}',
    '.nf [hidden]{display:none!important}',
    '.nf-title{margin:0 0 8px;padding:0;font-family:var(--nf-heading-font,inherit);font-size:var(--nf-heading-size,1.5em);font-weight:var(--nf-heading-weight,700);color:var(--nf-heading-color,inherit);text-transform:var(--nf-heading-transform,none);line-height:1.2;letter-spacing:normal;border:0;background:none;box-shadow:none}',
    '.nf-intro{margin:0 0 20px;color:var(--nf-muted,inherit)}',
    '.nf-intro p{margin:0 0 .6em}.nf-intro a{color:inherit;text-decoration:underline}',
    '.nf-form{margin:0;padding:0}',
    '.nf .nf-fields>*+*{margin-top:var(--nf-gap,18px)}',
    '.nf-field,.nf-group{margin:0;padding:0;border:0;min-width:0}',
    '.nf-group>legend{float:left;width:100%;margin:0 0 6px;padding:0}',
    '.nf-group>legend+*{clear:both}',
    '.nf-label{display:block;margin:0 0 6px;padding:0;font-family:inherit;font-size:var(--nf-label-size,.95em);font-weight:var(--nf-label-weight,600);color:var(--nf-label-color,inherit);text-transform:var(--nf-label-transform,none);line-height:1.35;letter-spacing:normal}',
    '.nf-req{font-weight:400;opacity:.75}',
    '.nf-help{margin:-2px 0 8px;padding:0;font-size:.875em;color:var(--nf-muted,inherit);opacity:.85;line-height:1.4;text-transform:none;letter-spacing:normal}',
    '.nf-input{display:block;width:100%;max-width:100%;min-height:46px;margin:0;padding:var(--nf-input-py,10px) var(--nf-input-px,12px);border:var(--nf-input-border-width,1px) solid var(--nf-input-border,#8a8178);border-radius:var(--nf-input-radius,8px);background:var(--nf-input-bg,#fff);color:var(--nf-input-color,#1a1a1a);font-family:inherit;font-size:16px;font-weight:400;line-height:1.4;text-transform:none;letter-spacing:normal;box-shadow:none;-webkit-appearance:none;appearance:none;height:auto}',
    'textarea.nf-input{min-height:112px;resize:vertical;border-radius:var(--nf-textarea-radius,var(--nf-input-radius,8px))}',
    'select.nf-input{padding-right:40px;cursor:pointer;background-image:url("data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' width=\'16\' height=\'16\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%23777\' stroke-width=\'2\' stroke-linecap=\'round\' stroke-linejoin=\'round\'%3E%3Cpath d=\'m6 9 6 6 6-6\'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 14px center}',
    '.nf-input::placeholder{color:var(--nf-placeholder,#6f6f6f);opacity:1}',
    '.nf-input:focus{outline:2px solid var(--nf-accent,#1a1a1a);outline-offset:1px}',
    '.nf-input[aria-invalid="true"]{border-color:#a93a2a}',
    '.nf-pair{display:flex;flex-wrap:wrap;gap:12px}',
    '.nf-pair>div{flex:1 1 150px;min-width:0}',
    '.nf-caption{display:block;margin:0 0 4px;font-size:.875em;font-weight:400;color:var(--nf-muted,inherit);text-transform:none;letter-spacing:normal}',
    '.nf .nf-options>*+*{margin-top:8px}',
    '.nf-option{display:flex;align-items:flex-start;gap:10px;margin:0;font-weight:400;line-height:1.4;cursor:pointer;text-transform:none;letter-spacing:normal}',
    '.nf-option input{flex:0 0 auto;width:var(--nf-check-size,18px);height:var(--nf-check-size,18px);margin:2px 0 0;accent-color:var(--nf-accent,#1a1a1a);cursor:pointer}',
    '.nf-option input:focus-visible{outline:2px solid var(--nf-accent,#1a1a1a);outline-offset:2px}',
    '.nf .nf-dates>*+*{margin-top:8px}',
    '.nf-date-row{display:flex;gap:8px;align-items:center}',
    '.nf-date-row .nf-input{flex:1 1 auto;min-width:0}',
    '.nf-linkbtn{display:inline-flex;align-items:center;min-height:40px;margin:0;padding:0 14px;border:1px dashed currentColor;border-radius:var(--nf-input-radius,8px);background:transparent;color:inherit;font:inherit;font-size:.9em;cursor:pointer;opacity:.85;text-transform:none;letter-spacing:normal;box-shadow:none}',
    '.nf-linkbtn:hover{opacity:1}',
    '.nf-remove{flex:0 0 auto;min-width:44px;min-height:44px;margin:0;padding:0 10px;border:1px solid transparent;border-radius:8px;background:transparent;color:inherit;font:inherit;font-size:.875em;text-decoration:underline;cursor:pointer;box-shadow:none}',
    '.nf-error{margin:6px 0 0;padding:0;font-size:.875em;font-weight:500;color:#a93a2a;line-height:1.4;text-transform:none;letter-spacing:normal}',
    '.nf-alert{margin:0 0 18px;padding:12px 14px;border-radius:8px;background:#fbe9e5;color:#8a2c1f;font-size:.95em;line-height:1.45;text-transform:none;letter-spacing:normal}',
    '.nf-alert ul{margin:6px 0 0;padding-left:20px}',
    '.nf .nf-fields>.nf-section{margin-top:calc(var(--nf-gap,18px) * 1.5);padding-top:var(--nf-gap,18px);border-top:1px solid rgba(127,127,127,.3)}',
    '.nf .nf-fields>.nf-section:first-child{margin-top:0;padding-top:0;border-top:0}',
    '.nf-section-title{margin:var(--nf-section-mt,0) 0 var(--nf-section-mb,4px);padding:0 0 8px;font-family:var(--nf-section-font,var(--nf-heading-font,inherit));font-size:var(--nf-section-size,1.15em);font-weight:var(--nf-section-weight,700);color:var(--nf-section-color,var(--nf-heading-color,inherit));letter-spacing:var(--nf-section-spacing,normal);line-height:1.25;text-transform:none;border:0;background:none;box-shadow:none}',
    '.nf-actions{margin-top:22px}',
    '.nf-submit{display:inline-flex;align-items:center;justify-content:center;min-height:48px;margin:0;padding:var(--nf-button-py,12px) var(--nf-button-px,26px);border:var(--nf-button-border-width,0) solid var(--nf-button-border,transparent);border-radius:var(--nf-button-radius,8px);background:var(--nf-button-bg,#1a1a1a);color:var(--nf-button-color,#fff);font-family:var(--nf-button-font,inherit);font-size:1em;font-weight:var(--nf-button-weight,600);text-transform:var(--nf-button-transform,none);line-height:1.2;letter-spacing:normal;cursor:pointer;box-shadow:none;-webkit-appearance:none;appearance:none}',
    '.nf-submit:hover{opacity:.92}',
    '.nf-submit:focus-visible{outline:2px solid var(--nf-accent,#1a1a1a);outline-offset:3px}',
    '.nf-submit[disabled]{opacity:.6;cursor:progress}',
    '.nf-success{padding:18px 20px;border-left:4px solid var(--nf-accent,#1a1a1a);border-radius:8px;background:rgba(127,127,127,.08);line-height:1.5}',
    '.nf-success:focus{outline:none}',
    '.nf-success p{margin:0}.nf-success p+p{margin-top:.5em}',
    '.nf-status{margin:0;padding:12px 0;color:var(--nf-muted,inherit)}',
    '.nf-hp{position:absolute!important;left:-10000px!important;top:auto!important;width:1px!important;height:1px!important;overflow:hidden!important}'
  ].join('\n');

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var style = document.createElement('style');
    style.id = STYLE_ID;
    style.appendChild(document.createTextNode(CSS_TEXT));
    (document.head || document.getElementsByTagName('head')[0] || document.documentElement).appendChild(style);
  }

  // ---------------------------------------------------------------------
  // DOM
  // ---------------------------------------------------------------------

  function addClass(el, name) {
    if ((' ' + el.className + ' ').indexOf(' ' + name + ' ') < 0) el.className = trim(el.className + ' ' + name);
  }

  function h(tag, attrs, children) {
    var el = document.createElement(tag);
    if (attrs) {
      for (var k in attrs) {
        if (!hasOwn.call(attrs, k) || attrs[k] === null || attrs[k] === undefined || attrs[k] === false) continue;
        if (k === 'text') el.appendChild(document.createTextNode(String(attrs[k])));
        else if (k === 'className') el.className = attrs[k];
        else el.setAttribute(k, attrs[k] === true ? '' : String(attrs[k]));
      }
    }
    for (var i = 0; children && i < children.length; i++) {
      var c = children[i];
      if (c === null || c === undefined || c === false) continue;
      el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    }
    return el;
  }

  function clear(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function fire(el, type, detail) {
    var ev;
    try {
      ev = new window.CustomEvent(type, { bubbles: true, detail: detail });
    } catch (e) {
      ev = document.createEvent('CustomEvent');
      ev.initCustomEvent(type, true, false, detail);
    }
    el.dispatchEvent(ev);
  }

  /** A section's placeholder label (the importer fills blank labels with the key). */
  function sectionTitle(f) {
    var label = trim(f.label);
    if (!label || (label === f.key && /^[a-z][a-z0-9_]*$/.test(label) && label.indexOf('_') >= 0)) return '';
    return label;
  }

  function autocomplete(f) {
    if (f.type === 'email') return 'email';
    if (f.type === 'phone') return 'tel';
    if (/company|organisation|organization|business/.test(f.key)) return 'organization';
    return null;
  }

  function required(f) {
    return f.required ? h('span', { className: 'nf-req', 'aria-hidden': 'true', text: ' *' }) : null;
  }

  // Each builder returns {node, controls, read(), focus}: the DOM holds the
  // answers, read once at submit.
  function textControl(f, id, describedBy) {
    var multi = f.type === 'long_text';
    var type = { email: 'email', phone: 'tel', number: 'number', date: 'date' }[f.type] || 'text';
    var input = h(multi ? 'textarea' : 'input', {
      id: id,
      name: f.key,
      className: 'nf-input',
      type: multi ? null : type,
      rows: multi ? 4 : null,
      placeholder: f.placeholder || null,
      autocomplete: autocomplete(f),
      inputmode: f.type === 'number' ? 'decimal' : null,
      step: f.type === 'number' ? 'any' : null,
      min: f.type === 'number' && isObject(f.options) && f.options.min !== undefined && f.options.min !== null ? f.options.min : (f.type === 'date' ? todayLocal() : null),
      max: f.type === 'number' && isObject(f.options) && f.options.max !== undefined && f.options.max !== null ? f.options.max : null,
      maxlength: LIMITS[f.type] || null,
      spellcheck: f.type === 'email' ? 'false' : null,
      autocapitalize: f.type === 'email' ? 'off' : null,
      required: f.required,
      'aria-describedby': describedBy
    });
    return {
      node: h('div', { className: 'nf-field' }, [h('label', { className: 'nf-label', 'for': id }, [f.label || f.key, required(f)]), helpNode(f, id), input]),
      controls: [input],
      read: function () { return input.value; },
      focus: input
    };
  }

  function helpNode(f, id) {
    return f.help_text ? h('p', { className: 'nf-help', id: id + '-help', text: f.help_text }) : null;
  }

  function groupShell(f, id, body) {
    return h('fieldset', { className: 'nf-group', id: id + '-group' }, [
      h('legend', { className: 'nf-label' }, [f.label || f.key, required(f)]),
      helpNode(f, id),
      body
    ]);
  }

  function pairControl(f, id, describedBy, parts) {
    var inputs = [];
    var cols = [];
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      var input = h('input', {
        id: id + '-' + p.key,
        name: f.key + '.' + p.key,
        className: 'nf-input',
        type: p.type,
        autocomplete: p.autocomplete || null,
        maxlength: p.type === 'text' ? NAME_PART : null,
        required: f.required,
        'aria-describedby': describedBy
      });
      inputs.push(input);
      cols.push(h('div', null, [h('label', { className: 'nf-caption', 'for': id + '-' + p.key, text: p.label }), input]));
    }
    return {
      node: groupShell(f, id, h('div', { className: 'nf-pair' }, cols)),
      controls: inputs,
      read: function () {
        var out = {};
        for (var j = 0; j < parts.length; j++) out[parts[j].key] = inputs[j].value;
        return out;
      },
      focus: inputs[0]
    };
  }

  function datesControl(f, id, describedBy) {
    var list = h('div', { className: 'nf-dates' });
    var addBtn = h('button', { type: 'button', className: 'nf-linkbtn', text: '+ Add another date' });
    var ctrl = { node: null, controls: [], read: null, focus: null };

    function rows() {
      var out = [];
      for (var i = 0; i < list.childNodes.length; i++) out.push(list.childNodes[i]);
      return out;
    }

    function relabel() {
      var all = rows();
      ctrl.controls = [];
      for (var i = 0; i < all.length; i++) {
        var input = all[i].getElementsByTagName('input')[0];
        input.setAttribute('aria-label', (f.label || 'Date') + ' ' + (i + 1));
        input.id = i === 0 ? id : id + '-' + (i + 1);
        ctrl.controls.push(input);
        var remove = all[i].getElementsByTagName('button')[0];
        remove.hidden = all.length < 2;
        remove.setAttribute('aria-label', 'Remove date ' + (i + 1));
      }
      ctrl.focus = ctrl.controls[0];
      addBtn.hidden = all.length >= MAX_DATES;
    }

    function addRow(focus) {
      var input = h('input', { className: 'nf-input', type: 'date', min: todayLocal(), 'aria-describedby': describedBy });
      var remove = h('button', { type: 'button', className: 'nf-remove', text: 'Remove' });
      var row = h('div', { className: 'nf-date-row' }, [input, remove]);
      remove.onclick = function () {
        list.removeChild(row);
        relabel();
        if (ctrl.controls.length) ctrl.controls[ctrl.controls.length - 1].focus();
      };
      list.appendChild(row);
      relabel();
      if (focus) input.focus();
    }

    addBtn.onclick = function () { addRow(true); };
    addRow(false);
    ctrl.node = groupShell(f, id, h('div', null, [list, h('div', { className: 'nf-actions-inline', style: 'margin-top:8px' }, [addBtn])]));
    ctrl.read = function () {
      var out = [];
      for (var i = 0; i < ctrl.controls.length; i++) out.push(ctrl.controls[i].value);
      return out;
    };
    return ctrl;
  }

  function optionsControl(f, id, describedBy) {
    var opts = Array.isArray(f.options) ? f.options : [];
    var many = f.type === 'checkbox';
    if (!many && opts.length > RADIO_MAX) {
      var select = h('select', { id: id, name: f.key, className: 'nf-input', required: f.required, 'aria-describedby': describedBy }, [
        h('option', { value: '', text: 'Select…' })
      ]);
      for (var s = 0; s < opts.length; s++) select.appendChild(h('option', { value: optValue(opts[s]), text: optLabel(opts[s]) }));
      return {
        node: h('div', { className: 'nf-field' }, [h('label', { className: 'nf-label', 'for': id }, [f.label || f.key, required(f)]), helpNode(f, id), select]),
        controls: [select],
        read: function () { return select.value; },
        focus: select
      };
    }
    var inputs = [];
    var box = h('div', { className: 'nf-options' });
    for (var i = 0; i < opts.length; i++) {
      var input = h('input', {
        type: many ? 'checkbox' : 'radio',
        id: id + '-' + i,
        name: f.key,
        value: optValue(opts[i]),
        'aria-describedby': describedBy
      });
      inputs.push(input);
      box.appendChild(h('label', { className: 'nf-option', 'for': id + '-' + i }, [input, h('span', { text: optLabel(opts[i]) })]));
    }
    return {
      node: groupShell(f, id, box),
      controls: inputs,
      read: function () {
        var picked = [];
        for (var j = 0; j < inputs.length; j++) if (inputs[j].checked) picked.push(inputs[j].value);
        return many ? picked : (picked[0] || '');
      },
      focus: inputs[0]
    };
  }

  function tickControl(f, id, describedBy) {
    var input = h('input', { type: 'checkbox', id: id, name: f.key, required: f.required, 'aria-describedby': describedBy });
    return {
      node: h('div', { className: 'nf-field' }, [
        h('label', { className: 'nf-option', 'for': id }, [input, h('span', null, [f.label || f.key, required(f)])]),
        f.help_text ? h('p', { className: 'nf-help', id: id + '-help', style: 'margin:6px 0 0 28px', text: f.help_text }) : null
      ]),
      controls: [input],
      read: function () { return input.checked; },
      focus: input
    };
  }

  function buildField(f, prefix) {
    var id = prefix + '-' + f.key;
    if (f.type === 'section') {
      var title = sectionTitle(f);
      return {
        node: h('div', { className: 'nf-section' }, [
          title ? h('h3', { className: 'nf-section-title', text: title }) : null,
          f.help_text ? h('p', { className: 'nf-help', text: f.help_text }) : null
        ])
      };
    }
    var describedBy = (f.help_text ? id + '-help ' : '') + id + '-error';
    var ctrl;
    if (f.type === 'name') {
      ctrl = pairControl(f, id, describedBy, [
        { key: 'first', label: 'First name', type: 'text', autocomplete: 'given-name' },
        { key: 'last', label: 'Last name', type: 'text', autocomplete: 'family-name' }
      ]);
    } else if (f.type === 'time_range') {
      ctrl = pairControl(f, id, describedBy, [
        { key: 'start', label: 'Start', type: 'time' },
        { key: 'end', label: 'Finish', type: 'time' }
      ]);
    } else if (f.type === 'dates') {
      ctrl = datesControl(f, id, describedBy);
    } else if (f.type === 'choice' || (f.type === 'checkbox' && optionValues(f).length)) {
      ctrl = optionsControl(f, id, describedBy);
    } else if (f.type === 'checkbox') {
      ctrl = tickControl(f, id, describedBy);
    } else {
      ctrl = textControl(f, id, describedBy);
    }
    ctrl.error = h('p', { className: 'nf-error', id: id + '-error', hidden: true });
    ctrl.node.appendChild(ctrl.error);
    return ctrl;
  }

  // ---------------------------------------------------------------------
  // Network (XMLHttpRequest: simple requests, a timeout, every browser)
  // ---------------------------------------------------------------------

  // A submit gets longer than a read: giving up on one the server then
  // commits would invite a second, duplicate send.
  var READ_TIMEOUT_MS = 20000;
  var SEND_TIMEOUT_MS = 45000;

  function request(method, url, body, done) {
    var xhr = new XMLHttpRequest();
    var finished = false;
    function finish(status) {
      if (finished) return;
      finished = true;
      var data = null;
      try { data = JSON.parse(xhr.responseText); } catch (e) { data = null; }
      done(status, data);
    }
    xhr.open(method, url, true);
    xhr.timeout = body === null ? READ_TIMEOUT_MS : SEND_TIMEOUT_MS;
    if (body !== null) xhr.setRequestHeader('Content-Type', 'text/plain;charset=UTF-8');
    xhr.onload = function () { finish(xhr.status); };
    xhr.onerror = function () { finish(0); };
    xhr.ontimeout = function () { finish(0); };
    xhr.send(body === null ? null : JSON.stringify(body));
  }

  function apiUrl(base, slug, suffix) {
    return base + '/api/public/forms/' + encodeURIComponent(slug) + (suffix || '');
  }

  function detailOf(data) {
    return data && isObject(data.detail) ? data.detail : {};
  }

  function failureText(status, data) {
    var detail = detailOf(data);
    if (status === 0) return "We couldn't reach the venue's booking system. Check your connection and try again.";
    if (status === 404) return "This form isn't available any more. Please contact the venue directly.";
    if (status === 413) return "That's too much to send in one go. Please shorten your answers and try again.";
    if (status === 429) return 'Too many tries in a short time. Please wait a minute, then send again.';
    if (typeof detail.message === 'string' && detail.message) return detail.message;
    return 'Something went wrong sending your enquiry. Please try again.';
  }

  // ---------------------------------------------------------------------
  // A mounted form
  // ---------------------------------------------------------------------

  function status(el, text, retry) {
    clear(el);
    var p = h('p', { className: 'nf-status', role: retry ? 'alert' : 'status', text: text });
    el.appendChild(p);
    if (retry) {
      var btn = h('button', { type: 'button', className: 'nf-linkbtn', text: 'Try again' });
      btn.onclick = retry;
      el.appendChild(btn);
    }
  }

  function load(ctrl) {
    status(ctrl.el, 'Loading the form…');
    ctrl.el.setAttribute('aria-busy', 'true');
    request('GET', apiUrl(ctrl.base, ctrl.slug), null, function (code, data) {
      ctrl.el.removeAttribute('aria-busy');
      if (code === 200 && data && data.form && Array.isArray(data.fields)) {
        ctrl.schema = data;
        ctrl.token = data.form_token;
        ctrl.fetchedAt = new Date().getTime();
        render(ctrl);
        if (ctrl.opts.onLoad) ctrl.opts.onLoad(data);
        return;
      }
      if (code === 404) status(ctrl.el, "This form isn't available. Please contact the venue directly.");
      else status(ctrl.el, "The form couldn't load.", function () { load(ctrl); });
      if (ctrl.opts.onLoad) ctrl.opts.onLoad(null);
    });
  }

  function render(ctrl) {
    var schema = ctrl.schema;
    var form = schema.form;
    var el = ctrl.el;
    var prefix = 'nf' + (++uid);
    clear(el);
    applyTheme(el, form.theme);
    ctrl.fields = schema.fields;
    ctrl.parts = {};

    var heading = h(ctrl.opts.heading || 'h2', { className: 'nf-title', id: prefix + '-title', text: form.name || 'Enquiry' });
    el.appendChild(heading);
    if (form.intro_html) {
      var intro = h('div', { className: 'nf-intro' });
      intro.innerHTML = form.intro_html; // sanitised by Norm (nh3) before it is served
      el.appendChild(intro);
    }

    var alert = h('div', { className: 'nf-alert', role: 'alert', hidden: true, tabindex: '-1' });
    var list = h('div', { className: 'nf-fields' });
    for (var i = 0; i < schema.fields.length; i++) {
      var part = buildField(schema.fields[i], prefix);
      if (schema.fields[i].type !== 'section') ctrl.parts[schema.fields[i].key] = part;
      list.appendChild(part.node);
    }

    // The honeypot: off-screen rather than display:none, so a naive bot fills
    // it; hidden from assistive tech and the tab order, and marked for
    // password managers to skip.
    var hpName = form.honeypot_field || 'website';
    ctrl.hp = h('input', {
      type: 'text', id: prefix + '-hp', name: hpName, tabindex: '-1', autocomplete: 'off',
      'data-lpignore': 'true', 'data-1p-ignore': 'true', 'data-form-type': 'other'
    });
    var trap = h('div', { className: 'nf-hp', 'aria-hidden': 'true' }, [
      h('label', { 'for': prefix + '-hp', text: 'Leave this field empty' }), ctrl.hp
    ]);

    ctrl.button = h('button', { type: 'submit', className: 'nf-submit', text: form.submit_label || 'Send enquiry' });
    ctrl.alert = alert;
    var formEl = h('form', { className: 'nf-form', novalidate: true, 'aria-labelledby': prefix + '-title' }, [
      alert, list, trap, h('div', { className: 'nf-actions' }, [ctrl.button])
    ]);
    formEl.onsubmit = function (ev) {
      if (ev && ev.preventDefault) ev.preventDefault();
      submit(ctrl);
      return false;
    };
    // Once a send has failed, re-check a field as it is corrected.
    formEl.addEventListener('input', function (ev) { recheck(ctrl, ev.target); });
    formEl.addEventListener('change', function (ev) { recheck(ctrl, ev.target); });
    el.appendChild(formEl);
    ctrl.formEl = formEl;
  }

  function values(ctrl) {
    var out = {};
    for (var k in ctrl.parts) if (hasOwn.call(ctrl.parts, k)) out[k] = ctrl.parts[k].read();
    return out;
  }

  function fieldByKey(ctrl, key) {
    for (var i = 0; i < ctrl.fields.length; i++) if (ctrl.fields[i].key === key) return ctrl.fields[i];
    return null;
  }

  function showError(part, message) {
    part.error.hidden = !message;
    clear(part.error);
    if (message) part.error.appendChild(document.createTextNode(message));
    for (var i = 0; i < part.controls.length; i++) {
      if (message) part.controls[i].setAttribute('aria-invalid', 'true');
      else part.controls[i].removeAttribute('aria-invalid');
    }
  }

  function showAlert(ctrl, title, messages) {
    clear(ctrl.alert);
    if (!title) {
      ctrl.alert.hidden = true;
      return;
    }
    ctrl.alert.appendChild(h('div', { text: title }));
    if (messages && messages.length) {
      var ul = h('ul');
      for (var i = 0; i < messages.length; i++) ul.appendChild(h('li', { text: messages[i] }));
      ctrl.alert.appendChild(ul);
    }
    ctrl.alert.hidden = false;
  }

  function showErrors(ctrl, errors) {
    var first = null;
    var messages = [];
    var unplaced = [];
    for (var i = 0; i < ctrl.fields.length; i++) {
      var f = ctrl.fields[i];
      var part = ctrl.parts[f.key];
      if (!part) continue;
      var msg = errors[f.key] || null;
      showError(part, msg);
      if (msg) {
        messages.push(msg);
        if (!first) first = part;
      }
    }
    for (var k in errors) if (hasOwn.call(errors, k) && !ctrl.parts[k]) unplaced.push(String(errors[k]));
    messages = messages.concat(unplaced);
    ctrl.errors = errors;
    if (messages.length) showAlert(ctrl, messages.length === 1 ? 'Please check this answer:' : 'Please check these answers:', messages);
    else showAlert(ctrl, null);
    return first;
  }

  function recheck(ctrl, target) {
    if (!ctrl.errors || ctrl.sending) return;
    for (var k in ctrl.parts) {
      if (!hasOwn.call(ctrl.parts, k)) continue;
      var part = ctrl.parts[k];
      if (indexOf(part.controls, target) < 0) continue;
      if (!ctrl.errors[k]) return;
      var msg = problem(fieldByKey(ctrl, k), part.read());
      showError(part, msg);
      if (!msg) delete ctrl.errors[k];
      return;
    }
  }

  function setSending(ctrl, on) {
    ctrl.sending = on;
    ctrl.button.disabled = on;
    if (on) ctrl.button.setAttribute('aria-busy', 'true');
    else ctrl.button.removeAttribute('aria-busy');
    clear(ctrl.button);
    ctrl.button.appendChild(document.createTextNode(on ? 'Sending…' : (ctrl.schema.form.submit_label || 'Send enquiry')));
  }

  function tracking(ctrl) {
    var t = typeof ctrl.opts.tracking === 'function' ? ctrl.opts.tracking() : null;
    if (!isObject(t)) t = attribution();
    var out = {};
    var allowed = TAGS.concat(['landing_page', 'referrer']);
    for (var i = 0; i < allowed.length; i++) {
      var v = t[allowed[i]];
      if (typeof v === 'string' && v) out[allowed[i]] = v.slice(0, allowed[i] === 'landing_page' || allowed[i] === 'referrer' ? 1000 : 500);
    }
    return out;
  }

  function submit(ctrl) {
    if (ctrl.sending || ctrl.done) return;
    var answers = values(ctrl);
    var errors = validate(ctrl.fields, answers);
    var first = showErrors(ctrl, errors);
    if (first) {
      if (first.focus && first.focus.focus) first.focus.focus();
      return;
    }
    setSending(ctrl, true);
    var body = {
      payload: serialise(ctrl.fields, answers, ctrl.schema.form.honeypot_field || 'website', ctrl.hp.value),
      form_token: ctrl.token,
      tracking: tracking(ctrl)
    };
    var go = function () {
      var wait = MIN_FILL_MS - (new Date().getTime() - ctrl.fetchedAt);
      window.setTimeout(function () { send(ctrl, body); }, wait > 0 ? wait : 0);
    };
    if (new Date().getTime() - ctrl.fetchedAt > TOKEN_REFRESH_MS) refreshToken(ctrl, body, go);
    else go();
  }

  // A form left open for two days: fetch a fresh token (keeping the answers).
  function refreshToken(ctrl, body, then) {
    request('GET', apiUrl(ctrl.base, ctrl.slug), null, function (code, data) {
      if (code === 200 && data && data.form_token) {
        ctrl.token = data.form_token;
        ctrl.fetchedAt = new Date().getTime();
        body.form_token = data.form_token;
      }
      then();
    });
  }

  function send(ctrl, body) {
    request('POST', apiUrl(ctrl.base, ctrl.slug, '/submit'), body, function (code, data) {
      setSending(ctrl, false);
      if (code === 200 && data && data.ok) {
        succeed(ctrl, typeof data.success_message === 'string' && data.success_message ? data.success_message : ctrl.schema.form.success_message);
        return;
      }
      var detail = detailOf(data);
      if (code === 422 && isObject(detail.errors) && keys(detail.errors).length) {
        var first = showErrors(ctrl, detail.errors);
        if (first && first.focus) first.focus.focus();
        else ctrl.alert.focus();
        return;
      }
      showAlert(ctrl, failureText(code, data));
      ctrl.alert.focus();
    });
  }

  function succeed(ctrl, message) {
    ctrl.done = true;
    var box = h('div', { className: 'nf-success', role: 'status', tabindex: '-1' });
    var lines = String(message || "Thanks — we'll be in touch.").split(/\n+/);
    for (var i = 0; i < lines.length; i++) if (trim(lines[i])) box.appendChild(h('p', { text: trim(lines[i]) }));
    ctrl.el.replaceChild(box, ctrl.formEl);
    box.focus();
    fire(ctrl.el, 'norm-form:submitted', { slug: ctrl.slug });
    if (ctrl.opts.onSubmitted) ctrl.opts.onSubmitted();
  }

  // ---------------------------------------------------------------------
  // The iframe variant: Norm's hosted page, sized to its content
  // ---------------------------------------------------------------------

  var frames = [];
  var listening = false;

  function onMessage(ev) {
    var data = ev.data;
    if (ev.origin !== ORIGIN || !isObject(data) || typeof data.type !== 'string') return;
    for (var i = 0; i < frames.length; i++) {
      var f = frames[i];
      if (ev.source !== f.frame.contentWindow) continue;
      if (data.type === 'norm-form:height' && typeof data.height === 'number' && data.height > 0 && data.height < 20000) {
        f.frame.style.height = Math.ceil(data.height) + 'px';
        f.frame.style.minHeight = '0';
      } else if (data.type === 'norm-form:ready') {
        // Hand the hosted form this site's first touch: inside the frame it
        // can't read this site's storage.
        f.frame.contentWindow.postMessage({ type: 'norm-form:tracking', tracking: attribution() }, ORIGIN);
      } else if (data.type === 'norm-form:submitted') {
        fire(f.el, 'norm-form:submitted', { slug: f.slug });
      }
    }
  }

  function mountFrame(el, slug) {
    var frame = h('iframe', {
      src: ORIGIN + '/f/' + encodeURIComponent(slug),
      title: el.getAttribute('data-norm-form-title') || 'Enquiry form',
      loading: 'lazy',
      style: 'display:block;width:100%;min-height:640px;border:0;background:transparent'
    });
    clear(el);
    el.appendChild(frame);
    frames.push({ frame: frame, el: el, slug: slug });
    if (!listening) {
      listening = true;
      window.addEventListener('message', onMessage);
    }
  }

  // ---------------------------------------------------------------------
  // Public API
  // ---------------------------------------------------------------------

  /**
   * Render form `slug` into `el`. Options: heading ('h1'/'h2'), mode
   * ('iframe'), tracking (function returning the attribution to send),
   * onLoad(schema | null), onSubmitted(). Mounting the same element twice
   * does nothing.
   */
  function mount(el, slug, opts) {
    if (!el || !slug || el.getAttribute('data-norm-form-ready')) return;
    el.setAttribute('data-norm-form-ready', '1');
    opts = opts || {};
    if ((opts.mode || el.getAttribute('data-norm-form-mode')) === 'iframe') {
      mountFrame(el, slug);
      return;
    }
    injectStyle();
    addClass(el, 'nf');
    load({ el: el, slug: String(slug), opts: opts, base: ORIGIN, sending: false, done: false });
  }

  function scan() {
    var nodes = document.querySelectorAll('[data-norm-form]');
    for (var i = 0; i < nodes.length; i++) {
      if (!nodes[i].getAttribute('data-norm-form-ready')) mount(nodes[i], nodes[i].getAttribute('data-norm-form'));
    }
  }

  window.NormForms = {
    version: VERSION,
    mount: mount,
    scan: scan,
    attribution: attribution,
    validate: validate,
    serialise: serialise,
    themeVars: themeVars
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', scan);
  else scan();
})(window, document);
