/* ============================================================================
   Jarvis web UI — plain JavaScript, no build step.

   Talks to the backend over one WebSocket (protocol documented in
   jarvis/web/server.py). Reconnects automatically if the server restarts.

   Voice uses backend faster-whisper or the browser's Web Speech API, selected
   automatically by the server's hello message; see the Voice section below.
   ========================================================================== */

"use strict";

// ------------------------------------------------------------------ elements

const chat      = document.getElementById("chat");
const chatScroll= document.getElementById("chat-scroll");
const voiceHero = document.querySelector(".voice-hero");
const emptyEl   = document.getElementById("empty");
const form      = document.getElementById("form");
const input     = document.getElementById("input");
const sendBtn   = document.getElementById("send");
const micBtn    = document.getElementById("mic");
const attachBtn = document.getElementById("attach");
const imageInput= document.getElementById("image-input");
const imagePreviews = document.getElementById("image-previews");
const orbButton = document.getElementById("orb-button");
const orbCanvas = document.getElementById("orb-canvas");
const orbStatus = document.getElementById("orb-status");
const modeVoice = document.getElementById("mode-voice");
const modeText  = document.getElementById("mode-text");
const ring      = document.getElementById("ring");
const statusline= document.getElementById("statusline");
const brainChip = document.getElementById("brain-chip");
const ttsChip   = document.getElementById("tts-chip");
const safetyChip= document.getElementById("safety-chip");
const conn      = document.getElementById("conn");
const rail      = document.getElementById("rail");
const railToggle= document.getElementById("rail-toggle");
const railFeed  = document.getElementById("rail-feed");
const toast     = document.getElementById("toast");
const historyToggle = document.getElementById("history-toggle");
const historyOverlay= document.getElementById("history-overlay");
const historyContent= document.getElementById("history-content");
const historyBack   = document.getElementById("history-back");
const historyClose  = document.getElementById("history-close");
const historySearchInput = document.getElementById("history-search-input");
const historySearch = historySearchInput.closest(".history-search");
const newSessionBtn = document.getElementById("new-session");
const trackerToggle = document.getElementById("tracker-toggle");
const trackerOverlay= document.getElementById("tracker-overlay");
const trackerClose  = document.getElementById("tracker-close");
const trackerTabs   = document.getElementById("tracker-tabs");
const trackerForm   = document.getElementById("tracker-form");
const trackerText   = document.getElementById("tracker-text");
const trackerDue    = document.getElementById("tracker-due");
const trackerListName = document.getElementById("tracker-list-name");
const trackerContent= document.getElementById("tracker-content");
const memoryToggle  = document.getElementById("memory-toggle");
const memoryOverlay = document.getElementById("memory-overlay");
const memoryClose   = document.getElementById("memory-close");
const memoryForm    = document.getElementById("memory-form");
const memoryText    = document.getElementById("memory-text");
const memoryContent = document.getElementById("memory-content");
const universityToggle = document.getElementById("university-toggle");
const layout = document.querySelector(".layout");
const gucScreen = document.getElementById("guc-screen");
const gucBack = document.getElementById("guc-back");
const gucStatusBar = document.getElementById("guc-status-bar");
const gucGlobalActions = document.getElementById("guc-global-actions");
const gucTabs = document.getElementById("guc-tabs");
const gucContent = document.getElementById("guc-content");

// ------------------------------------------------------------------- helpers

function timestamp() {
  return new Date().toLocaleTimeString([], { hour12: false });
}

function loggedTime(value) {
  if (!value) return timestamp();
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) :
    date.toLocaleTimeString([], { hour12: false });
}

function scrollToBottom() {
  chatScroll.scrollTop = chatScroll.scrollHeight;
}

// Slack for "already at the bottom" — sub-pixel scroll math rarely lands on
// an exact 0, and this also covers the case where the user is close enough
// that snapping back down isn't disruptive.
const SCROLL_BOTTOM_SLACK = 48;

function isNearBottom() {
  return chatScroll.scrollHeight - chatScroll.scrollTop - chatScroll.clientHeight <= SCROLL_BOTTOM_SLACK;
}

// The orb shrinks smoothly and continuously as the user scrolls up and away
// from the live edge, fully collapsing once they reach the very top — giving
// maximum room to read older messages. Driven by --orb-scroll-ratio (1 at the
// bottom, 0 at the top). requestAnimationFrame keeps rapid wheel/touch scroll
// events from repeatedly measuring layout within the same painted frame.
//
// The orb's actual width/height shrink (not just a CSS transform) so its
// layout footprint genuinely reduces — a transform-only scale still occupies
// its full unscaled box, which then gets visibly clipped once the container
// collapses below that box's height. Base sizes mirror the CSS values in
// styles.css (180px desktop / 132px mobile, see the max-width:900px query);
// tracked via matchMedia rather than re-measured on every scroll tick.
const MOBILE_ORB_QUERY = window.matchMedia("(max-width: 900px)");
let baseOrbSize = MOBILE_ORB_QUERY.matches ? 132 : 180;
MOBILE_ORB_QUERY.addEventListener("change", (e) => { baseOrbSize = e.matches ? 132 : 180; });
const MIN_ORB_SCALE = 0.4; // never shrinks smaller than this fraction, until it fades out entirely

let scrollCompactFramePending = false;

function updateVoiceHeroForScroll() {
  scrollCompactFramePending = false;
  const maxScroll = chatScroll.scrollHeight - chatScroll.clientHeight;
  // No real scrolling possible (short conversation) — always full size.
  const ratio = maxScroll > 0 ? Math.min(1, Math.max(0, chatScroll.scrollTop / maxScroll)) : 1;
  voiceHero.style.setProperty("--orb-scroll-ratio", ratio.toFixed(3));
  const orbSize = baseOrbSize * (MIN_ORB_SCALE + (1 - MIN_ORB_SCALE) * ratio);
  orbButton.style.width = orbButton.style.height = `${orbSize.toFixed(1)}px`;
  // Status caption + mode toggle don't resize themselves, so collapse the
  // whole container's height too: orbSize always fits the (already-shrunk)
  // orb with no clipping, plus a share of the extra content (padding/status/
  // toggle, ~91px at full size) proportional to ratio, so it's a smooth
  // single collapse rather than a separate jump. Only recomputed on scroll
  // ticks (already rAF-throttled), never inside the 60fps orb render loop,
  // so this can't reproduce the forced-layout jank that caused the lag.
  const EXTRA_CONTENT_HEIGHT = 91; // padding + status caption + mode toggle, measured
  voiceHero.style.maxHeight = `${(orbSize + ratio * EXTRA_CONTENT_HEIGHT).toFixed(1)}px`;
  // Fully collapsed at the top: stop intercepting clicks meant for the chat
  // underneath (opacity 0 alone does not disable pointer events).
  voiceHero.classList.toggle("scroll-hidden", ratio <= 0.02);
}

chatScroll.addEventListener("scroll", () => {
  if (scrollCompactFramePending) return;
  scrollCompactFramePending = true;
  requestAnimationFrame(updateVoiceHeroForScroll);
});
updateVoiceHeroForScroll();

let toastTimer = null;
function showToast(text) {
  toast.textContent = text;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 2600);
}

// Small inline icons for the activity feed (stroke = currentColor).
const ICONS = {
  dot:   '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="3" fill="currentColor"/></svg>',
  bolt:  '<svg viewBox="0 0 16 16"><path d="M9 1 3 9h4l-1 6 6-8H8l1-6z" fill="currentColor"/></svg>',
  tool:  '<svg viewBox="0 0 16 16"><path d="M10 2a4 4 0 0 0-4 5L2 11l3 3 4-4a4 4 0 0 0 5-4l-2 2-2-1-1-2 2-2z" fill="currentColor"/></svg>',
  warn:  '<svg viewBox="0 0 16 16"><path d="M8 1 15 14H1L8 1zM7.3 6h1.4v4H7.3V6zm0 5h1.4v1.5H7.3V11z" fill="currentColor"/></svg>',
  cross: '<svg viewBox="0 0 16 16"><path d="M3 3l10 10M13 3L3 13" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
};

/** Add one line to the activity rail. kind: ""|"accent"|"dim"|"warn"|"err" */
function logActivity(text, kind = "", icon = "dot") {
  const li = document.createElement("li");
  if (kind) li.className = kind;
  li.innerHTML = `<span class="icon">${ICONS[icon] || ICONS.dot}</span>` +
                 `<span><span class="t">${timestamp()}</span></span>`;
  li.lastChild.appendChild(document.createTextNode(text));
  railFeed.appendChild(li);
  railFeed.scrollTop = railFeed.scrollHeight;
  // keep the feed from growing forever
  while (railFeed.children.length > 200) railFeed.removeChild(railFeed.firstChild);
}

// ------------------------------------------------------------------ messages

let streamingMsg = null;   // .msg element of the reply currently streaming
let streamingBody = null;  // its .msg-body, cached so tokens don't re-query the DOM
let gotTokens = false;
let restoredEventCount = 0;

function addMessage(who, text, time = null, images = []) {
  emptyEl.hidden = true;
  // Sending your own message always snaps to the bottom (you just typed it);
  // an incoming one only does if you were already there — otherwise it would
  // yank you away from whatever you scrolled up to read.
  const shouldStick = who === "you" || isNearBottom();
  const msg = document.createElement("div");
  msg.className = `msg ${who}`;
  const meta = document.createElement("div");
  meta.className = "msg-meta";
  meta.innerHTML = `<span class="who">${who === "you" ? "YOU" : "JARVIS"}</span>` +
                   `<span class="t">${loggedTime(time)}</span>`;
  const body = document.createElement("div");
  body.className = "msg-body";
  if (images.length) {
    const strip = document.createElement("div");
    strip.className = "message-images";
    for (const source of images) {
      const thumb = document.createElement("img");
      thumb.src = source;
      thumb.alt = "Attached image";
      strip.appendChild(thumb);
    }
    body.appendChild(strip);
  }
  body.appendChild(document.createTextNode(text));
  msg.append(meta, body);
  chat.appendChild(msg);
  if (shouldStick) scrollToBottom();
  return msg;
}


/** Add a passive confirmation result when replaying a saved conversation. */
function addHistoryNote(event, parent = chat) {
  const note = document.createElement("div");
  note.className = "msg history-note mono";
  const answer = event.approved ? "APPROVED" : "DECLINED";
  note.textContent = `[CONFIRM] ${event.description || event.action || "Action"} — ${answer}`;
  parent.appendChild(note);
}

function renderConversation(events) {
  for (const event of events) {
    if (event.type === "user_message") addMessage("you", event.text || "", event.ts);
    else if (event.type === "assistant_reply") addMessage("jarvis", event.text || "", event.ts);
    else if (event.type === "confirmation_answered") {
      emptyEl.hidden = true;
      addHistoryNote(event);
    }
  }
}

// Restore once before opening the live link, so subsequent socket messages
// always append after the saved conversation.
async function restoreCurrentConversation() {
  try {
    const index = await fetch("/api/sessions").then((response) => response.json());
    if (!index.current) return;
    const data = await fetch(`/api/sessions/${encodeURIComponent(index.current)}`)
      .then((response) => response.json());
    const events = Array.isArray(data.events) ? data.events : [];
    restoredEventCount = events.length;
    renderConversation(events);
  } catch (error) {
    // History is optional: a missing/corrupt log must never block live chat.
  }
}

function beginStreamingReply() {
  const msg = addMessage("jarvis", "");
  msg.classList.add("streaming");
  streamingMsg = msg;
  streamingBody = msg.querySelector(".msg-body");
  return msg;
}

// Streaming scroll checks are batched to once per animation frame — tokens
// can arrive many times a second, and reading scrollHeight/scrollTop right
// after each DOM mutation forces a synchronous layout every time (classic
// layout-thrashing), which is what showed up as flicker/stutter rather than
// an actual scroll jump.
let streamScrollFramePending = false;
function scheduleStreamScroll() {
  if (streamScrollFramePending) return;
  streamScrollFramePending = true;
  requestAnimationFrame(() => {
    streamScrollFramePending = false;
    if (isNearBottom()) scrollToBottom();
  });
}

function endStreamingReply(finalText) {
  const shouldStick = isNearBottom();
  if (streamingMsg) {
    streamingMsg.classList.remove("streaming");
    // trust the engine's final text (it may differ slightly from the chunks)
    if (finalText != null) streamingBody.textContent = finalText;
    streamingMsg = null;
    streamingBody = null;
  } else if (finalText) {
    addMessage("jarvis", finalText);   // reply that never streamed (e.g. Ollama)
  }
  gotTokens = false;
  setBusy(false);
  scheduleMicArm();
  if (shouldStick) scrollToBottom();
}

// --------------------------------------------------------- confirmation card

let activeConfirm = null;   // the card currently awaiting a click, if any

function answerConfirmation(approved) {
  if (!activeConfirm) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    // The click can't reach the server right now (mid-reconnect or the
    // connection just dropped). Silently doing nothing here used to look
    // exactly like a hang to the user — surface it and leave the card
    // clickable so they can retry once "link established" shows again.
    showToast("Connection isn't ready — wait for reconnect, then try again.");
    return;
  }
  ws.send(JSON.stringify({ type: "confirm_response", approved }));
  resolveConfirmCard(approved);
  logActivity(approved ? "action approved" : "action cancelled",
              approved ? "accent" : "dim", "dot");
}

function resolveConfirmCard(approved, note) {
  if (!activeConfirm) return;
  stopConfirmationVoice();
  const card = activeConfirm;
  activeConfirm = null;
  orbButton.classList.remove("confirm-pending");
  card.classList.add("resolved", approved ? "approved" : "declined");
  card.querySelectorAll("button").forEach((b) => (b.disabled = true));
  card.querySelector(".cc-state").textContent =
    note || (approved ? "Approved — running the action." : "Cancelled — nothing was changed.");
  statusline.textContent = "processing";
}

function showConfirmCard(description) {
  stopConfirmationVoice();
  emptyEl.hidden = true;
  const card = document.createElement("div");
  card.className = "confirm-card";
  card.innerHTML =
    `<div class="cc-head mono">CONFIRMATION REQUIRED</div>` +
    `<div class="cc-desc"></div>` +
    `<div class="cc-actions">` +
    `<button type="button" class="cc-yes">Confirm</button>` +
    `<button type="button" class="cc-no">Cancel</button>` +
    `</div>` +
    `<div class="cc-state mono"></div>`;
  card.querySelector(".cc-desc").textContent = description;
  chat.appendChild(card);
  activeConfirm = card;
  orbButton.classList.add("confirm-pending");

  card.querySelector(".cc-yes").addEventListener("click", () => answerConfirmation(true));
  card.querySelector(".cc-no").addEventListener("click", () => answerConfirmation(false));

  statusline.textContent = "awaiting your confirmation";
  logActivity(`confirmation requested — ${description}`, "warn", "warn");
  scrollToBottom();
  card.querySelector(".cc-no").focus();   // safe default gets the focus
  startConfirmationVoice(description);
}

// ---------------------------------------------------------------- busy state

let busy = false;
function setBusy(value) {
  busy = value;
  sendBtn.disabled = value;
  ring.classList.toggle("thinking", value);
  statusline.textContent = value ? "processing" : "standing by";
}

// ----------------------------------------------------------------- websocket

let ws = null;
let retryDelay = 1000;   // doubles up to 10s while the server is unreachable
let retryAttempts = 0;
// After this many failed attempts (~1+2+4+8+10+10 = 35s of backoff), stop
// silently pretending each retry might be the one — tell the user plainly
// instead of spinning "reconnecting" forever. Retries still continue in the
// background in case the server comes back on its own.
const RECONNECT_ATTEMPTS_BEFORE_GIVING_UP_QUIETLY = 6;
let intentionalShutdown = false;
let sttSource = "browser";

/** Enter the terminal shutdown state. Safe to call both before sending the
    request and again when the server confirms it. */
function showShuttingDown() {
  intentionalShutdown = true;
  showToast("Jarvis is shutting down…");
  // A normal toast fades quickly; shutdown is terminal, so leave this one
  // visible alongside the status line after the socket disappears.
  clearTimeout(toastTimer);
  statusline.textContent = "shutting down";
  input.disabled = true;
  sendBtn.disabled = true;
  micBtn.disabled = true;
  orbButton.disabled = true;
  modeVoice.disabled = true;
  modeText.disabled = true;
  if (recognition) stopListening();
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  speakingUtterances = 0;
  orbState = "idle";
  disarmMic();
  updateOrbStatus();
}

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);

  ws.onopen = () => {
    retryDelay = 1000;
    retryAttempts = 0;
    conn.classList.add("online");
    logActivity("link established", "accent", "bolt");
  };

  ws.onclose = () => {
    conn.classList.remove("online");
    if (intentionalShutdown) return;
    if (activeConfirm) resolveConfirmCard(false, "Connection lost — treated as cancelled.");
    if (busy) { endStreamingReply(null); }
    retryAttempts++;
    if (retryAttempts > RECONNECT_ATTEMPTS_BEFORE_GIVING_UP_QUIETLY) {
      statusline.textContent = "can't reconnect — try reloading the page";
      logActivity("can't reconnect after repeated attempts — try reloading", "err", "cross");
    } else {
      statusline.textContent = "link lost — reconnecting";
      logActivity("link lost — retrying", "err", "cross");
    }
    setTimeout(connect, retryDelay);
    retryDelay = Math.min(retryDelay * 2, 10000);
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);

    switch (msg.type) {
      case "hello": {
        const brainNames = { hosted: "GROQ", ollama: "OLLAMA", claude: "CLAUDE" };
        const label = brainNames[msg.brain] || msg.brain.toUpperCase();
        brainChip.textContent = `${label} · ${msg.model}`;
        ttsChip.textContent = msg.tts_engine ? `VOICE ${msg.tts_engine.toUpperCase()}` : "VOICE OFF";
        safetyChip.textContent = `SAFETY ${msg.safety.toUpperCase()}`;
        sttSource = msg.stt_source === "backend" ? "backend" : "browser";
        logActivity(`engine online · ${msg.brain} · ${msg.model}`, "dim", "dot");
        if (msg.briefing && restoredEventCount === 0) {
          addMessage("jarvis", msg.briefing);
          if (voiceFirstMode) speakSentence(msg.briefing);
        }
        break;
      }

      case "stt_result":
        backendTranscribing = false;
        setListening(false);
        if (msg.stt_source === "browser") sttSource = "browser";
        if (confirmationAwaitingBackendResult) {
          confirmationAwaitingBackendResult = false;
          // The card may have been clicked while transcription was in flight;
          // consume that stale result without letting it become a chat turn.
          if (activeConfirm && !msg.error) handleConfirmationTranscript(msg.text || "");
        } else if (msg.error) showToast(msg.error);
        else handleFinalTranscript(msg.text || "");
        break;

      case "token":
        if (!streamingMsg) beginStreamingReply();
        gotTokens = true;
        streamingBody.appendChild(document.createTextNode(msg.text));
        ttsPush(msg.text);
        scheduleStreamScroll();
        break;

      case "reply_done":
        ttsFinish(msg.text);
        endStreamingReply(msg.text);
        break;

      case "session_reset":
        streamingMsg = null;
        streamingBody = null;
        gotTokens = false;
        activeConfirm = null;
        orbButton.classList.remove("confirm-pending");
        chat.replaceChildren(emptyEl);
        emptyEl.hidden = false;
        railFeed.replaceChildren();
        closeHistory(false);
        setBusy(false);
        showToast("New session started");
        logActivity("new session started", "accent", "bolt");
        input.focus();
        break;

      case "session_resumed":
        streamingMsg = null;
        streamingBody = null;
        gotTokens = false;
        activeConfirm = null;
        orbButton.classList.remove("confirm-pending");
        chat.replaceChildren(emptyEl);
        emptyEl.hidden = false;
        railFeed.replaceChildren();
        renderConversation(Array.isArray(msg.events) ? msg.events : []);
        closeHistory(false);
        setBusy(false);
        showToast("Resumed session — continuing where you left off.");
        logActivity("session resumed", "accent", "bolt");
        input.focus();
        break;

      case "tool_start":
        statusline.textContent = `running ${msg.name}`;
        logActivity(`${msg.name} — ${msg.detail || "started"}`, "accent", "tool");
        break;

      case "tool_end":
        statusline.textContent = busy ? "processing" : "standing by";
        if (msg.ok) {
          logActivity(`${msg.name} done — ${msg.detail || ""}`, "dim", "dot");
        } else {
          logActivity(`${msg.name} failed — ${msg.detail || ""}`, "err", "cross");
        }
        break;

      case "confirmation_required":
        showConfirmCard(msg.description);
        break;

      case "navigate_app":
        switchAppSection(msg.section);
        break;

      case "confirmation_resolved":
        // Normally resolved by the click itself; this covers the timeout case
        // (and keeps the card honest if the server decided without us).
        if (activeConfirm) {
          resolveConfirmCard(msg.approved,
            msg.timed_out ? "Timed out with no answer — treated as cancelled." : null);
          if (msg.timed_out) logActivity("confirmation timed out — cancelled", "warn", "warn");
        }
        break;

      case "error":
        endStreamingReply(null);
        addMessage("jarvis", msg.message).classList.add("error");
        logActivity("engine error", "err", "warn");
        break;

      case "shutting_down":
        showShuttingDown();
        break;

      // Unknown message types are ignored so old pages don't break on new servers.
    }
  };
}

restoreCurrentConversation().finally(connect);

// ----------------------------------------------------------- history browser

let sessionIndex = null;
const historyDeleteTimers = new Set();
const HISTORY_SEARCH_DEBOUNCE_MS = 300;
let historySearchTimer = null;
let historySearchGeneration = 0;
let historySearchResults = [];

// Confirmation stays local to the button: the first click arms it for four
// seconds, and the second performs the irreversible request. View changes
// clear every pending timer so detached rows cannot retain stale state.
function clearHistoryDeleteTimers() {
  for (const timer of historyDeleteTimers) clearTimeout(timer);
  historyDeleteTimers.clear();
}

function makeSessionDeleteButton(session, onDeleted) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "session-row-delete";
  button.title = "Permanently delete this session";
  button.setAttribute("aria-label", "Delete session");
  button.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 4h10M6 4V2.5h4V4m-5 2v6m3-6v6m3-6v6M4 4l.6 10h6.8L12 4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  if (session.id === (sessionIndex && sessionIndex.current)) {
    button.disabled = true;
    button.title = "Can't delete the active session — start a new one first";
    button.setAttribute("aria-label", button.title);
    return button;
  }

  let confirmTimer = null;
  const reset = () => {
    if (confirmTimer !== null) {
      clearTimeout(confirmTimer);
      historyDeleteTimers.delete(confirmTimer);
      confirmTimer = null;
    }
    button.classList.remove("confirm");
    button.title = "Permanently delete this session";
    button.setAttribute("aria-label", "Delete session");
    button.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 4h10M6 4V2.5h4V4m-5 2v6m3-6v6m3-6v6M4 4l.6 10h6.8L12 4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  };
  button.addEventListener("click", async (event) => {
    event.stopPropagation();
    if (!button.classList.contains("confirm")) {
      button.classList.add("confirm");
      button.textContent = "Confirm?";
      button.title = "Click again to permanently delete";
      button.setAttribute("aria-label", button.title);
      confirmTimer = setTimeout(reset, 4000);
      historyDeleteTimers.add(confirmTimer);
      return;
    }

    reset();
    button.disabled = true;
    button.textContent = "Deleting…";
    try {
      const response = await fetch(`/api/sessions/${encodeURIComponent(session.id)}`, {
        method: "DELETE",
      });
      const result = await response.json();
      if (!response.ok || !result.deleted) throw new Error("delete refused");
      sessionIndex = await fetch("/api/sessions").then((item) => item.json());
      showToast("Session deleted.");
      onDeleted();
    } catch (error) {
      reset();
      button.disabled = false;
      showToast("Session could not be deleted.");
    }
  });
  return button;
}

function formatSessionDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Unknown time" : date.toLocaleString();
}

function shortText(value, limit = 180) {
  const text = typeof value === "string" ? value : JSON.stringify(value || {});
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function renderSessionList(data) {
  clearHistoryDeleteTimers();
  historyBack.hidden = true;
  historySearch.hidden = false;
  historyContent.replaceChildren();
  const sessions = Array.isArray(data.sessions) ? data.sessions : [];
  if (!sessions.length) {
    const empty = document.createElement("p");
    empty.className = "history-empty mono";
    empty.textContent = "No logged sessions yet.";
    historyContent.appendChild(empty);
    return;
  }
  for (const session of sessions) {
    const row = document.createElement("div");
    row.className = "session-row";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "session-row-open";
    const top = document.createElement("span");
    top.className = "session-row-top mono";
    top.textContent = formatSessionDate(session.start);
    if (session.id === data.current) {
      const live = document.createElement("span");
      live.className = "live-mark";
      live.textContent = "● live";
      top.appendChild(live);
    }
    const summary = document.createElement("span");
    summary.className = "session-summary";
    summary.textContent = session.summary || "(no messages)";
    const count = document.createElement("span");
    count.className = "session-count mono";
    count.textContent = `${Number(session.message_count) || 0} message${session.message_count === 1 ? "" : "s"}`;
    button.append(top, summary, count);
    button.addEventListener("click", () => showSession(session));
    const deleteButton = makeSessionDeleteButton(session, () => renderSessionList(sessionIndex));
    row.append(button, deleteButton);
    historyContent.appendChild(row);
  }
}

function appendHighlightedText(parent, text, query) {
  const source = String(text || "");
  const needle = query.toLocaleLowerCase();
  const comparable = source.toLocaleLowerCase();
  let cursor = 0;
  let position = comparable.indexOf(needle);
  while (position >= 0) {
    parent.appendChild(document.createTextNode(source.slice(cursor, position)));
    const mark = document.createElement("mark");
    mark.textContent = source.slice(position, position + query.length);
    parent.appendChild(mark);
    cursor = position + query.length;
    position = comparable.indexOf(needle, cursor);
  }
  parent.appendChild(document.createTextNode(source.slice(cursor)));
}

function renderSessionSearchResults(results, query) {
  clearHistoryDeleteTimers();
  historyBack.hidden = true;
  historySearch.hidden = false;
  historyContent.replaceChildren();
  if (!results.length) {
    const empty = document.createElement("p");
    empty.className = "history-empty mono";
    empty.textContent = "No matching sessions.";
    historyContent.appendChild(empty);
    return;
  }
  for (const session of results) {
    const row = document.createElement("div");
    row.className = "session-row";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "session-row-open";
    const top = document.createElement("span");
    top.className = "session-row-top mono";
    top.textContent = formatSessionDate(session.start);
    const count = document.createElement("span");
    count.className = "session-count mono";
    count.textContent = `${Number(session.match_count) || 0} matching event${session.match_count === 1 ? "" : "s"}`;
    const snippet = document.createElement("span");
    snippet.className = "session-search-snippet";
    appendHighlightedText(snippet, session.snippet, query);
    button.append(top, count, snippet);
    button.addEventListener("click", () => showSession(session));
    row.appendChild(button);
    historyContent.appendChild(row);
  }
}

async function showSession(session) {
  clearHistoryDeleteTimers();
  historyContent.innerHTML = '<p class="history-empty mono">LOADING TRANSCRIPT…</p>';
  historyBack.hidden = false;
  historySearch.hidden = true;
  try {
    const data = await fetch(`/api/sessions/${encodeURIComponent(session.id)}`)
      .then((response) => response.json());
    historyContent.replaceChildren();
    const controls = document.createElement("div");
    controls.className = "history-session-controls";
    if (session.id === (sessionIndex && sessionIndex.current)) {
      const active = document.createElement("span");
      active.className = "history-active mono";
      active.textContent = "This is your active session";
      controls.appendChild(active);
    } else {
      const resume = document.createElement("button");
      resume.type = "button";
      resume.className = "history-resume";
      resume.textContent = "Resume this session";
      resume.addEventListener("click", () => {
        if (!ws || ws.readyState !== WebSocket.OPEN) {
          showToast("Jarvis is reconnecting — try again in a moment.");
          return;
        }
        resume.disabled = true;
        resume.textContent = "Resuming…";
        ws.send(JSON.stringify({ type: "resume_session", id: session.id }));
        setBusy(true);
      });
      controls.appendChild(resume);
    }
    controls.appendChild(makeSessionDeleteButton(
      session, () => renderSessionList(sessionIndex)
    ));
    historyContent.appendChild(controls);
    for (const event of (Array.isArray(data.events) ? data.events : [])) {
      if (event.type === "user_message" || event.type === "assistant_reply") {
        const msg = document.createElement("div");
        msg.className = `history-msg ${event.type === "user_message" ? "you" : "jarvis"}`;
        const meta = document.createElement("div");
        meta.className = "msg-meta";
        meta.textContent = `${event.type === "user_message" ? "YOU" : "JARVIS"}  ${loggedTime(event.ts)}`;
        const body = document.createElement("div");
        body.className = "msg-body";
        body.textContent = event.text || "";
        msg.append(meta, body);
        historyContent.appendChild(msg);
      } else if (event.type === "confirmation_answered") {
        addHistoryNote(event, historyContent);
      } else if (event.type === "tool_call") {
        const tool = document.createElement("div");
        tool.className = "history-tool mono";
        tool.textContent = `tool: ${event.name || "unknown"}(${shortText(event.args, 140)})`;
        historyContent.appendChild(tool);
      }
    }
    if (!historyContent.children.length) {
      historyContent.innerHTML = '<p class="history-empty mono">No transcript events.</p>';
    }
  } catch (error) {
    historyContent.innerHTML = '<p class="history-empty mono">Could not load this session.</p>';
  }
}

async function openHistory() {
  closeTrackers(false);
  closeMemory(false);
  closeGuc(false);
  historyOverlay.hidden = false;
  document.body.classList.add("history-open");
  clearTimeout(historySearchTimer);
  historySearchGeneration += 1;
  historySearchInput.value = "";
  historySearchResults = [];
  historySearch.hidden = false;
  historyContent.innerHTML = '<p class="history-empty mono">LOADING SESSIONS…</p>';
  try {
    sessionIndex = await fetch("/api/sessions").then((response) => response.json());
    renderSessionList(sessionIndex);
  } catch (error) {
    historyContent.innerHTML = '<p class="history-empty mono">Could not load session history.</p>';
  }
  historyClose.focus();
}

function closeHistory(restoreFocus = true) {
  clearTimeout(historySearchTimer);
  historySearchGeneration += 1;
  historyOverlay.hidden = true;
  document.body.classList.remove("history-open");
  if (restoreFocus) historyToggle.focus();
}

historyToggle.addEventListener("click", openHistory);
newSessionBtn.addEventListener("click", () => {
  if (!gucScreen.hidden) closeGuc(false);
  if (busy) {
    showToast("Still working — wait for it to finish.");
    return;
  }
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    showToast("Jarvis is reconnecting — try again in a moment.");
    return;
  }
  ws.send(JSON.stringify({ type: "new_session" }));
  setBusy(true);
});
historyClose.addEventListener("click", closeHistory);
historyBack.addEventListener("click", () => {
  const query = historySearchInput.value.trim();
  if (query) renderSessionSearchResults(historySearchResults, query);
  else renderSessionList(sessionIndex || { sessions: [] });
});
historySearchInput.addEventListener("input", () => {
  clearTimeout(historySearchTimer);
  const query = historySearchInput.value.trim();
  const generation = ++historySearchGeneration;
  if (!query) {
    historySearchResults = [];
    renderSessionList(sessionIndex || { sessions: [] });
    return;
  }
  historySearchTimer = setTimeout(async () => {
    historyContent.innerHTML = '<p class="history-empty mono">SEARCHING SESSIONS…</p>';
    try {
      const response = await fetch(`/api/sessions/search?q=${encodeURIComponent(query)}`);
      const data = await response.json();
      if (!response.ok) throw new Error("search failed");
      if (generation !== historySearchGeneration) return;
      historySearchResults = Array.isArray(data.results) ? data.results : [];
      renderSessionSearchResults(historySearchResults, query);
    } catch (error) {
      if (generation !== historySearchGeneration) return;
      historyContent.innerHTML = '<p class="history-empty mono">Could not search session history.</p>';
    }
  }, HISTORY_SEARCH_DEBOUNCE_MS);
});
historyOverlay.addEventListener("click", (event) => {
  if (event.target === historyOverlay) closeHistory();
});
// --------------------------------------------------------------- trackers

const TRACKER_CATEGORIES = [
  ["todo", "To-dos"], ["calendar", "Calendar"],
  ["files", "Files & Projects"], ["custom", "Custom"],
];
let trackerCategory = "todo";
const trackerDeleteTimers = new Set();

function clearTrackerDeleteTimers() {
  for (const timer of trackerDeleteTimers) clearTimeout(timer);
  trackerDeleteTimers.clear();
}

function trackerDeleteButton(item) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "session-row-delete tracker-delete";
  button.title = "Delete tracker item";
  button.setAttribute("aria-label", button.title);
  button.textContent = "×";
  let confirmTimer = null;
  const reset = () => {
    if (confirmTimer !== null) {
      clearTimeout(confirmTimer);
      trackerDeleteTimers.delete(confirmTimer);
      confirmTimer = null;
    }
    button.classList.remove("confirm");
    button.textContent = "×";
  };
  button.addEventListener("click", async () => {
    if (!button.classList.contains("confirm")) {
      button.classList.add("confirm");
      button.textContent = "Confirm?";
      confirmTimer = setTimeout(reset, 4000);
      trackerDeleteTimers.add(confirmTimer);
      return;
    }
    reset();
    button.disabled = true;
    try {
      const response = await fetch(`/api/trackers/${encodeURIComponent(item.id)}`, { method: "DELETE" });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error("delete refused");
      showToast("Tracker item deleted.");
      await loadTrackers();
    } catch (error) {
      button.disabled = false;
      showToast("Tracker item could not be deleted.");
    }
  });
  return button;
}

function renderTrackerItems(items) {
  clearTrackerDeleteTimers();
  trackerContent.replaceChildren();
  if (!items.length) {
    trackerContent.innerHTML = '<p class="history-empty mono">No pending items here.</p>';
    return;
  }
  if (trackerCategory === "custom") {
    items.sort((a, b) => String(a.list_name || "Custom").localeCompare(String(b.list_name || "Custom")));
  }
  let lastList = null;
  for (const item of items) {
    if (trackerCategory === "custom" && item.list_name !== lastList) {
      const heading = document.createElement("h3");
      heading.className = "tracker-group mono";
      heading.textContent = item.list_name || "Custom";
      trackerContent.appendChild(heading);
      lastList = item.list_name;
    }
    const row = document.createElement("div");
    row.className = "session-row tracker-row";
    const complete = document.createElement("button");
    complete.type = "button";
    complete.className = "tracker-check";
    complete.setAttribute("aria-label", `Complete ${item.text || "item"}`);
    complete.textContent = "✓";
    complete.addEventListener("click", async () => {
      complete.disabled = true;
      try {
        const response = await fetch(`/api/trackers/${encodeURIComponent(item.id)}/complete`, { method: "POST" });
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error("complete refused");
        await loadTrackers();
      } catch (error) {
        complete.disabled = false;
        showToast("Tracker item could not be completed.");
      }
    });
    const detail = document.createElement("div");
    detail.className = "tracker-detail";
    const text = document.createElement("span");
    text.className = "tracker-item-text";
    text.textContent = item.text || "";
    detail.appendChild(text);
    if (item.due) {
      const due = document.createElement("span");
      due.className = "tracker-item-due mono";
      const date = new Date(item.due);
      due.textContent = `Due ${Number.isNaN(date.getTime()) ? item.due : date.toLocaleString()}`;
      detail.appendChild(due);
    }
    row.append(complete, detail, trackerDeleteButton(item));
    trackerContent.appendChild(row);
  }
}

async function loadTrackers() {
  trackerContent.innerHTML = '<p class="history-empty mono">LOADING ITEMS…</p>';
  try {
    const url = `/api/trackers?category=${encodeURIComponent(trackerCategory)}&status=pending`;
    const data = await fetch(url).then((response) => response.json());
    renderTrackerItems(Array.isArray(data.items) ? data.items : []);
  } catch (error) {
    trackerContent.innerHTML = '<p class="history-empty mono">Could not load trackers.</p>';
  }
}

function selectTrackerCategory(category) {
  trackerCategory = category;
  trackerListName.hidden = category !== "custom";
  for (const button of trackerTabs.querySelectorAll("button")) {
    button.setAttribute("aria-selected", String(button.dataset.category === category));
  }
  loadTrackers();
}

for (const [category, label] of TRACKER_CATEGORIES) {
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.category = category;
  button.textContent = label;
  button.setAttribute("aria-selected", String(category === trackerCategory));
  button.addEventListener("click", () => selectTrackerCategory(category));
  trackerTabs.appendChild(button);
}

async function openTrackers() {
  closeHistory(false);
  closeMemory(false);
  closeGuc(false);
  trackerOverlay.hidden = false;
  document.body.classList.add("tracker-open");
  await loadTrackers();
  trackerText.focus();
}

function closeTrackers(restoreFocus = true) {
  trackerOverlay.hidden = true;
  document.body.classList.remove("tracker-open");
  clearTrackerDeleteTimers();
  if (restoreFocus) trackerToggle.focus();
}

trackerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = trackerText.value.trim();
  if (!text) return;
  const payload = { category: trackerCategory, text };
  if (trackerDue.value) payload.due = trackerDue.value;
  if (trackerCategory === "custom" && trackerListName.value.trim()) {
    payload.list_name = trackerListName.value.trim();
  }
  try {
    const response = await fetch("/api/trackers", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok || !data.item) throw new Error("add refused");
    trackerText.value = "";
    trackerDue.value = "";
    await loadTrackers();
    trackerText.focus();
  } catch (error) {
    showToast("Tracker item could not be added.");
  }
});

trackerToggle.addEventListener("click", openTrackers);
trackerClose.addEventListener("click", closeTrackers);
trackerOverlay.addEventListener("click", (event) => {
  if (event.target === trackerOverlay) closeTrackers();
});

// ------------------------------------------------------------------ memory

const memoryDeleteTimers = new Set();

function clearMemoryDeleteTimers() {
  for (const timer of memoryDeleteTimers) clearTimeout(timer);
  memoryDeleteTimers.clear();
}

function memoryDeleteButton(fact) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "session-row-delete tracker-delete";
  button.title = "Delete remembered fact";
  button.setAttribute("aria-label", button.title);
  button.textContent = "×";
  let confirmTimer = null;
  const reset = () => {
    if (confirmTimer !== null) {
      clearTimeout(confirmTimer);
      memoryDeleteTimers.delete(confirmTimer);
      confirmTimer = null;
    }
    button.classList.remove("confirm");
    button.textContent = "×";
  };
  button.addEventListener("click", async () => {
    if (!button.classList.contains("confirm")) {
      button.classList.add("confirm");
      button.textContent = "Confirm?";
      confirmTimer = setTimeout(reset, 4000);
      memoryDeleteTimers.add(confirmTimer);
      return;
    }
    reset();
    button.disabled = true;
    try {
      const response = await fetch(`/api/memory/${encodeURIComponent(fact.id)}`, { method: "DELETE" });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error("delete refused");
      showToast("Remembered fact deleted.");
      await loadMemory();
    } catch (error) {
      button.disabled = false;
      showToast("Remembered fact could not be deleted.");
    }
  });
  return button;
}

function renderMemoryFacts(facts) {
  clearMemoryDeleteTimers();
  memoryContent.replaceChildren();
  if (!facts.length) {
    memoryContent.innerHTML = '<p class="history-empty mono">Nothing remembered yet.</p>';
    return;
  }
  for (const fact of facts) {
    const row = document.createElement("div");
    row.className = "session-row tracker-row";
    const detail = document.createElement("div");
    detail.className = "tracker-detail";
    const text = document.createElement("span");
    text.className = "tracker-item-text";
    text.textContent = fact.text || "";
    detail.appendChild(text);
    row.append(detail, memoryDeleteButton(fact));
    memoryContent.appendChild(row);
  }
}

async function loadMemory() {
  memoryContent.innerHTML = '<p class="history-empty mono">LOADING MEMORY…</p>';
  try {
    const data = await fetch("/api/memory").then((response) => response.json());
    renderMemoryFacts(Array.isArray(data.facts) ? data.facts : []);
  } catch (error) {
    memoryContent.innerHTML = '<p class="history-empty mono">Could not load memory.</p>';
  }
}

async function openMemory() {
  closeHistory(false);
  closeTrackers(false);
  closeGuc(false);
  memoryOverlay.hidden = false;
  document.body.classList.add("memory-open");
  await loadMemory();
  memoryText.focus();
}

function closeMemory(restoreFocus = true) {
  memoryOverlay.hidden = true;
  document.body.classList.remove("memory-open");
  clearMemoryDeleteTimers();
  if (restoreFocus) memoryToggle.focus();
}

memoryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = memoryText.value.trim();
  if (!text) return;
  try {
    const response = await fetch("/api/memory", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await response.json();
    if (!response.ok || !data.fact) throw new Error("add refused");
    memoryText.value = "";
    await loadMemory();
    memoryText.focus();
  } catch (error) {
    showToast("Remembered fact could not be added.");
  }
});

memoryToggle.addEventListener("click", openMemory);
memoryClose.addEventListener("click", closeMemory);
memoryOverlay.addEventListener("click", (event) => {
  if (event.target === memoryOverlay) closeMemory();
});

// ----------------------------------------------------------- GUC University

let gucPollTimer = null;
let gucPollStarted = 0;
let gucData = null;
let gucSection = "assignments";
const GUC_SECTIONS = [["assignments", "Assignments"], ["quizzes", "Quizzes"], ["exams", "Exams"], ["emails", "Emails"], ["announcements", "Announcements"], ["finances", "Finances"], ["completed", "Completed"]];
const GUC_CATEGORY_LABELS = { assignments: "Assignment", quizzes: "Quiz", exams: "Exam", emails: "Email", announcements: "Announcement", finances: "Finance", other: "Item" };

function gucReadableTime(value, fallback = "Never") {
  if (!value) return fallback;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

async function updateGucItem(item, field, value, button) {
  button.disabled = true;
  try {
    const response = await fetch(`/api/guc/item/${encodeURIComponent(item.id)}/${field}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [field]: value }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error("update refused");
    await loadGucStatus();
  } catch (error) {
    button.disabled = false;
    showToast("University item could not be updated.");
  }
}

function gucItem(item, section) {
  const finance = section === "finances";
  // Finance is fully read-only: no Seen/Acknowledge/Completed controls at
  // all — its displayed text only ever changes via the automatic
  // background re-scrape (jarvis/guc/sync.py's sync_finances). There is
  // no Delete anywhere on the University screen; Completed items are
  // moved to the Completed tab (see section below), never removed.
  const simple = section === "announcements"; // no Completed concept, but Seen still applies
  const wrapper = document.createElement("div");
  wrapper.className = `guc-item${item.seen ? " seen" : ""}`;
  const detail = document.createElement("div");
  detail.className = "guc-item-detail";
  if (section === "completed" && item.category) {
    const badge = document.createElement("span");
    badge.className = "guc-item-category mono";
    badge.textContent = (GUC_CATEGORY_LABELS[item.category] || item.category).toUpperCase();
    detail.appendChild(badge);
  }
  const text = document.createElement("span");
  text.className = "guc-item-text";
  text.textContent = item.text || "Untitled item";
  detail.appendChild(text);
  if (item.due) {
    const due = document.createElement("span");
    due.className = "guc-item-due mono";
    due.textContent = `DUE ${gucReadableTime(item.due, item.due)}`;
    detail.appendChild(due);
  }
  wrapper.appendChild(detail);
  if (finance) return wrapper; // no actions row at all for finance items

  const actions = document.createElement("div");
  actions.className = "guc-item-actions";
  const seen = Object.assign(document.createElement("button"), { type: "button", className: "guc-toggle-action", textContent: `Seen${item.seen ? " ✓" : ""}` });
  seen.setAttribute("aria-pressed", String(Boolean(item.seen)));
  seen.addEventListener("click", () => updateGucItem(item, "seen", !item.seen, seen));
  actions.appendChild(seen);
  if (!simple) {
    const isDone = item.status === "done";
    // In the Completed tab this is the only way back out — un-completing
    // moves the item back to its original tab (section_for_item() is
    // purely status-driven, see jarvis/guc/sync.py).
    const completed = Object.assign(document.createElement("button"), { type: "button", className: "guc-toggle-action", textContent: `Completed${isDone ? " ✓" : ""}` });
    completed.setAttribute("aria-pressed", String(isDone));
    completed.addEventListener("click", () => updateGucItem(item, "completed", !isDone, completed));
    actions.appendChild(completed);
  }
  wrapper.appendChild(actions);
  return wrapper;
}

async function handleGucReview(item, action, row) {
  const buttons = row.querySelectorAll("button");
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const response = await fetch(`/api/guc/review/${encodeURIComponent(item.id)}/${action}`, { method: "POST" });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || `${action} failed`);
    row.remove();
    if (action === "approve") showToast(data.calendar ? "Approved — added to Google Calendar" : "Approved");
    else showToast("Dismissed");
    await loadGucStatus();
  } catch (error) {
    buttons.forEach((button) => { button.disabled = false; });
    showToast(`GUC item could not be ${action === "approve" ? "approved" : "dismissed"}: ${error.message}`);
  }
}

function gucBulkButton(label, action, scope) {
  const button = Object.assign(document.createElement("button"), { type: "button", className: "guc-bulk-action", textContent: label });
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const response = await fetch(`/api/guc/bulk/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scope }) });
      const result = await response.json();
      if (!response.ok) throw new Error("bulk update failed");
      const count = Number(result.marked_seen ?? result.marked_completed) || 0;
      showToast(`${count} University item${count === 1 ? "" : "s"} updated.`);
      await loadGucStatus();
    } catch (error) { button.disabled = false; showToast("University items could not be updated."); }
  });
  return button;
}

function gucBulkActions(scope, global = false) {
  // Finance is fully read-only — no bulk actions apply to it at all.
  if (scope === "finances") return document.createElement("div");
  const actions = document.createElement("div");
  actions.className = "guc-bulk-actions";
  // global's own "all sections" already reads as plural, so it doesn't take
  // the leading "all" the per-section buttons need ("Read all this section"
  // reads oddly too — "in this section" flows better as a trailing clause).
  const readLabel = global ? "Read all sections" : "Read all in this section";
  actions.appendChild(gucBulkButton(readLabel, "seen", scope));
  // "Complete all" doesn't apply within the Completed tab itself (or
  // globally, since global already covers every other section) — only
  // Read-all is offered there, per spec.
  if (scope !== "completed") {
    const completeLabel = global ? "Complete all sections" : "Complete all in this section";
    actions.appendChild(gucBulkButton(completeLabel, "completed", scope));
  }
  return actions;
}

function appendGucReviewGroup(items, label) {
  if (!items?.length) return;
  const review = document.createElement("section"); review.className = "guc-review-section";
  review.appendChild(Object.assign(document.createElement("h3"), { className: "guc-section-title mono", textContent: `${label} (${items.length})` }));
  for (const item of items) {
    const row = document.createElement("div"); row.className = "guc-review-row";
    const text = Object.assign(document.createElement("span"), { className: "guc-item-text", textContent: item.text || "Untitled item" });
    const due = Object.assign(document.createElement("span"), { className: "guc-item-due mono", textContent: item.due ? `DUE ${gucReadableTime(item.due, item.due)}` : "NO DUE DATE" });
    const actions = document.createElement("div"); actions.className = "guc-review-actions";
    const approve = Object.assign(document.createElement("button"), { type: "button", className: "guc-approve", textContent: "Approve" });
    const dismiss = Object.assign(document.createElement("button"), { type: "button", className: "guc-dismiss", textContent: "Dismiss" });
    approve.addEventListener("click", () => handleGucReview(item, "approve", row)); dismiss.addEventListener("click", () => handleGucReview(item, "dismiss", row));
    actions.append(approve, dismiss); row.append(text, due, actions); review.appendChild(row);
  }
  gucContent.appendChild(review);
}

function renderGucSection() {
  gucContent.replaceChildren();
  if (!gucData?.credentials_configured) {
    gucContent.innerHTML = '<p class="guc-empty">GUC integration isn\'t set up — add GUC_USERNAME/GUC_PASSWORD to .env. See README.</p>';
    return;
  }
  const section = gucData.sections?.[gucSection] || { needs_review: [], items: [] };
  const heading = document.createElement("header");
  heading.className = "guc-section-head";
  const title = Object.assign(document.createElement("h2"), { className: "guc-section-heading", textContent: GUC_SECTIONS.find(([id]) => id === gucSection)?.[1] || gucSection });
  heading.append(title, gucBulkActions(gucSection));
  gucContent.appendChild(heading);
  if (gucSection === "assignments") appendGucReviewGroup(gucData.unclassified?.needs_review, "UNCLASSIFIED NEEDS REVIEW");
  appendGucReviewGroup(section.needs_review, "NEEDS REVIEW");
  const tracked = document.createElement("section"); tracked.className = "guc-tracked-section";
  tracked.appendChild(Object.assign(document.createElement("h3"), { className: "guc-section-title mono", textContent: `TRACKED ITEMS (${section.items?.length || 0})` }));
  for (const item of (section.items || [])) tracked.appendChild(gucItem(item, gucSection));
  if (!section.items?.length) tracked.appendChild(Object.assign(document.createElement("p"), { className: "guc-empty", textContent: "No tracked items in this section." }));
  gucContent.appendChild(tracked);
}

function renderGucStatus(data) {
  gucData = data;
  gucStatusBar.replaceChildren();
  const checked = Object.assign(document.createElement("span"), { className: "guc-last-checked mono", textContent: `LAST CHECKED  ${gucReadableTime(data.last_checked)}` });
  const logins = document.createElement("div"); logins.className = "guc-logins";
  for (const system of ["cms", "portal", "mail"]) {
    const login = document.createElement("span"); login.className = "guc-login mono";
    const state = data.login_status?.[system]; const dot = document.createElement("span");
    dot.className = `guc-dot${state === true ? " ok" : state === false ? " error" : ""}`;
    if (state === false && data.login_errors?.[system]) login.title = data.login_errors[system];
    login.append(dot, system.toUpperCase()); logins.appendChild(login);
  }
  const check = Object.assign(document.createElement("button"), { type: "button", className: "guc-check-now", textContent: data.in_progress ? "Checking…" : "Check now", disabled: Boolean(data.in_progress) });
  check.addEventListener("click", startGucCheck);
  gucStatusBar.append(checked, logins, check);
  gucGlobalActions.replaceChildren(gucBulkActions("all", true));
  renderGucSection();
}

async function loadGucStatus() {
  try {
    const response = await fetch("/api/guc/status");
    const data = await response.json();
    if (!response.ok) throw new Error("status failed");
    renderGucStatus(data);
    return data;
  } catch (error) {
    gucContent.innerHTML = '<p class="guc-empty">Could not load the GUC dashboard.</p>';
    return null;
  }
}

function stopGucPolling() {
  if (gucPollTimer !== null) clearInterval(gucPollTimer);
  gucPollTimer = null;
}

function pollGucStatus() {
  stopGucPolling();
  gucPollStarted = Date.now();
  gucPollTimer = setInterval(async () => {
    const data = await loadGucStatus();
    if (!data || !data.in_progress || Date.now() - gucPollStarted >= 180000) {
      stopGucPolling();
      if (data && !data.in_progress) await loadGucStatus();
    }
  }, 3000);
}

async function startGucCheck() {
  try {
    const response = await fetch("/api/guc/check", { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error("check request failed");
    if (!data.started) {
      showToast("A GUC check is already running.");
      return;
    }
    showToast("GUC check started…");
    await loadGucStatus();
    pollGucStatus();
  } catch (error) {
    showToast("GUC check could not be started.");
  }
}

async function openGuc() {
  if (!gucScreen.hidden) {
    closeGuc();
    return;
  }
  closeHistory(false);
  closeTrackers(false);
  closeMemory(false);
  layout.hidden = true;
  gucScreen.hidden = false;
  await loadGucStatus();
  gucBack.focus();
}

function closeGuc(restoreFocus = true) {
  gucScreen.hidden = true;
  layout.hidden = false;
  stopGucPolling();
  if (restoreFocus) universityToggle.focus();
}

universityToggle.addEventListener("click", openGuc);

// Driven by the server's "navigate_app" WS message (jarvis/tools/apps.py's
// navigate_app tool, via jarvis/web/bridge.py) — lets the brain switch the
// web UI's own screens by name instead of only being able to describe them.
// Keep the section keys in sync with APP_SECTIONS in jarvis/tools/apps.py.
function switchAppSection(section) {
  switch (section) {
    case "chat":
      closeHistory(false);
      closeTrackers(false);
      closeMemory(false);
      closeGuc(false);
      break;
    case "university":
      openGuc();
      break;
    case "history":
      openHistory();
      break;
    case "trackers":
      openTrackers();
      break;
    case "memory":
      openMemory();
      break;
    case "activity":
      rail.classList.remove("collapsed");
      railToggle.setAttribute("aria-expanded", "true");
      break;
    default:
      showToast(`Jarvis tried to open an unknown screen: ${section}`);
  }
}
gucBack.addEventListener("click", closeGuc);
for (const [section, label] of GUC_SECTIONS) {
  const button = Object.assign(document.createElement("button"), { type: "button", textContent: label });
  button.dataset.section = section;
  button.setAttribute("aria-selected", String(section === gucSection));
  button.addEventListener("click", () => {
    gucSection = section;
    for (const tab of gucTabs.querySelectorAll("button")) tab.setAttribute("aria-selected", String(tab.dataset.section === section));
    renderGucSection();
  });
  gucTabs.appendChild(button);
}
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!historyOverlay.hidden) closeHistory();
  else if (!trackerOverlay.hidden) closeTrackers();
  else if (!memoryOverlay.hidden) closeMemory();
  else if (!gucScreen.hidden) closeGuc();
});

// --------------------------------------------------------------------- input

const MAX_PENDING_IMAGES = 4;
const MAX_IMAGE_DIMENSION = 1600;
let pendingImages = [];

function updateImagePreviews() {
  imagePreviews.replaceChildren();
  pendingImages.forEach((source, index) => {
    const preview = document.createElement("div");
    preview.className = "image-preview";
    const image = document.createElement("img");
    image.src = source;
    image.alt = `Attached image ${index + 1}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "image-remove";
    remove.setAttribute("aria-label", `Remove attached image ${index + 1}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      pendingImages.splice(index, 1);
      updateImagePreviews();
    });
    preview.append(image, remove);
    imagePreviews.appendChild(preview);
  });
  attachBtn.disabled = pendingImages.length >= MAX_PENDING_IMAGES;
}

function downscaleImage(file) {
  return new Promise((resolve, reject) => {
    const source = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      const scale = Math.min(1, MAX_IMAGE_DIMENSION / Math.max(image.width, image.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(image.width * scale));
      canvas.height = Math.max(1, Math.round(image.height * scale));
      canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(source);
      resolve(canvas.toDataURL("image/jpeg", 0.85));
    };
    image.onerror = () => { URL.revokeObjectURL(source); reject(new Error("invalid image")); };
    image.src = source;
  });
}

async function addImageFiles(files) {
  const candidates = Array.from(files).filter((file) => file.type.startsWith("image/"));
  const room = MAX_PENDING_IMAGES - pendingImages.length;
  if (candidates.length > room) showToast(`You can attach up to ${MAX_PENDING_IMAGES} images.`);
  for (const file of candidates.slice(0, room)) {
    try { pendingImages.push(await downscaleImage(file)); }
    catch (_error) { showToast(`${file.name || "That image"} could not be attached.`); }
  }
  updateImagePreviews();
}

attachBtn.addEventListener("click", () => {
  if (pendingImages.length >= MAX_PENDING_IMAGES) {
    showToast(`You can attach up to ${MAX_PENDING_IMAGES} images.`);
    return;
  }
  imageInput.click();
});
imageInput.addEventListener("change", async () => {
  await addImageFiles(imageInput.files);
  imageInput.value = "";
});
input.addEventListener("paste", (event) => {
  const files = Array.from(event.clipboardData?.items || [])
    .filter((item) => item.type.startsWith("image/"))
    .map((item) => item.getAsFile()).filter(Boolean);
  if (files.length) {
    event.preventDefault();
    addImageFiles(files);
  }
});

/** Send one user message. fromVoice: true when it was dictated — the reply
    will then be spoken aloud as it streams. */
function sendMessage(text, fromVoice = false) {
  if ((!text && !pendingImages.length) || busy || !ws || ws.readyState !== WebSocket.OPEN) return false;
  if ("speechSynthesis" in window) speechSynthesis.cancel(); // stop any earlier reply
  voiceTurn = fromVoice;
  ttsBuffer = "";
  const images = pendingImages.slice();
  addMessage("you", text, null, images);
  ws.send(JSON.stringify({ type: "user_message", text, images }));
  pendingImages = [];
  updateImagePreviews();
  input.value = "";
  setBusy(true);
  return true;
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  sendMessage(input.value.trim(), false);
});

/* ============================================================================
   Voice + audio-reactive orb — built on browser Web APIs (best in
   Chrome/Edge; both APIs are free and run without extra dependencies).

   Mic:     hello.stt_source selects backend faster-whisper (one MediaRecorder
            binary blob per utterance) or browser SpeechRecognition. Both feed
            the same final-transcript and voice-control logic.
   Replies: speechSynthesis — replies to VOICE messages are spoken aloud,
            sentence by sentence while the rest is still streaming in.
            (Typed messages stay silent on purpose.)

   Orb:     microphone motion uses a second getUserMedia stream connected to
            an AnalyserNode. speechSynthesis exposes no capturable audio, so
            reply motion uses boundary pulses with a synthetic timer fallback.

   ========================================================================== */

const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;    // active SpeechRecognition instance while listening
let mediaRecorder = null;
let backendTranscribing = false;
let backendRecordStartedAt = 0;
let backendSpeechDetected = false;
let backendSilenceSince = null;
let voiceTurn = false;     // current turn came from the mic → speak the reply
let ttsBuffer = "";        // streamed text not yet split into a spoken sentence
let orbState = "idle";
let currentAmplitude = 0;
let speechPulse = 0;
let micStream = null;
let micAudioContext = null;
let micAnalyser = null;
let micFrequencyData = null;
let micRequestId = 0;
let micArmPromise = null;
let speakingUtterances = 0;
let micArmTimer = null;
let vadAboveSince = null;
let vadCooldownUntil = 0;
let voiceFirstMode = localStorage.getItem("jarvis-voice-first") !== "false";
let confirmationRecognition = null;
let confirmationRecorder = null;
let confirmationPromptUtterance = null;
let confirmationAttempt = 0;
let confirmationAwaitingBackendResult = false;

const VAD_AMPLITUDE_THRESHOLD = 0.05;
const VAD_SUSTAIN_MS = 150;
// Trimmed from 1400ms/1200ms: still well above a natural speech pause, but
// shaves real perceived latency off every turn without reintroducing the
// "cuts me off mid-sentence" problem these debounces were built to fix.
const END_OF_SPEECH_SILENCE_MS = 1000;
const MAX_UTTERANCE_MS = 25000;
const POST_SPEECH_COOLDOWN_MS = 500;
const STT_SILENCE_END_MS = 900;
const STT_MAX_RECORD_MS = 15000;
const STT_START_TIMEOUT_MS = 6000;
const CONFIRM_SILENCE_END_MS = 900;
const CONFIRM_MAX_LISTEN_MS = 8000;
const CONFIRM_START_TIMEOUT_MS = 5000;

const CONFIRM_YES_PREFIX = /^(?:yes|yeah|yep|yup|confirm|correct|sure|go ahead|do it|proceed)\b/;
const CONFIRM_NO_PREFIX = /^(?:no|nope|nah|cancel|don't|dont|stop|negative)\b/;

function confirmationVoiceAvailable() {
  // Deliberately NOT gated on voiceFirstMode: answering an already-visible
  // confirmation card is a direct response to something the user just
  // triggered, not unprompted ambient listening — it should work by voice
  // whether or not hands-free mode is on. (Previously this required
  // voiceFirstMode, which silently meant "say yes" did literally nothing —
  // no listener was ever started — for anyone using Jarvis primarily by
  // typing with voice-first off. That was the real bug, not a missing
  // voice-to-confirm wiring; the wiring already existed, it just never ran.)
  return "speechSynthesis" in window &&
    typeof SpeechSynthesisUtterance !== "undefined" &&
    (sttSource === "backend" ? typeof MediaRecorder !== "undefined" : Boolean(SpeechRec));
}

function stopConfirmationVoice() {
  const session = confirmationRecognition;
  confirmationRecognition = null;
  if (session) {
    if (session._jarvisCancel) session._jarvisCancel();
    session.onend = null;
    session.onresult = null;
    session.onerror = null;
    try { session.stop(); } catch (e) { /* already stopped */ }
  }
  if (confirmationRecorder && confirmationRecorder.state !== "inactive") {
    confirmationRecorder._jarvisSend = false;
    try { confirmationRecorder.stop(); } catch (e) { /* already stopped */ }
  }
  confirmationRecorder = null;
  // If backend audio is already being transcribed, leave this true so the
  // eventual result is consumed and discarded rather than sent as chat.
  if (confirmationPromptUtterance) {
    confirmationPromptUtterance.onend = null;
    confirmationPromptUtterance.onerror = null;
    confirmationPromptUtterance = null;
  }
  if (orbState === "listening") setListening(false);
}

function handleConfirmationTranscript(text) {
  if (!activeConfirm) return;
  const answer = (text || "").trim().toLowerCase();
  if (CONFIRM_YES_PREFIX.test(answer)) {
    answerConfirmation(true);
  } else if (CONFIRM_NO_PREFIX.test(answer)) {
    answerConfirmation(false);
  } else {
    showToast("Didn't catch a clear yes or no — please say it again, or click a button.");
    if (confirmationAttempt < 2) startConfirmationAnswerListen();
  }
}

async function startConfirmationBackendListen() {
  await ensureMicArmed();
  if (!activeConfirm || !micStream || mediaRecorder || backendTranscribing) return;
  let options;
  if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) options = { mimeType: "audio/webm;codecs=opus" };
  else if (MediaRecorder.isTypeSupported("audio/webm")) options = { mimeType: "audio/webm" };
  let recorder;
  try {
    recorder = options ? new MediaRecorder(micStream, options) : new MediaRecorder(micStream);
  } catch (error) { return; }
  const chunks = [];
  mediaRecorder = recorder;
  confirmationRecorder = recorder;
  recorder._jarvisSend = false;
  recorder._jarvisConfirmation = true;
  recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
  recorder.onerror = () => {
    if (mediaRecorder === recorder) mediaRecorder = null;
    if (confirmationRecorder === recorder) confirmationRecorder = null;
    setListening(false);
  };
  recorder.onstop = async () => {
    const shouldSend = recorder._jarvisSend;
    if (mediaRecorder === recorder) mediaRecorder = null;
    if (confirmationRecorder === recorder) confirmationRecorder = null;
    setListening(false);
    if (!shouldSend || !activeConfirm) return;
    confirmationAwaitingBackendResult = true;
    backendTranscribing = true;
    try {
      const buffer = await new Blob(chunks, { type: recorder.mimeType }).arrayBuffer();
      if (!ws || ws.readyState !== WebSocket.OPEN) throw new Error("socket closed");
      ws.send(buffer);
    } catch (error) {
      backendTranscribing = false;
      confirmationAwaitingBackendResult = false;
    }
  };
  backendRecordStartedAt = performance.now();
  backendSpeechDetected = false;
  backendSilenceSince = null;
  recorder.start();
  setListening(true);
}

function startConfirmationBrowserListen() {
  if (!SpeechRec || confirmationRecognition || recognition) return;
  const session = new SpeechRec();
  confirmationRecognition = session;
  session.lang = "en-US";
  session.interimResults = true;
  session.continuous = true;
  session.maxAlternatives = 1;
  let finalTranscript = "";
  let interim = "";
  let finished = false;
  let silenceTimer = null;
  let maxTimer = null;
  const clearTimers = () => { clearTimeout(silenceTimer); clearTimeout(maxTimer); };
  session._jarvisCancel = () => { finished = true; clearTimers(); };
  const finalize = () => {
    if (finished) return;
    finished = true;
    clearTimers();
    const text = `${finalTranscript} ${interim}`.trim();
    session.onend = null;
    try { session.stop(); } catch (e) { /* already stopped */ }
    if (confirmationRecognition === session) confirmationRecognition = null;
    setListening(false);
    handleConfirmationTranscript(text);
  };
  session.onresult = (event) => {
    interim = "";
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const result = event.results[i][0].transcript.trim();
      if (event.results[i].isFinal) finalTranscript = `${finalTranscript} ${result}`.trim();
      else interim = result;
    }
    clearTimeout(silenceTimer);
    silenceTimer = setTimeout(finalize, CONFIRM_SILENCE_END_MS);
  };
  session.onerror = () => {
    if (finished) return;
    finished = true;
    clearTimers();
    if (confirmationRecognition === session) confirmationRecognition = null;
    setListening(false);
  };
  session.onend = finalize;
  maxTimer = setTimeout(finalize, CONFIRM_MAX_LISTEN_MS);
  try { session.start(); } catch (error) { session.onerror(); return; }
  setListening(true);
}

function startConfirmationAnswerListen() {
  if (!activeConfirm || !confirmationVoiceAvailable()) return;
  confirmationAttempt += 1;
  if (sttSource === "backend") startConfirmationBackendListen();
  else startConfirmationBrowserListen();
}

function startConfirmationVoice(description) {
  if (!confirmationVoiceAvailable()) return;
  confirmationAttempt = 0;
  const cleaned = stripMarkdown(description || "").trim();
  const utterance = new SpeechSynthesisUtterance(
    `${cleaned}. Say yes to confirm, or no to cancel.`
  );
  confirmationPromptUtterance = utterance;
  utterance.rate = 1.05;

  // speechSynthesis is a known source of silent failures across browsers
  // (a hung utterance that never fires onstart/onend/onerror — documented
  // Chrome behavior when a tab loses focus, among other cases). Previously
  // listening ONLY started from utterance.onend, so if speech synthesis
  // ever silently hung, the mic never activated and "say yes" did nothing
  // with zero visible sign of failure. This fallback timer guarantees
  // listening starts regardless of whether the utterance ever resolves.
  let started = false;
  const beginListeningOnce = () => {
    if (started || confirmationPromptUtterance !== utterance || !activeConfirm) return;
    started = true;
    confirmationPromptUtterance = null;
    orbState = "idle";
    updateOrbStatus();
    startConfirmationAnswerListen();
  };
  const fallbackTimer = setTimeout(beginListeningOnce, 6000);

  utterance.onstart = () => {
    if (confirmationPromptUtterance !== utterance || !activeConfirm) return;
    disarmMic();
    orbState = "speaking";
    updateOrbStatus();
  };
  utterance.onend = () => {
    clearTimeout(fallbackTimer);
    beginListeningOnce();
  };
  utterance.onerror = () => {
    clearTimeout(fallbackTimer);
    beginListeningOnce();
  };
  try {
    speechSynthesis.speak(utterance);
  } catch (error) {
    clearTimeout(fallbackTimer);
    beginListeningOnce();
  }
}

function updateOrbStatus() {
  if (!voiceFirstMode && orbState === "idle") orbStatus.textContent = "Muted";
  else if (orbState === "listening") orbStatus.textContent = "Listening…";
  else if (orbState === "speaking") orbStatus.textContent = "Speaking…";
  else orbStatus.textContent = "Tap to talk";
}

function setVoiceMode(enabled) {
  voiceFirstMode = enabled;
  localStorage.setItem("jarvis-voice-first", String(enabled));
  modeVoice.setAttribute("aria-pressed", String(enabled));
  modeText.setAttribute("aria-pressed", String(!enabled));
  if (enabled) scheduleMicArm();
  else {
    stopConfirmationVoice();
    disarmMic();
  }
  updateOrbStatus();
}

modeVoice.addEventListener("click", () => setVoiceMode(true));
modeText.addEventListener("click", () => setVoiceMode(false));
setVoiceMode(voiceFirstMode);

async function startMicAnalyser() {
  if (micStream) return;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;
  const requestId = ++micRequestId;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (requestId !== micRequestId) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) { stream.getTracks().forEach((track) => track.stop()); return; }
    micStream = stream;
    micAudioContext = new AudioContextClass();
    micAnalyser = micAudioContext.createAnalyser();
    micAnalyser.fftSize = 128;
    micAnalyser.smoothingTimeConstant = 0.72;
    micFrequencyData = new Uint8Array(micAnalyser.frequencyBinCount);
    micAudioContext.createMediaStreamSource(stream).connect(micAnalyser);
  } catch (error) {
    // SpeechRecognition reports permission failures; this independent visual
    // stream may fail without affecting transcription.
  }
}

function ensureMicArmed() {
  if (micStream) return Promise.resolve();
  if (micArmPromise) return micArmPromise;
  const request = startMicAnalyser();
  const trackedRequest = request.finally(() => {
    if (micArmPromise === trackedRequest) micArmPromise = null;
  });
  micArmPromise = trackedRequest;
  return micArmPromise;
}

function stopMicAnalyser() {
  micRequestId += 1;
  if (micStream) micStream.getTracks().forEach((track) => track.stop());
  if (micAudioContext) micAudioContext.close().catch(() => {});
  micStream = null; micAudioContext = null; micAnalyser = null; micFrequencyData = null;
}

function disarmMic() {
  clearTimeout(micArmTimer);
  micArmTimer = null;
  vadAboveSince = null;
  stopMicAnalyser();
}

function scheduleMicArm() {
  clearTimeout(micArmTimer);
  vadAboveSince = null;
  vadCooldownUntil = performance.now() + POST_SPEECH_COOLDOWN_MS;
  if (!voiceFirstMode || document.hidden) return;
  micArmTimer = setTimeout(() => {
    micArmTimer = null;
    if (voiceFirstMode && !document.hidden && orbState === "idle" && !speakingUtterances) {
      ensureMicArmed();
    }
  }, POST_SPEECH_COOLDOWN_MS);
}

// Cached instead of read from getBoundingClientRect() every animation frame:
// that call forces a synchronous layout flush, and doing it 60x/sec (every
// single canvas frame, whether or not anything is actually changing) was the
// real cause of the UI lagging while scrolling — the orb's own size query was
// fighting the scroll's layout/paint work on every frame. A ResizeObserver
// only fires when the button's rendered size actually changes.
let cachedOrbSize = 180;
new ResizeObserver((entries) => {
  const width = entries[entries.length - 1]?.contentRect?.width;
  if (width) cachedOrbSize = width;
}).observe(orbButton);

function drawOrb(time) {
  const size = Math.max(1, cachedOrbSize);
  const dpr = Math.min(window.devicePixelRatio || 1, 3);
  const pixels = Math.round(size * dpr);
  if (orbCanvas.width !== pixels || orbCanvas.height !== pixels) {
    orbCanvas.width = pixels; orbCanvas.height = pixels;
  }
  const ctx = orbCanvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, size, size);
  const t = time / 1000;
  let target = 0.08 + 0.035 * Math.sin(t * 1.8);
  let micAmplitude = 0;
  if (micAnalyser) {
    micAnalyser.getByteFrequencyData(micFrequencyData);
    micAmplitude = micFrequencyData.reduce((sum, value) => sum + value, 0) / (micFrequencyData.length * 255);
    if (orbState === "listening") target = micAmplitude;
  } else if (orbState === "speaking") {
    target = Math.max(0.08, speechPulse);
    speechPulse *= 0.9;
  }
  currentAmplitude += (target - currentAmplitude) * 0.16;

  // Backend recording reuses this already-computed amplitude: no second
  // analyser read or parallel VAD loop is needed.
  if (mediaRecorder && mediaRecorder.state === "recording") {
    const elapsed = time - backendRecordStartedAt;
    const confirmationRecording = mediaRecorder._jarvisConfirmation;
    const silenceEndMs = confirmationRecording ? CONFIRM_SILENCE_END_MS : STT_SILENCE_END_MS;
    const startTimeoutMs = confirmationRecording ? CONFIRM_START_TIMEOUT_MS : STT_START_TIMEOUT_MS;
    const maxRecordMs = confirmationRecording ? CONFIRM_MAX_LISTEN_MS : STT_MAX_RECORD_MS;
    if (micAmplitude >= VAD_AMPLITUDE_THRESHOLD) {
      backendSpeechDetected = true;
      backendSilenceSince = null;
    } else if (backendSpeechDetected) {
      if (backendSilenceSince === null) backendSilenceSince = time;
      if (time - backendSilenceSince >= silenceEndMs) stopBackendRecording(true);
    } else if (elapsed >= startTimeoutMs) {
      stopBackendRecording(false);
    }
    if (elapsed >= maxRecordMs) stopBackendRecording(backendSpeechDetected);
  }

  const voiceRecognizerAvailable = sttSource === "backend" ?
    typeof MediaRecorder !== "undefined" : Boolean(SpeechRec);
  const vadReady = orbState === "idle" && Boolean(micStream) && voiceFirstMode &&
    !busy && !backendTranscribing && !activeConfirm && time >= vadCooldownUntil &&
    !recognition && !mediaRecorder && voiceRecognizerAvailable &&
    ws && ws.readyState === WebSocket.OPEN;
  if (vadReady && micAmplitude >= VAD_AMPLITUDE_THRESHOLD) {
    if (vadAboveSince === null) vadAboveSince = time;
    if (time - vadAboveSince >= VAD_SUSTAIN_MS) {
      vadAboveSince = null;
      startListening();
    }
  } else {
    vadAboveSince = null;
  }
  const rgb = orbButton.classList.contains("confirm-pending") ? "252,191,63" : "34,211,238";
  const center = size / 2;
  const radius = size * (0.275 + currentAmplitude * 0.025);
  const gradient = ctx.createRadialGradient(center - radius * 0.2, center - radius * 0.25, 2, center, center, radius * 1.25);
  gradient.addColorStop(0, `rgba(${rgb},0.95)`);
  gradient.addColorStop(0.52, `rgba(${rgb},0.38)`);
  gradient.addColorStop(1, `rgba(${rgb},0)`);
  ctx.fillStyle = gradient;
  ctx.beginPath(); ctx.arc(center, center, radius * 1.25, 0, Math.PI * 2); ctx.fill();
  ctx.lineWidth = Math.max(1, size * 0.008); ctx.lineCap = "round";
  for (let i = 0; i < 44; i += 1) {
    const angle = (i / 44) * Math.PI * 2 - Math.PI / 2;
    const bin = micFrequencyData && orbState === "listening" ? micFrequencyData[i % micFrequencyData.length] / 255 : null;
    const variation = bin === null ? (0.45 + 0.55 * Math.sin(t * 3.1 + i * 0.72)) * currentAmplitude : bin;
    const inner = radius * 1.12;
    const outer = inner + size * (0.025 + variation * 0.17);
    ctx.strokeStyle = `rgba(${rgb},${0.3 + variation * 0.7})`;
    ctx.beginPath();
    ctx.moveTo(center + Math.cos(angle) * inner, center + Math.sin(angle) * inner);
    ctx.lineTo(center + Math.cos(angle) * outer, center + Math.sin(angle) * outer);
    ctx.stroke();
  }
  requestAnimationFrame(drawOrb);
}
requestAnimationFrame(drawOrb);

// --- speaking (per sentence, so speech starts before the reply finishes) ---

/** Remove markdown symbols so speech doesn't read "asterisk asterisk" aloud. */
function normalizeForSpeech(text) {
  // Mirrors jarvis/voice/tts.py's normalize_for_speech(): the web HUD speaks
  // replies through the BROWSER's own speechSynthesis, a separate code path
  // from the backend Piper/Kokoro engine, so the Python-side course-code
  // fix never reached this interface without a matching JS copy.
  return text
    .replace(/\(\|([A-Za-z]+\d+)\|\)/g, "$1") // GUC scrape markup: "(|CSEN401|)" -> "CSEN401"
    .replace(/\|/g, " ")                  // stray pipes
    .replace(/\b([A-Za-z]{2,8})(\d{2,5})\b/g, "$1 $2"); // "CSEN401" -> "CSEN 401"
}

function stripMarkdown(text) {
  return normalizeForSpeech(
    text
      .replace(/```[\s\S]*?```/g, " ")        // fenced code blocks
      .replace(/`([^`]*)`/g, "$1")            // inline code
      .replace(/\*\*?|__?/g, "")              // *bold* / _italics_
      .replace(/^\s{0,3}#{1,6}\s*/gm, "")     // headings
      .replace(/^\s*[-*+]\s+/gm, "")          // bullets
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1") // [label](url)
  ).replace(/\s{2,}/g, " ");
}

function speakSentence(text) {
  const cleaned = stripMarkdown(text).trim();
  if (!cleaned || !("speechSynthesis" in window)) return;
  const utterance = new SpeechSynthesisUtterance(cleaned);
  utterance.rate = 1.05;
  speakingUtterances += 1;
  let boundarySeen = false;
  let fallbackTimer = null;
  let boundaryWatch = null;
  let settled = false;
  utterance.onstart = () => {
    disarmMic();
    orbState = "speaking";
    updateOrbStatus();
    boundaryWatch = setTimeout(() => {
      // This is visual synthesis, not audio analysis: browser TTS exposes no
      // MediaStream/AudioNode. Prefer real boundary pulses when available.
      if (!boundarySeen) fallbackTimer = setInterval(() => {
        speechPulse = 0.38 + Math.random() * 0.55;
      }, 110);
    }, 400);
  };
  utterance.onboundary = () => {
    boundarySeen = true;
    speechPulse = 0.75 + Math.random() * 0.25;
  };
  const settle = () => {
    if (settled) return;
    settled = true;
    clearTimeout(boundaryWatch);
    clearInterval(fallbackTimer);
    speakingUtterances = Math.max(0, speakingUtterances - 1);
    if (!speakingUtterances) {
      orbState = "idle";
      updateOrbStatus();
      scheduleMicArm();
    }
  };
  utterance.onend = settle;
  utterance.onerror = settle;
  speechSynthesis.speak(utterance);
}

// Mirrors jarvis/brain.py's SentenceBuffer._ABBREVIATIONS -- without this,
// "Dr. Smith called" streams as two separate utterances ("Dr." then "Smith
// called") with an unnatural pause between them.
const TTS_ABBREVIATIONS = new Set([
  "dr", "mr", "mrs", "ms", "prof", "st", "vs", "etc", "jr", "sr",
  "approx", "no", "vol", "fig", "dept", "univ", "e.g", "i.e", "inc",
  "ltd", "co", "u.s", "u.k",
]);

function endsWithAbbreviation(text) {
  // Include internal periods so dotted entries such as "e.g." and "U.S."
  // are recognized, matching SentenceBuffer._ends_with_abbreviation().
  const m = text.match(/([\w.]+)\.$/);
  return !!m && TTS_ABBREVIATIONS.has(m[1].toLowerCase());
}

function ttsPush(chunk) {
  if (!voiceTurn) return;
  ttsBuffer += chunk;
  // a sentence ends at . ! or ? followed by whitespace, or at a newline
  let searchFrom = 0;
  while (true) {
    const rest = ttsBuffer.slice(searchFrom);
    const match = rest.match(/[.!?](?=\s)|\n/);
    if (!match) break;
    const end = searchFrom + match.index + match[0].length;
    if (ttsBuffer[end - 1] === "." && endsWithAbbreviation(ttsBuffer.slice(0, end))) {
      searchFrom = end;
      continue;
    }
    speakSentence(ttsBuffer.slice(0, end));
    ttsBuffer = ttsBuffer.slice(end);
    searchFrom = 0;
  }
}

function ttsFinish(finalText) {
  if (!voiceTurn) return;
  if (gotTokens) {
    speakSentence(ttsBuffer);        // whatever was left after the last split
  } else if (finalText) {
    speakSentence(finalText);        // reply never streamed (e.g. Ollama brain)
  }
  ttsBuffer = "";
  voiceTurn = false;
  if (!speakingUtterances) scheduleMicArm();
}

// --- listening ---

function setListening(on) {
  micBtn.classList.toggle("listening", on);
  ring.classList.toggle("listening", on);
  orbState = on ? "listening" : "idle";
  updateOrbStatus();
  if (on) {
    statusline.textContent = "listening";
  } else {
    // A listening session can end with nothing sent (background-noise false
    // trigger, a recognition error) — always go through scheduleMicArm's
    // cooldown before VAD can re-check, or sustained noise could thrash
    // start/stop in a tight loop. scheduleMicArm() is a no-op re-arm if the
    // mic is already open, it just resets the cooldown timer.
    if (voiceFirstMode) scheduleMicArm(); else disarmMic();
    if (!busy) statusline.textContent = "standing by";
  }
}

function stopListening() {
  if (recognition) {
    if (recognition._jarvisCancel) recognition._jarvisCancel();
    recognition.onend = null;        // don't double-handle the stop
    try { recognition.stop(); } catch (e) { /* already stopped */ }
    recognition = null;
  }
  if (mediaRecorder) stopBackendRecording(false);
  setListening(false);
}

function handleFinalTranscript(text) {
  text = (text || "").trim();
  const bareWord = text.toLowerCase().replace(/[.!?,]+$/, "").trim();
  if (bareWord === "goodbye" || bareWord === "exit") {
    input.value = "";
    showShuttingDown();
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "shutdown_request" }));
    }
  } else if (bareWord === "mute") {
    input.value = "";
    setVoiceMode(false);
    showToast("Voice muted — tap the orb or switch to Voice-first to resume.");
  } else if (text) {
    sendMessage(text, true);
  }
}

function stopBackendRecording(sendAudio) {
  const recorder = mediaRecorder;
  if (!recorder || recorder.state === "inactive") return;
  recorder._jarvisSend = sendAudio;
  try { recorder.stop(); } catch (e) { /* already stopped */ }
}

async function startBackendListening() {
  if (typeof MediaRecorder === "undefined" || mediaRecorder || backendTranscribing) return;
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  await ensureMicArmed();
  if (!micStream) {
    showToast("Microphone blocked — allow mic access for this site and retry.");
    return;
  }
  let options;
  if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) {
    options = { mimeType: "audio/webm;codecs=opus" };
  } else if (MediaRecorder.isTypeSupported("audio/webm")) {
    options = { mimeType: "audio/webm" };
  }
  let recorder;
  try {
    recorder = options ? new MediaRecorder(micStream, options) : new MediaRecorder(micStream);
  } catch (error) {
    showToast(`Voice input problem: ${error.message || error}`);
    return;
  }
  const chunks = [];
  mediaRecorder = recorder;
  recorder._jarvisSend = false;
  recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
  recorder.onerror = (event) => {
    if (mediaRecorder === recorder) mediaRecorder = null;
    setListening(false);
    showToast(`Voice input problem: ${event.error?.message || "recording failed"}`);
  };
  recorder.onstop = async () => {
    const shouldSend = recorder._jarvisSend;
    if (mediaRecorder === recorder) mediaRecorder = null;
    if (!shouldSend) { setListening(false); return; }
    backendTranscribing = true;
    setListening(false);
    orbStatus.textContent = "Transcribing…";
    statusline.textContent = "transcribing";
    try {
      const buffer = await new Blob(chunks, { type: recorder.mimeType }).arrayBuffer();
      if (!ws || ws.readyState !== WebSocket.OPEN) throw new Error("Jarvis is reconnecting");
      ws.send(buffer);
    } catch (error) {
      backendTranscribing = false;
      updateOrbStatus();
      showToast(`Voice input problem: ${error.message || error}`);
    }
  };
  backendRecordStartedAt = performance.now();
  backendSpeechDetected = false;
  backendSilenceSince = null;
  recorder.start();
  setListening(true);
}

function startListening() {
  if (sttSource === "backend") {
    startBackendListening();
    return;
  }
  if (!SpeechRec || recognition) return;
  if ("speechSynthesis" in window) speechSynthesis.cancel(); // don't hear itself
  ensureMicArmed();
  const session = new SpeechRec();
  recognition = session;
  session.lang = "en-US";
  session.interimResults = true;
  session.continuous = true;
  session.maxAlternatives = 1;
  let finalTranscript = "";
  let interim = "";
  let finished = false;
  let silenceTimer = null;
  let maxTimer = null;

  const clearSessionTimers = () => {
    clearTimeout(silenceTimer);
    clearTimeout(maxTimer);
  };
  session._jarvisCancel = () => {
    if (finished) return;
    finished = true;
    clearSessionTimers();
  };
  const finalize = () => {
    if (finished) return;
    finished = true;
    clearSessionTimers();
    const text = `${finalTranscript} ${interim}`.trim();
    session.onend = null;
    try { session.stop(); } catch (e) { /* already stopped */ }
    if (recognition === session) recognition = null;
    setListening(false);
    handleFinalTranscript(text);
  };

  session.onresult = (event) => {
    interim = "";
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const text = event.results[i][0].transcript.trim();
      if (event.results[i].isFinal) {
        if (text) finalTranscript = `${finalTranscript} ${text}`.trim();
      } else {
        interim = text;
      }
    }
    input.value = `${finalTranscript} ${interim}`.trim();
    clearTimeout(silenceTimer);
    silenceTimer = setTimeout(finalize, END_OF_SPEECH_SILENCE_MS);
  };
  session.onerror = (event) => {
    if (finished) return;
    finished = true;
    clearSessionTimers();
    if (recognition === session) recognition = null;
    setListening(false);
    if (event.error === "not-allowed" || event.error === "service-not-allowed") {
      showToast("Microphone blocked — allow mic access for this site and retry.");
    } else if (event.error === "no-speech") {
      showToast("Didn't catch anything — tap the mic and try again.");
    } else if (event.error !== "aborted") {
      showToast(`Voice input problem: ${event.error}`);
    }
  };
  session.onend = finalize;   // browser gave up unexpectedly

  maxTimer = setTimeout(finalize, MAX_UTTERANCE_MS);
  session.start();
  setListening(true);
}

function handleVoiceControl() {
  if (orbState === "speaking") {
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    // Some browsers don't reliably fire onend/onerror for an utterance killed
    // by cancel(), so reset the speaking count ourselves.
    speakingUtterances = 0;
    orbState = "idle";
    updateOrbStatus();
    scheduleMicArm();
    return;
  }
  if (sttSource === "browser" && !SpeechRec) {
    showToast("Voice input needs Chrome or Edge — this browser doesn't support it.");
    return;
  }
  if (sttSource === "backend" && typeof MediaRecorder === "undefined") {
    showToast("Voice recording isn't supported by this browser.");
    return;
  }
  if (recognition || mediaRecorder) { stopListening(); return; } // tap again to cancel
  if (busy) {
    showToast("Still answering — wait for the reply, then talk.");
    return;
  }
  startListening();
}

micBtn.addEventListener("click", handleVoiceControl);
orbButton.addEventListener("click", handleVoiceControl);

document.addEventListener("visibilitychange", () => {
  if (document.hidden) disarmMic();
  else if (voiceFirstMode) scheduleMicArm();
});
window.addEventListener("pagehide", disarmMic);

window.__jarvisDebug = {
  get orbState() { return orbState; },
  get voiceFirstMode() { return voiceFirstMode; },
  get micArmed() { return Boolean(micStream); },
  get recognitionActive() { return Boolean(recognition); },
  get mediaRecorderActive() { return Boolean(mediaRecorder); },
  get sttSource() { return sttSource; },
  get busy() { return busy; },
  get activeConfirm() { return Boolean(activeConfirm); },
  get confirmationRecognitionActive() { return Boolean(confirmationRecognition); },
  get confirmationAttempt() { return confirmationAttempt; },
  get confirmationPromptSpeaking() { return Boolean(confirmationPromptUtterance); },
  get vadCooldownUntil() { return vadCooldownUntil; },
  get wsOpen() { return Boolean(ws && ws.readyState === WebSocket.OPEN); },
  get micAnalyserPresent() { return Boolean(micAnalyser); },
};

// ---------------------------------------------------------------------- rail

railToggle.addEventListener("click", () => {
  if (!gucScreen.hidden) closeGuc(false);
  const collapsed = rail.classList.toggle("collapsed");
  railToggle.setAttribute("aria-expanded", String(!collapsed));
});

// Collapse the rail by default on small screens so chat gets the full width.
if (window.matchMedia("(max-width: 900px)").matches) {
  rail.classList.add("collapsed");
  railToggle.setAttribute("aria-expanded", "false");
}

input.focus();
