/* ==========================================================================
 * Plugin RAG widget
 *
 * Drops a floating chat bubble onto any page. All DOM lives inside a Shadow
 * Root so the host page can't restyle us and we can't leak CSS into them.
 * Markdown coming from the model is rendered via a hand-written DOM-only
 * renderer (no innerHTML on model text -> XSS-safe by construction).
 *
 * Settings come from `window.__PLUGIN_RAG_SETTINGS__`, prepended to this
 * bundle by the /widget.js endpoint. The backend URL defaults to the
 * script's own origin and can be overridden with `data-backend="..."`.
 * ========================================================================= */
(function () {
  if (window.__PLUGIN_RAG_LOADED__) return;
  window.__PLUGIN_RAG_LOADED__ = true;

  var settings = window.__PLUGIN_RAG_SETTINGS__ || {};
  var SCRIPT = document.currentScript;
  var BACKEND =
    (SCRIPT && SCRIPT.getAttribute('data-backend')) ||
    (SCRIPT && new URL(SCRIPT.src, location.href).origin) ||
    location.origin;
  var EMBED_MODE = SCRIPT && SCRIPT.getAttribute('data-mode') === 'embed';

  var TITLE        = settings.title       || 'Assistant';
  var SUBTITLE     = settings.subtitle    || '';
  var GREETING     = settings.greeting    || "Hi! How can I help today?";
  var COLOR        = settings.primaryColor || '#6366f1';
  var SECONDARY    = settings.secondaryColor || null;  // gradient endpoint; falls back to dark primary
  var TERTIARY     = settings.tertiaryColor  || null;  // accent (pulse ring, chip hover); falls back to primary
  var POSITION     = settings.position === 'bottom-left' ? 'left' : 'right';
  var STARTERS     = Array.isArray(settings.starterQuestions) ? settings.starterQuestions : [];
  var SVG_NS       = 'http://www.w3.org/2000/svg';
  // CSS expressions injected into the stylesheet. When the host site does
  // not specify secondary/tertiary, derive sensible defaults from primary
  // so single-color setups keep working unchanged.
  var C2_VALUE = SECONDARY || 'color-mix(in srgb, var(--c) 80%, black)';
  var C3_VALUE = TERTIARY  || 'var(--c)';

  /* --------------------- session id --------------------------------- */
  var SID_KEY = 'plugin_rag_sid';
  var sid;
  try { sid = localStorage.getItem(SID_KEY); } catch (e) { sid = null; }
  if (!sid) {
    sid = (crypto && crypto.randomUUID)
      ? crypto.randomUUID()
      : 'sid-' + Math.random().toString(36).slice(2) + Date.now();
    try { localStorage.setItem(SID_KEY, sid); } catch (e) {}
  }

  /* --------------------- styles ------------------------------------- */
  var STYLE = `
    :host { all: initial; }
    *, *::before, *::after { box-sizing: border-box; }

    .root {
      --c: ${COLOR};
      --c2: ${C2_VALUE};
      --c3: ${C3_VALUE};
      --c-soft: color-mix(in srgb, var(--c3) 14%, transparent);
      --c-strong: var(--c2);
      --bg: #ffffff;
      --bg-alt: #f8fafc;
      --fg: #0f172a;
      --muted: #64748b;
      --border: rgba(15, 23, 42, 0.08);
      --shadow: 0 30px 80px -20px rgba(15, 23, 42, 0.35);
      font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', Roboto, Arial, sans-serif;
      color: var(--fg);
      font-size: 14px;
      line-height: 1.55;
    }

    /* ---- floating bubble ---- */
    .bubble {
      position: fixed;
      bottom: 20px;
      ${POSITION}: 20px;
      width: 60px;
      height: 60px;
      border-radius: 50%;
      background: linear-gradient(135deg, var(--c) 0%, var(--c2) 100%);
      color: #fff;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      border: none;
      box-shadow: 0 18px 35px -10px color-mix(in srgb, var(--c) 60%, transparent),
                  0 6px 14px rgba(0,0,0,0.18);
      z-index: 2147483646;
      transition: transform .22s cubic-bezier(.34,1.56,.64,1),
                  box-shadow .22s ease;
      animation: bubble-in .55s cubic-bezier(.34,1.56,.64,1) both;
    }
    .bubble::before {
      content: '';
      position: absolute;
      inset: -4px;
      border-radius: 50%;
      background: var(--c3);
      opacity: .35;
      filter: blur(14px);
      z-index: -1;
      animation: pulse-ring 3s ease-in-out infinite;
    }
    .bubble:hover { transform: translateY(-3px) scale(1.04); }
    .bubble:active { transform: translateY(0) scale(0.96); }
    .bubble svg { width: 28px; height: 28px; fill: #fff; transition: transform .25s ease; }
    .bubble.open svg { transform: rotate(90deg); }
    @keyframes bubble-in {
      from { transform: translateY(28px) scale(.4); opacity: 0; }
      to   { transform: none;                       opacity: 1; }
    }
    @keyframes pulse-ring {
      0%, 100% { opacity: .25; transform: scale(1); }
      50%      { opacity: .55; transform: scale(1.08); }
    }

    /* ---- panel ---- */
    .panel {
      position: fixed;
      bottom: 96px;
      ${POSITION}: 20px;
      width: 400px;
      max-width: calc(100vw - 32px);
      height: 620px;
      max-height: calc(100vh - 120px);
      background: var(--bg);
      border-radius: 24px;
      box-shadow: var(--shadow);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      z-index: 2147483647;
      border: 1px solid var(--border);
      transform: translateY(16px) scale(.96);
      opacity: 0;
      pointer-events: none;
      transform-origin: ${POSITION === 'left' ? 'bottom left' : 'bottom right'};
      transition: transform .35s cubic-bezier(.34,1.56,.64,1),
                  opacity .25s ease;
      backdrop-filter: blur(8px);
    }
    .panel.open {
      transform: none;
      opacity: 1;
      pointer-events: auto;
    }

    .embed-mode .bubble { display: none; }
    .embed-mode .panel {
      position: relative; bottom: auto; right: auto; left: auto;
      width: 100%; height: 100%; max-height: 100%; max-width: 100%;
      border-radius: 0; box-shadow: none; border: none;
      transform: none; opacity: 1; pointer-events: auto;
    }

    /* ---- header ---- */
    .header {
      position: relative;
      padding: 18px 18px 16px;
      color: #fff;
      background: radial-gradient(120% 120% at 0% 0%, color-mix(in srgb, var(--c) 70%, white) 0%, var(--c) 60%, var(--c2) 100%);
      display: flex; align-items: center; gap: 12px;
      overflow: hidden;
    }
    .header::after {
      content: '';
      position: absolute;
      inset: 0;
      background:
        radial-gradient(60% 80% at 90% 0%, rgba(255,255,255,0.25), transparent 60%),
        radial-gradient(50% 60% at 10% 100%, rgba(255,255,255,0.18), transparent 60%);
      pointer-events: none;
    }
    .avatar {
      width: 38px; height: 38px;
      border-radius: 12px;
      background: rgba(255,255,255,0.2);
      backdrop-filter: blur(6px);
      display: flex; align-items: center; justify-content: center;
      flex-shrink: 0;
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.3);
    }
    .avatar svg { width: 20px; height: 20px; fill: #fff; }
    .avatar .dot {
      position: absolute;
      transform: translate(13px, 13px);
      width: 10px; height: 10px;
      border-radius: 50%;
      background: #22c55e;
      box-shadow: 0 0 0 2px var(--c);
    }
    .meta { flex: 1; min-width: 0; }
    .meta .t { font-weight: 600; font-size: 15px; line-height: 1.2; letter-spacing: -.01em; }
    .meta .s { font-size: 12px; opacity: .85; line-height: 1.3; margin-top: 3px; }
    .close {
      background: rgba(255,255,255,0.15);
      border: none;
      color: #fff;
      cursor: pointer;
      width: 30px; height: 30px;
      border-radius: 8px;
      display: flex; align-items: center; justify-content: center;
      transition: background .15s ease, transform .15s ease;
    }
    .close:hover { background: rgba(255,255,255,0.28); transform: rotate(90deg); }
    .close svg { width: 16px; height: 16px; stroke: #fff; }
    .embed-mode .close { display: none; }

    .icon-btn {
      background: rgba(255,255,255,0.15);
      border: none;
      color: #fff;
      cursor: pointer;
      width: 30px; height: 30px;
      border-radius: 8px;
      display: flex; align-items: center; justify-content: center;
      margin-right: 6px;
      transition: background .15s ease, transform .15s ease;
    }
    .icon-btn:hover { background: rgba(255,255,255,0.28); }
    .icon-btn:active { transform: scale(0.92); }
    .icon-btn[disabled] { opacity: .45; cursor: not-allowed; }
    .icon-btn svg { width: 15px; height: 15px; fill: #fff; }

    /* ---- messages ---- */
    .messages {
      flex: 1;
      overflow-y: auto;
      padding: 18px 16px 6px;
      background: var(--bg-alt);
      display: flex; flex-direction: column; gap: 12px;
      scroll-behavior: smooth;
    }
    .messages::-webkit-scrollbar { width: 8px; }
    .messages::-webkit-scrollbar-thumb {
      background: color-mix(in srgb, var(--muted) 35%, transparent);
      border-radius: 4px;
    }

    .row { display: flex; gap: 8px; align-items: flex-end; max-width: 88%; }
    .row.user { align-self: flex-end; flex-direction: row-reverse; }
    .row.bot  { align-self: flex-start; }
    .mini-av {
      width: 26px; height: 26px;
      border-radius: 50%;
      background: linear-gradient(135deg, var(--c), var(--c2));
      color: #fff;
      flex-shrink: 0;
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 4px 10px -4px var(--c);
    }
    .mini-av svg { width: 14px; height: 14px; fill: #fff; }
    .row.user .mini-av { background: #1e293b; box-shadow: 0 4px 10px -4px #1e293b; }

    .bubble-msg {
      padding: 10px 14px;
      border-radius: 16px;
      word-wrap: break-word;
      overflow-wrap: anywhere;
      animation: msg-in .28s cubic-bezier(.2,.7,.3,1) both;
    }
    .row.user .bubble-msg {
      background: linear-gradient(135deg, var(--c) 0%, var(--c2) 100%);
      color: #fff;
      border-bottom-right-radius: 4px;
      white-space: pre-wrap;
      box-shadow: 0 6px 18px -8px color-mix(in srgb, var(--c) 70%, transparent);
    }
    .row.bot .bubble-msg {
      background: var(--bg);
      color: var(--fg);
      border: 1px solid var(--border);
      border-bottom-left-radius: 4px;
      box-shadow: 0 1px 2px rgba(15,23,42,0.04);
    }
    @keyframes msg-in {
      from { transform: translateY(8px); opacity: 0; }
      to   { transform: none;            opacity: 1; }
    }

    /* ---- markdown ---- */
    .md p { margin: 0 0 8px 0; }
    .md p:last-child { margin-bottom: 0; }
    .md ul, .md ol { margin: 4px 0 8px; padding-left: 22px; }
    .md li { margin: 2px 0; }
    .md h1, .md h2, .md h3 { margin: 8px 0 4px; line-height: 1.25; letter-spacing: -.01em; }
    .md h1 { font-size: 1.18em; } .md h2 { font-size: 1.1em; } .md h3 { font-size: 1.02em; }
    .md a { color: var(--c2); text-decoration: underline; word-break: break-all; }
    .md code {
      font-family: 'SF Mono', Consolas, 'Liberation Mono', monospace;
      font-size: 12.5px;
      background: rgba(15,23,42,0.06);
      padding: 1px 6px;
      border-radius: 6px;
    }
    .md pre {
      background: #0f172a; color: #e2e8f0;
      padding: 12px 14px;
      border-radius: 10px;
      overflow-x: auto;
      font-size: 12.5px;
      margin: 6px 0;
    }
    .md pre code { background: transparent; color: inherit; padding: 0; }
    .md blockquote {
      border-left: 3px solid var(--c);
      padding: 0 10px;
      color: var(--muted);
      margin: 6px 0;
    }
    .md hr { border: none; border-top: 1px solid var(--border); margin: 8px 0; }

    /* ---- streaming fade ---- */
    /* Apply a gradient mask while streaming so the most-recently arrived
       text emerges from a soft fade at the bottom of the bubble. As more
       text comes in, earlier text scrolls out of the fade region and
       becomes fully solid. Pure CSS - no animation timing to misbehave. */
    .bubble-msg.md.streaming {
      -webkit-mask-image: linear-gradient(to bottom, #000 0, #000 calc(100% - 22px), transparent 100%);
              mask-image: linear-gradient(to bottom, #000 0, #000 calc(100% - 22px), transparent 100%);
    }

    /* ---- typing ---- */
    .typing {
      display: inline-flex; align-items: center; gap: 4px; padding: 10px 14px;
    }
    .typing span {
      width: 7px; height: 7px;
      border-radius: 50%;
      background: var(--c);
      opacity: .4;
      animation: pulse 1.2s infinite ease-in-out;
    }
    .typing span:nth-child(2) { animation-delay: .15s; }
    .typing span:nth-child(3) { animation-delay: .3s; }
    @keyframes pulse {
      0%, 60%, 100% { opacity: .35; transform: translateY(0); }
      30%           { opacity: 1;   transform: translateY(-3px); }
    }

    /* ---- suggestion chips ---- */
    .suggestions { display: flex; flex-wrap: wrap; gap: 6px; padding: 4px 16px 0; }
    .suggestions button {
      font: inherit;
      font-size: 12.5px;
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--c2);
      border-radius: 999px;
      padding: 7px 12px;
      cursor: pointer;
      transition: transform .12s ease, background .12s ease, border-color .12s ease;
      animation: msg-in .35s ease both;
    }
    .suggestions button:hover {
      background: var(--c-soft);
      border-color: var(--c3);
      transform: translateY(-1px);
    }

    /* ---- composer ---- */
    .composer {
      border-top: 1px solid var(--border);
      padding: 10px 12px 12px;
      display: flex;
      gap: 8px;
      background: var(--bg);
      align-items: flex-end;
    }
    .composer textarea {
      flex: 1;
      resize: none;
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 10px 12px;
      font: inherit;
      font-size: 14px;
      line-height: 1.4;
      outline: none;
      max-height: 110px;
      min-height: 40px;
      color: var(--fg);
      background: var(--bg-alt);
      transition: border-color .15s ease, background .15s ease;
    }
    .composer textarea:focus {
      border-color: var(--c);
      background: var(--bg);
    }
    .composer .send {
      background: linear-gradient(135deg, var(--c), var(--c2));
      color: #fff;
      border: none;
      border-radius: 12px;
      width: 40px; height: 40px;
      cursor: pointer;
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 8px 18px -6px color-mix(in srgb, var(--c) 60%, transparent);
      transition: transform .12s ease, opacity .12s ease;
    }
    .composer .send:hover { transform: translateY(-1px); }
    .composer .send:active { transform: translateY(0) scale(.95); }
    .composer .send[disabled] { opacity: .5; cursor: not-allowed; transform: none; }
    .composer .send svg { width: 18px; height: 18px; fill: #fff; }

    .footer {
      font-size: 11px; text-align: center;
      color: var(--muted);
      padding: 6px 0 8px;
      background: var(--bg);
    }

    .err {
      align-self: center;
      font-size: 12.5px;
      color: #b91c1c;
      background: #fee2e2;
      border: 1px solid #fecaca;
      padding: 6px 12px;
      border-radius: 10px;
      animation: msg-in .25s ease both;
    }
  `;

  /* --------------------- SVG helpers ----------------------------- */
  function svgEl(viewBox) {
    var s = document.createElementNS(SVG_NS, 'svg');
    s.setAttribute('viewBox', viewBox);
    s.setAttribute('aria-hidden', 'true');
    return s;
  }
  function svgPath(attrs) {
    var p = document.createElementNS(SVG_NS, 'path');
    for (var k in attrs) {
      if (Object.prototype.hasOwnProperty.call(attrs, k)) p.setAttribute(k, attrs[k]);
    }
    return p;
  }

  /* --------------------- DOM construction ------------------------ */
  var host = document.createElement('div');
  host.id = 'plugin-rag-root';
  if (EMBED_MODE) host.style.cssText = 'position:relative;width:100%;height:100%;';
  document.body.appendChild(host);
  var shadow = host.attachShadow({ mode: 'open' });

  var styleEl = document.createElement('style');
  styleEl.textContent = STYLE;
  shadow.appendChild(styleEl);

  var root = document.createElement('div');
  root.className = 'root' + (EMBED_MODE ? ' embed-mode' : '');
  shadow.appendChild(root);

  // Bubble
  var bubble = document.createElement('button');
  bubble.className = 'bubble';
  bubble.setAttribute('aria-label', 'Open chat');
  var bSvg = svgEl('0 0 24 24');
  bSvg.appendChild(svgPath({ d: 'M4 4h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H8l-4 4V6a2 2 0 0 1 2-2z' }));
  bubble.appendChild(bSvg);
  root.appendChild(bubble);

  // Panel
  var panel = document.createElement('div');
  panel.className = 'panel' + (EMBED_MODE ? ' open' : '');
  root.appendChild(panel);

  // Header
  var header = document.createElement('div');
  header.className = 'header';
  panel.appendChild(header);

  var avatar = document.createElement('div');
  avatar.className = 'avatar';
  var avSvg = svgEl('0 0 24 24');
  avSvg.appendChild(svgPath({ d: 'M12 2a4 4 0 1 1 0 8 4 4 0 0 1 0-8zm0 10c4.4 0 8 2.7 8 6v2H4v-2c0-3.3 3.6-6 8-6z' }));
  avatar.appendChild(avSvg);
  var dot = document.createElement('span');
  dot.className = 'dot';
  avatar.appendChild(dot);
  header.appendChild(avatar);

  var meta = document.createElement('div');
  meta.className = 'meta';
  var titleEl = document.createElement('div');
  titleEl.className = 't'; titleEl.textContent = TITLE;
  meta.appendChild(titleEl);
  if (SUBTITLE) {
    var subEl = document.createElement('div');
    subEl.className = 's'; subEl.textContent = SUBTITLE;
    meta.appendChild(subEl);
  }
  header.appendChild(meta);

  var clearBtn = document.createElement('button');
  clearBtn.className = 'icon-btn';
  clearBtn.type = 'button';
  clearBtn.setAttribute('aria-label', 'Clear chat');
  clearBtn.title = 'Clear chat';
  var clrSvg = svgEl('0 0 24 24');
  clrSvg.appendChild(svgPath({
    d: 'M9 3h6a1 1 0 0 1 1 1v1h4a1 1 0 1 1 0 2h-1l-1 13a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 7H4a1 1 0 1 1 0-2h4V4a1 1 0 0 1 1-1zm1 4v11h2V7h-2zm4 0v11h2V7h-2z',
  }));
  clearBtn.appendChild(clrSvg);
  header.appendChild(clearBtn);

  var closeBtn = document.createElement('button');
  closeBtn.className = 'close';
  closeBtn.setAttribute('aria-label', 'Close chat');
  var cSvg = svgEl('0 0 24 24');
  cSvg.appendChild(svgPath({
    d: 'M6 6l12 12M18 6L6 18',
    stroke: 'currentColor', 'stroke-width': '2',
    fill: 'none', 'stroke-linecap': 'round',
  }));
  closeBtn.appendChild(cSvg);
  header.appendChild(closeBtn);

  // Messages
  var messages = document.createElement('div');
  messages.className = 'messages';
  panel.appendChild(messages);

  // Suggestions row (separate from messages so it can sit just above composer)
  var suggestionsRow = document.createElement('div');
  suggestionsRow.className = 'suggestions';
  panel.appendChild(suggestionsRow);

  // Composer
  var composer = document.createElement('form');
  composer.className = 'composer';
  composer.setAttribute('autocomplete', 'off');
  panel.appendChild(composer);

  var input = document.createElement('textarea');
  input.rows = 1;
  input.placeholder = 'Ask anything…';
  input.setAttribute('aria-label', 'Message');
  composer.appendChild(input);

  var sendBtn = document.createElement('button');
  sendBtn.type = 'submit';
  sendBtn.className = 'send';
  sendBtn.setAttribute('aria-label', 'Send');
  // Send icon - paper plane pointing right.
  var sSvg = svgEl('0 0 24 24');
  sSvg.appendChild(svgPath({ d: 'M2.5 21l19-9-19-9 0 7 13 2-13 2z' }));
  sendBtn.appendChild(sSvg);
  composer.appendChild(sendBtn);

  var footer = document.createElement('div');
  footer.className = 'footer';
  footer.textContent = 'AI may be inaccurate. Verify important information.';
  panel.appendChild(footer);

  /* --------------------- helpers ---------------------------------- */
  function scroll() { messages.scrollTop = messages.scrollHeight; }

  function withAvatar(sideClass, body) {
    var row = document.createElement('div');
    row.className = 'row ' + sideClass;
    var av = document.createElement('div');
    av.className = 'mini-av';
    var ic = svgEl('0 0 24 24');
    if (sideClass === 'user') {
      ic.appendChild(svgPath({ d: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm0 2c-4 0-8 2-8 5v1h16v-1c0-3-4-5-8-5z' }));
    } else {
      ic.appendChild(svgPath({ d: 'M12 3l1.5 4 4 1.5-4 1.5L12 14l-1.5-4-4-1.5 4-1.5L12 3z' }));
    }
    av.appendChild(ic);
    row.appendChild(av);
    row.appendChild(body);
    return row;
  }

  function addUserMessage(text) {
    var b = document.createElement('div');
    b.className = 'bubble-msg';
    b.textContent = text;
    messages.appendChild(withAvatar('user', b));
    scroll();
  }

  function addBotMessageContainer() {
    var b = document.createElement('div');
    b.className = 'bubble-msg md';
    var row = withAvatar('bot', b);
    messages.appendChild(row);
    scroll();
    return { row: row, body: b };
  }

  function addTypingIndicator() {
    var b = document.createElement('div');
    b.className = 'bubble-msg';
    var t = document.createElement('div');
    t.className = 'typing';
    for (var i = 0; i < 3; i++) t.appendChild(document.createElement('span'));
    b.appendChild(t);
    var row = withAvatar('bot', b);
    messages.appendChild(row);
    scroll();
    return row;
  }

  function addError(text) {
    var d = document.createElement('div');
    d.className = 'err';
    d.textContent = text;
    messages.appendChild(d);
    scroll();
  }

  function clearSuggestions() {
    while (suggestionsRow.firstChild) suggestionsRow.removeChild(suggestionsRow.firstChild);
  }
  function setSuggestions(items) {
    clearSuggestions();
    if (!items || !items.length) return;
    items.slice(0, 5).forEach(function (q) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = q;
      btn.addEventListener('click', function () {
        if (!sending) sendMessage(q);
      });
      suggestionsRow.appendChild(btn);
    });
  }

  /* --------------------- safe markdown renderer ------------------ */
  function isSafeUrl(url) {
    try {
      var u = new URL(url, 'https://placeholder.invalid/');
      return u.protocol === 'http:' || u.protocol === 'https:' || u.protocol === 'mailto:';
    } catch (e) { return false; }
  }

  function renderInline(text, parent) {
    var i = 0, n = text.length;
    while (i < n) {
      var ch = text[i];
      if (ch === '`') {
        var end = text.indexOf('`', i + 1);
        if (end > i) {
          var c = document.createElement('code');
          c.textContent = text.substring(i + 1, end);
          parent.appendChild(c);
          i = end + 1; continue;
        }
      }
      if (ch === '[') {
        var rb = text.indexOf(']', i + 1);
        if (rb > i && text[rb + 1] === '(') {
          var rp = text.indexOf(')', rb + 2);
          if (rp > rb) {
            var label = text.substring(i + 1, rb);
            var url = text.substring(rb + 2, rp).trim();
            if (isSafeUrl(url)) {
              var a = document.createElement('a');
              a.href = url;
              a.target = '_blank';
              a.rel = 'noopener noreferrer nofollow';
              renderInline(label, a);
              parent.appendChild(a);
              i = rp + 1; continue;
            }
          }
        }
      }
      if (ch === '*' && text[i + 1] === '*') {
        var ee = text.indexOf('**', i + 2);
        if (ee > i + 1) {
          var s = document.createElement('strong');
          renderInline(text.substring(i + 2, ee), s);
          parent.appendChild(s);
          i = ee + 2; continue;
        }
      }
      if (ch === '*') {
        var ie = text.indexOf('*', i + 1);
        if (ie > i + 1) {
          var em = document.createElement('em');
          renderInline(text.substring(i + 1, ie), em);
          parent.appendChild(em);
          i = ie + 1; continue;
        }
      }
      if (ch === '_') {
        var ie2 = text.indexOf('_', i + 1);
        if (ie2 > i + 1) {
          var em2 = document.createElement('em');
          renderInline(text.substring(i + 1, ie2), em2);
          parent.appendChild(em2);
          i = ie2 + 1; continue;
        }
      }
      var j = i;
      while (j < n) {
        var c2 = text[j];
        if (c2 === '`' || c2 === '[' || c2 === '*' || c2 === '_') break;
        j++;
      }
      if (j > i) {
        parent.appendChild(document.createTextNode(text.substring(i, j)));
        i = j;
      } else {
        parent.appendChild(document.createTextNode(text[i]));
        i++;
      }
    }
  }

  function renderMarkdown(md, target) {
    while (target.firstChild) target.removeChild(target.firstChild);
    var lines = md.split(/\r?\n/);
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];
      if (/^```/.test(line)) {
        var pre = document.createElement('pre');
        var code = document.createElement('code');
        var buf = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i++; }
        if (i < lines.length) i++;
        code.textContent = buf.join('\n');
        pre.appendChild(code);
        target.appendChild(pre);
        continue;
      }
      var hm = line.match(/^(#{1,6})\s+(.+)$/);
      if (hm) {
        var lvl = Math.min(3, hm[1].length);
        var h = document.createElement('h' + lvl);
        renderInline(hm[2], h);
        target.appendChild(h);
        i++; continue;
      }
      if (/^---+$/.test(line.trim())) {
        target.appendChild(document.createElement('hr'));
        i++; continue;
      }
      if (/^>\s?/.test(line)) {
        var bq = document.createElement('blockquote');
        var bqL = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) {
          bqL.push(lines[i].replace(/^>\s?/, ''));
          i++;
        }
        var p0 = document.createElement('p');
        renderInline(bqL.join(' '), p0);
        bq.appendChild(p0);
        target.appendChild(bq);
        continue;
      }
      if (/^\s*[-*+]\s+/.test(line)) {
        var ul = document.createElement('ul');
        while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
          var li = document.createElement('li');
          renderInline(lines[i].replace(/^\s*[-*+]\s+/, ''), li);
          ul.appendChild(li);
          i++;
        }
        target.appendChild(ul);
        continue;
      }
      if (/^\s*\d+\.\s+/.test(line)) {
        var ol = document.createElement('ol');
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          var li2 = document.createElement('li');
          renderInline(lines[i].replace(/^\s*\d+\.\s+/, ''), li2);
          ol.appendChild(li2);
          i++;
        }
        target.appendChild(ol);
        continue;
      }
      if (line.trim() === '') { i++; continue; }
      var pBuf = [line]; i++;
      while (i < lines.length) {
        var l2 = lines[i];
        if (l2.trim() === '') break;
        if (/^(#{1,6})\s+/.test(l2)) break;
        if (/^```/.test(l2)) break;
        if (/^>\s?/.test(l2)) break;
        if (/^\s*[-*+]\s+/.test(l2)) break;
        if (/^\s*\d+\.\s+/.test(l2)) break;
        pBuf.push(l2); i++;
      }
      var p = document.createElement('p');
      renderInline(pBuf.join(' '), p);
      target.appendChild(p);
    }
    linkify(target);
  }

  // Walk the rendered DOM and wrap bare URLs / email addresses inside text
  // nodes in safe anchors. Skips <a>, <code>, <pre> so we don't double-link
  // markdown links or mangle code blocks. Strips trailing punctuation
  // (".,;:!?)]") so "see https://x.com." links to https://x.com without the
  // dot, and the dot stays in the surrounding text.
  var URL_OR_EMAIL = /(https?:\/\/[^\s<>()"'`]+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})/g;
  function linkify(node) {
    if (node.nodeType === 1) {
      var tag = node.tagName;
      if (tag === 'A' || tag === 'CODE' || tag === 'PRE') return;
      var kids = Array.prototype.slice.call(node.childNodes);
      for (var i = 0; i < kids.length; i++) linkify(kids[i]);
      return;
    }
    if (node.nodeType !== 3) return;
    var text = node.nodeValue;
    URL_OR_EMAIL.lastIndex = 0;
    if (!URL_OR_EMAIL.test(text)) return;
    URL_OR_EMAIL.lastIndex = 0;
    var parent = node.parentNode, last = 0, m;
    var frags = [];
    while ((m = URL_OR_EMAIL.exec(text)) !== null) {
      if (m.index > last) frags.push(document.createTextNode(text.substring(last, m.index)));
      var raw = m[0];
      var trimmed = raw.replace(/[.,;:!?)\]]+$/, '');
      var trailing = raw.substring(trimmed.length);
      var isEmail = trimmed.indexOf('://') === -1 && trimmed.indexOf('@') > -1;
      var href = isEmail ? 'mailto:' + trimmed : trimmed;
      if (isSafeUrl(href)) {
        var a = document.createElement('a');
        a.href = href;
        a.target = '_blank';
        a.rel = 'noopener noreferrer nofollow';
        a.textContent = trimmed;
        frags.push(a);
        if (trailing) frags.push(document.createTextNode(trailing));
      } else {
        frags.push(document.createTextNode(raw));
      }
      last = m.index + raw.length;
    }
    if (last < text.length) frags.push(document.createTextNode(text.substring(last)));
    for (var k = 0; k < frags.length; k++) parent.insertBefore(frags[k], node);
    parent.removeChild(node);
  }

  /* --------------------- SSE streaming --------------------------- */
  function streamChat(message, onEvent) {
    return new Promise(function (resolve) {
      fetch(BACKEND.replace(/\/$/, '') + '/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
        body: JSON.stringify({ message: message, session_id: sid }),
      }).then(function (resp) {
        if (!resp.ok) {
          if (resp.status === 429) {
            var retry = parseInt(resp.headers.get('Retry-After') || '5', 10);
            onEvent('error', { message: 'You\'re sending messages a bit fast. Please wait ' + retry + 's.' });
            onEvent('done', {});
            resolve();
            return;
          }
          onEvent('error', { message: 'Server error. Please try again.' });
          onEvent('done', {});
          resolve();
          return;
        }
        var reader = resp.body.getReader();
        var dec = new TextDecoder();
        var buf = '';
        // SSE event boundary is a blank line; sse-starlette emits CRLFs,
        // many other servers emit LFs - match either.
        var sep = /(?:\r?\n){2}/g;
        function pump() {
          return reader.read().then(function (r) {
            if (r.done) {
              if (buf.trim()) parseSse(buf, onEvent);
              resolve();
              return;
            }
            buf += dec.decode(r.value, { stream: true });
            sep.lastIndex = 0;
            var lastEnd = 0;
            var match;
            while ((match = sep.exec(buf)) !== null) {
              parseSse(buf.substring(lastEnd, match.index), onEvent);
              lastEnd = match.index + match[0].length;
            }
            buf = buf.substring(lastEnd);
            return pump();
          });
        }
        return pump();
      }).catch(function () {
        onEvent('error', { message: 'Network error. Please try again.' });
        onEvent('done', {});
        resolve();
      });
    });
  }

  function parseSse(raw, onEvent) {
    var lines = raw.split(/\r?\n/);
    var ev = 'message';
    var dataLines = [];
    for (var i = 0; i < lines.length; i++) {
      var l = lines[i];
      if (l.indexOf('event:') === 0) ev = l.substring(6).trim();
      else if (l.indexOf('data:') === 0) dataLines.push(l.substring(5).replace(/^ /, ''));
    }
    if (ev === 'ping') return;
    var data = {};
    var ds = dataLines.join('\n');
    if (ds) { try { data = JSON.parse(ds); } catch (e) { data = { _raw: ds }; } }
    onEvent(ev, data);
  }

  /* --------------------- conversation flow ----------------------- */
  var sending = false;
  var typingEl = null;
  var currentBot = null;
  // Typewriter drain decouples on-screen pace from token arrival jitter.
  // pendingText holds chars received but not yet shown; renderedText is
  // what's currently in the DOM. drainTick releases chars at ~30ms per
  // tick with an adaptive step so big bursts don't lag and slow trickles
  // still feel paced.
  var pendingText = '';
  var renderedText = '';
  var drainTimer = null;
  var streamingDone = false;

  function scheduleDrain() {
    if (drainTimer) return;
    drainTimer = setTimeout(drainTick, 30);
  }
  function drainTick() {
    drainTimer = null;
    if (pendingText.length > 0) {
      var step = Math.max(1, Math.ceil(pendingText.length / 8));
      renderedText += pendingText.substring(0, step);
      pendingText = pendingText.substring(step);
      renderStreamingBubble(renderedText);
      scroll();
      scheduleDrain();
      return;
    }
    if (streamingDone) finalizeStreaming();
  }
  function renderStreamingBubble(text) {
    if (!currentBot) currentBot = addBotMessageContainer();
    currentBot.body.classList.add('streaming');
    renderMarkdown(text, currentBot.body);
  }
  function finalizeStreaming() {
    if (currentBot) {
      currentBot.body.classList.remove('streaming');
      if (!renderedText) currentBot.row.remove();
    }
    sending = false;
    sendBtn.disabled = false;
    input.disabled = false;
    clearBtn.disabled = false;
    input.focus();
    currentBot = null;
    pendingText = '';
    renderedText = '';
    streamingDone = false;
  }

  function sendMessage(text) {
    if (sending || !text.trim()) return;
    sending = true;
    sendBtn.disabled = true;
    input.disabled = true;
    clearBtn.disabled = true;
    addUserMessage(text);
    input.value = '';
    autosize();
    clearSuggestions();

    typingEl = addTypingIndicator();
    currentBot = null;
    pendingText = '';
    renderedText = '';
    streamingDone = false;

    streamChat(text, function (ev, data) {
      if (ev === 'token') {
        if (typingEl) { typingEl.remove(); typingEl = null; }
        pendingText += (data && data.text) || '';
        scheduleDrain();
        return;
      }
      if (ev === 'suggestions') {
        setSuggestions((data && data.items) || []);
        return;
      }
      if (ev === 'error') {
        if (typingEl) { typingEl.remove(); typingEl = null; }
        addError((data && data.message) || 'Something went wrong.');
        return;
      }
      if (ev === 'done') {
        if (typingEl) { typingEl.remove(); typingEl = null; }
        streamingDone = true;
        scheduleDrain();
      }
    });
  }

  function clearChat() {
    if (sending) return;
    // Fresh session id so backend won't replay old history.
    sid = (crypto && crypto.randomUUID)
      ? crypto.randomUUID()
      : 'sid-' + Math.random().toString(36).slice(2) + Date.now();
    try { localStorage.setItem(SID_KEY, sid); } catch (e) {}
    while (messages.firstChild) messages.removeChild(messages.firstChild);
    clearSuggestions();
    greeted = false;
    showGreeting();
  }

  /* --------------------- greeting + history loading -------------- */
  var greeted = false;

  function loadHistory() {
    return fetch(BACKEND.replace(/\/$/, '') + '/chat/history?session_id=' + encodeURIComponent(sid))
      .then(function (r) { return r.ok ? r.json() : { messages: [] }; })
      .catch(function () { return { messages: [] }; });
  }

  function showGreeting() {
    if (greeted) return;
    greeted = true;
    loadHistory().then(function (data) {
      var msgs = (data && data.messages) || [];
      if (msgs.length === 0) {
        // Fresh session - show the greeting + starter chips.
        var bot = addBotMessageContainer();
        renderMarkdown(GREETING, bot.body);
        if (STARTERS.length) setSuggestions(STARTERS);
        return;
      }
      // Returning visitor - replay their conversation.
      msgs.forEach(function (m) {
        if (m.role === 'user') {
          addUserMessage(m.content);
        } else {
          var bot = addBotMessageContainer();
          renderMarkdown(m.content || '', bot.body);
        }
      });
    });
  }

  function open() {
    panel.classList.add('open');
    bubble.classList.add('open');
    showGreeting();
    setTimeout(function () { input.focus(); }, 200);
  }
  function close() {
    panel.classList.remove('open');
    bubble.classList.remove('open');
  }

  bubble.addEventListener('click', function () {
    if (panel.classList.contains('open')) close(); else open();
  });
  closeBtn.addEventListener('click', close);
  clearBtn.addEventListener('click', clearChat);

  if (EMBED_MODE) showGreeting();

  /* --------------------- composer behaviour ---------------------- */
  function autosize() {
    input.style.height = 'auto';
    input.style.height = Math.min(110, input.scrollHeight) + 'px';
  }
  input.addEventListener('input', autosize);
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      composer.dispatchEvent(new Event('submit', { cancelable: true }));
    }
  });
  composer.addEventListener('submit', function (e) {
    e.preventDefault();
    sendMessage(input.value);
  });
})();
