// Interfaz de chat para el backend (carpeta backend/). Sin dependencias ni paso de compilacion.
// Todo texto que viene de la API se inserta con textContent (nunca innerHTML): no hay inyeccion de HTML.
(function () {
  "use strict";

  var cfg = Object.assign({ apiBase: "", apiKey: "" }, window.CHAT_CONFIG || {});
  var I18N = window.CHAT_I18N;
  var DOC_RE = /^[A-Za-z0-9.\-]{4,32}$/;

  var state = {
    lang: "es",
    view: "login",
    sid: null,
    token: null,
    questions: [],
    attemptsLeft: 0,
    busy: false,
    handoffShown: {},
  };

  function $(id) { return document.getElementById(id); }

  // ---------------------------------------------------------------- i18n
  function t(key, vars) {
    var s = key.split(".").reduce(function (o, k) { return o && o[k]; }, I18N[state.lang]);
    if (typeof s !== "string") return key;
    return s.replace(/\{(\w+)\}/g, function (_, k) { return vars && vars[k] !== undefined ? vars[k] : "{" + k + "}"; });
  }

  function applyI18n() {
    document.documentElement.lang = I18N[state.lang].htmlLang;
    document.title = t("title");
    var text = {
      "app-title": "title", "app-badge": "badge", "lang-switch-label": "langLabel", "login-title": "loginTitle",
      "login-help": "loginHelp", "doc-label": "docLabel", "login-btn": "start", "challenge-title": "challengeTitle",
      "challenge-back": "back", "verify-btn": "verify", "send-btn": "send", "end-btn": "end",
      "ended-title": "endedTitle", "ended-text": "endedText", "restart-btn": "newChat", "disclaimer": "disclaimer",
      "input-label": "placeholder",
    };
    Object.keys(text).forEach(function (id) { $(id).textContent = t(text[id]); });
    $("input").placeholder = t("placeholder");
    $("lang-switch").value = state.lang;
    updateChallengeHelp();
    var typing = $("typing");
    if (typing) typing.querySelector(".visually-hidden").textContent = t("typing");
  }

  function updateChallengeHelp() {
    $("challenge-help").textContent = t("challengeHelp", { n: state.questions.length, left: state.attemptsLeft });
  }

  // ---------------------------------------------------------------- vistas y mensajes de error
  function setView(name) {
    state.view = name;
    ["login", "challenge", "chat", "ended"].forEach(function (v) { $("view-" + v).hidden = v !== name; });
    $("end-btn").hidden = name !== "chat";
    var focus = { login: "doc", challenge: "verify-btn", chat: "input", ended: "restart-btn" }[name];
    var el = $(focus);
    if (el && !el.disabled) el.focus();
  }

  function showBanner(text) {
    var b = $("banner");
    b.textContent = text;
    b.hidden = !text;
  }

  function errorText(error) {
    var msgs = I18N[state.lang].errors;
    var base = msgs[error.code] || msgs.generic;
    if (!msgs[error.code] && error.trace_id) base += " " + msgs.support + " " + error.trace_id;
    return base;
  }

  function setBusy(busy) {
    state.busy = busy;
    ["login-btn", "send-btn", "verify-btn"].forEach(function (id) { if (busy) $(id).disabled = true; });
    if (!busy) {
      $("login-btn").disabled = false;
      $("send-btn").disabled = false;
      refreshVerifyEnabled();
    }
  }

  // ---------------------------------------------------------------- API
  function api(method, path, body, auth) {
    var headers = { "Content-Type": "application/json" };
    if (cfg.apiKey) headers["X-API-Key"] = cfg.apiKey;
    if (auth && state.token) headers.Authorization = "Bearer " + state.token;
    return fetch(cfg.apiBase + path, { method: method, headers: headers, body: body ? JSON.stringify(body) : undefined })
      .then(function (res) {
        if (res.status === 204) return { ok: true, status: 204, data: null };
        return res.json().catch(function () { return null; }).then(function (data) {
          if (res.ok) return { ok: true, status: res.status, data: data };
          return { ok: false, status: res.status, error: (data && data.error) || { code: "generic" } };
        });
      })
      .catch(function () { return { ok: false, status: 0, error: { code: "network" } }; });
  }

  // ---------------------------------------------------------------- 1. documento
  function onLogin(ev) {
    ev.preventDefault();
    if (state.busy) return;
    showBanner("");
    var doc = $("doc").value.trim();
    var err = $("doc-error");
    if (!DOC_RE.test(doc)) {
      err.textContent = t("docInvalid");
      err.hidden = false;
      $("doc").setAttribute("aria-invalid", "true");
      return;
    }
    err.hidden = true;
    $("doc").removeAttribute("aria-invalid");
    setBusy(true);
    api("POST", "/v1/sessions", { document_number: doc, language: state.lang }, false).then(function (r) {
      setBusy(false);
      if (!r.ok) return showBanner(errorText(r.error));
      state.sid = r.data.session_id;
      state.token = r.data.token;
      state.questions = r.data.auth.questions;
      state.attemptsLeft = r.data.auth.attempts_left;
      renderQuestions();
      setView("challenge");
    });
  }

  // ---------------------------------------------------------------- 2. preguntas de seguridad
  function renderQuestions() {
    var box = $("questions");
    box.textContent = "";
    state.questions.forEach(function (q, i) {
      var fs = document.createElement("fieldset");
      fs.className = "question";
      var lg = document.createElement("legend");
      lg.textContent = (i + 1) + ". " + q.text;
      fs.appendChild(lg);
      q.options.forEach(function (o, j) {
        var label = document.createElement("label");
        label.className = "option";
        var input = document.createElement("input");
        input.type = "radio";
        input.name = q.id;
        input.value = o.id;
        input.id = q.id + "-" + j;
        input.addEventListener("change", refreshVerifyEnabled);
        var span = document.createElement("span");
        span.textContent = o.label;
        label.appendChild(input);
        label.appendChild(span);
        fs.appendChild(label);
      });
      box.appendChild(fs);
    });
    updateChallengeHelp();
    refreshVerifyEnabled();
  }

  function selectedAnswers() {
    return state.questions.map(function (q) {
      var checked = document.querySelector('input[name="' + q.id + '"]:checked');
      return checked ? { question_id: q.id, option_id: checked.value } : null;
    });
  }

  function refreshVerifyEnabled() {
    var all = state.questions.length > 0 && selectedAnswers().every(Boolean);
    $("verify-btn").disabled = state.busy || !all;
  }

  function onVerify(ev) {
    ev.preventDefault();
    if (state.busy) return;
    var answers = selectedAnswers();
    if (!answers.every(Boolean)) return;
    showBanner("");
    setBusy(true);
    api("POST", "/v1/sessions/" + encodeURIComponent(state.sid) + "/verify", { answers: answers }, true).then(function (r) {
      setBusy(false);
      if (!r.ok) {
        showBanner(errorText(r.error));
        if (["AUTH_LOCKED", "SESSION_EXPIRED", "INVALID_TOKEN"].indexOf(r.error.code) >= 0) resetSession(true);
        return;
      }
      if (r.data.status === "authenticated") {
        $("messages").textContent = "";
        setView("chat");
        addMessage("assistant", r.data.greeting || "");
        showSuggestions(r.data.suggested_replies || []);
        return;
      }
      state.questions = r.data.questions || [];
      state.attemptsLeft = r.data.attempts_left;
      showBanner(r.data.attempts_left === 1 ? t("wrongOne") : t("wrong", { left: r.data.attempts_left }));
      renderQuestions();
    });
  }

  // ---------------------------------------------------------------- 3. chat
  function addMessage(role, text, opts) {
    opts = opts || {};
    var box = $("messages");
    var item = document.createElement("article");
    item.className = "msg msg-" + role + (opts.error ? " msg-error" : "") + (opts.offer ? " msg-offer" : "");
    var who = document.createElement("span");
    who.className = "visually-hidden";
    who.textContent = (role === "user" ? t("you") : t("assistant")) + ": ";
    item.appendChild(who);
    if (opts.offer) {
      var tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = t("offer");
      item.appendChild(tag);
    }
    var p = document.createElement("p");
    p.textContent = text;
    item.appendChild(p);
    box.appendChild(item);
    box.scrollTop = box.scrollHeight;
    return item;
  }

  function addHandoff(ticket) {
    if (!ticket || state.handoffShown[ticket]) return;
    state.handoffShown[ticket] = true;
    var box = $("messages");
    var card = document.createElement("div");
    card.className = "handoff";
    card.setAttribute("role", "status");
    var s = document.createElement("span");
    s.textContent = t("handoff") + " ";
    var code = document.createElement("strong");
    code.textContent = ticket;
    card.appendChild(s);
    card.appendChild(code);
    box.appendChild(card);
    box.scrollTop = box.scrollHeight;
  }

  function showTyping(on) {
    var existing = $("typing");
    if (existing) existing.remove();
    if (!on) return;
    var el = document.createElement("div");
    el.id = "typing";
    el.className = "msg msg-assistant typing";
    var sr = document.createElement("span");
    sr.className = "visually-hidden";
    sr.textContent = t("typing");
    el.appendChild(sr);
    for (var i = 0; i < 3; i++) {
      var d = document.createElement("span");
      d.className = "dot";
      d.setAttribute("aria-hidden", "true");
      el.appendChild(d);
    }
    $("messages").appendChild(el);
    $("messages").scrollTop = $("messages").scrollHeight;
  }

  function scrollToEnd() {
    var box = $("messages");
    box.scrollTop = box.scrollHeight;
    // las sugerencias cambian la altura del chat despues de desplazar: se reaplica tras el siguiente dibujo
    window.requestAnimationFrame(function () { box.scrollTop = box.scrollHeight; });
  }

  function showSuggestions(list) {
    var box = $("suggestions");
    box.textContent = "";
    list.forEach(function (text) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "chip";
      b.textContent = text;
      b.addEventListener("click", function () { send(text); });
      box.appendChild(b);
    });
    scrollToEnd();
  }

  function send(text) {
    text = (text || "").trim();
    if (!text || state.busy) return;
    showBanner("");
    showSuggestions([]);
    addMessage("user", text);
    $("input").value = "";
    setBusy(true);
    $("input").disabled = true;
    showTyping(true);
    api("POST", "/v1/sessions/" + encodeURIComponent(state.sid) + "/messages", { message: text, language: state.lang }, true)
      .then(function (r) {
        showTyping(false);
        setBusy(false);
        $("input").disabled = false;
        if (!r.ok) {
          if (["SESSION_EXPIRED", "INVALID_TOKEN"].indexOf(r.error.code) >= 0) {
            resetSession(false);
            showBanner(errorText(r.error));
            return;
          }
          addMessage("assistant", errorText(r.error), { error: true });
          $("input").focus();
          return;
        }
        var d = r.data;
        addMessage("assistant", d.reply, { offer: d.proactive_offer });
        addHandoff(d.handoff_ticket);
        showSuggestions(d.suggested_replies || []);
        $("input").focus();
      });
  }

  // ---------------------------------------------------------------- cierre y reinicio
  function resetSession(backToLogin) {
    state.sid = null;
    state.token = null;
    state.questions = [];
    state.handoffShown = {};
    showTyping(false);
    showSuggestions([]);
    $("input").disabled = false;
    if (backToLogin) { $("doc").value = ""; setView("login"); } else { setView("ended"); }
  }

  function onEnd() {
    var sid = state.sid;
    if (sid) api("DELETE", "/v1/sessions/" + encodeURIComponent(sid), null, true);
    showBanner("");
    resetSession(false);
  }

  function onRestart() {
    showBanner("");
    $("messages").textContent = "";
    $("doc").value = "";
    setView("login");
  }

  // ---------------------------------------------------------------- arranque
  function init() {
    var saved = (navigator.language || "es").slice(0, 2);
    state.lang = saved === "pt" ? "pt" : "es";
    applyI18n();
    $("lang-switch").addEventListener("change", function (ev) {
      state.lang = ev.target.value;
      applyI18n();
    });
    $("login-form").addEventListener("submit", onLogin);
    $("challenge-form").addEventListener("submit", onVerify);
    $("challenge-back").addEventListener("click", function () { showBanner(""); resetSession(true); });
    $("composer").addEventListener("submit", function (ev) { ev.preventDefault(); send($("input").value); });
    $("end-btn").addEventListener("click", onEnd);
    $("restart-btn").addEventListener("click", onRestart);
    setView("login");
  }

  init();
})();
