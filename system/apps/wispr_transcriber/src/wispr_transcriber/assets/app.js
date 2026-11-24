// Press the button, speak, see what Wispr sends back.

const $ = (id) => document.getElementById(id);
const els = {
  dot: $("signin-dot"), signin: $("signin-text"),
  record: $("record"), micIcon: $("icon-mic"), stopIcon: $("icon-stop"), hint: $("hint"),
  pick: $("pick"), sample: $("sample"), say: $("say"), sayWrap: $("say-wrap"), file: $("file"),
  notice: $("notice"), failed: $("failed"),
  result: $("result"), raw: $("raw-text"), fmt: $("fmt-text"), copyFmt: $("copy-fmt"),
  player: $("player"), facts: $("facts"), rawjson: $("rawjson"),
  past: $("past"), runs: $("runs"),
};
const others = [els.pick, els.sample, els.say];

let recorder = null;
let chunks = [];
let startedAt = 0;
let ticker = null;
let meter = null;
let wakeLock = null;
let currentFormattedText = "";

function setBusy(message) {
  els.hint.textContent = message || "Press to record, then say something.";
  els.record.disabled = Boolean(message);
  others.forEach((button) => { button.disabled = Boolean(message); });
  if (message) els.failed.style.display = "none";
}

function fail(message) {
  els.failed.textContent = message;
  els.failed.style.display = "block";
}

function notice(html) {
  els.notice.innerHTML = html;
  els.notice.style.display = "block";
}

async function call(path, options = {}) {
  const response = await fetch(path, options);
  let body = {};
  try { body = await response.json(); } catch { /* empty or non-JSON */ }
  if (!response.ok) throw new Error(body.detail || `That did not work (${response.status}).`);
  return body;
}

// ---- Recording (desktop + iOS Safari) ----------------------------------------------------

function stopMeter() {
  if (!meter) return;
  cancelAnimationFrame(meter.frame);
  meter.context.close().catch(() => {});
  meter = null;
  els.record.style.removeProperty("--ring");
}

function startMeter(stream) {
  // The ring breathing with your voice is the honest proof the microphone is live.
  // Wrapped in try/catch because iOS Safari can suspend or refuse AudioContext while recording.
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    const context = new AudioCtx();
    if (context.state === "suspended") context.resume().catch(() => {});
    const analyser = context.createAnalyser();
    analyser.fftSize = 512;
    context.createMediaStreamSource(stream).connect(analyser);
    const data = new Uint8Array(analyser.frequencyBinCount);
    meter = { context, frame: 0 };
    const draw = () => {
      analyser.getByteTimeDomainData(data);
      let peak = 0;
      for (const value of data) peak = Math.max(peak, Math.abs(value - 128));
      els.record.style.setProperty("--ring", `${Math.min(26, (peak / 128) * 90)}px`);
      meter.frame = requestAnimationFrame(draw);
    };
    draw();
  } catch {
    meter = null;
  }
}

async function acquireWakeLock() {
  // iOS Safari 16.4+ supports Screen Wake Lock; keeps the phone awake during longer dictation.
  try {
    if (navigator.wakeLock && typeof navigator.wakeLock.request === "function") {
      wakeLock = await navigator.wakeLock.request("screen");
    }
  } catch {
    wakeLock = null;
  }
}

function releaseWakeLock() {
  if (!wakeLock) return;
  const lock = wakeLock;
  wakeLock = null;
  lock.release().catch(() => {});
}

function pickRecorderMimeType() {
  if (typeof MediaRecorder === "undefined" || typeof MediaRecorder.isTypeSupported !== "function") {
    return "";
  }
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4;codecs=mp4a.40.2",
    "audio/mp4",
    "audio/aac",
  ];
  for (const type of candidates) {
    try {
      if (MediaRecorder.isTypeSupported(type)) return type;
    } catch { /* ignore unsupported probes */ }
  }
  return "";
}

function filenameForMime(mimeType) {
  const lower = (mimeType || "").toLowerCase();
  if (lower.includes("mp4") || lower.includes("aac") || lower.includes("m4a")) return "spoken.m4a";
  if (lower.includes("ogg")) return "spoken.ogg";
  if (lower.includes("wav")) return "spoken.wav";
  return "spoken.webm";
}

function showRecording(on) {
  els.record.classList.toggle("live", on);
  els.micIcon.hidden = on;
  els.stopIcon.hidden = !on;
  els.record.setAttribute("aria-label", on ? "Stop recording" : "Start recording");
  others.forEach((button) => { button.disabled = on; });
}

// Keep inside the service's own ceiling, so a long recording is never rejected after the wait.
const MAX_RECORD_SECONDS = 230;

function tick() {
  const seconds = (Date.now() - startedAt) / 1000;
  els.hint.innerHTML = `Listening &mdash; <span id="timer">${seconds.toFixed(1)}s</span> &middot; press again when you are done`;
  if (seconds >= MAX_RECORD_SECONDS) stopRecording();
}

/** Put everything back the way it was before recording started. */
function finishRecording(stream) {
  stream.getTracks().forEach((track) => track.stop());
  stopMeter();
  releaseWakeLock();
  clearInterval(ticker);
  ticker = null;
  recorder = null;
  showRecording(false);
  setBusy("");
}

async function startRecording() {
  if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
    notice(
      "<strong>Microphone access is unavailable on this page.</strong> On iOS Safari and desktop " +
      "browsers, microphone recording requires HTTPS (open this app via its Imbue Studio share link)."
    );
    return;
  }
  if (typeof MediaRecorder === "undefined") {
    fail("This browser does not support audio recording. Use 'use a recording' to upload a clip instead.");
    return;
  }

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    const link = await call("/api/share-link").catch(() => ({}));
    const where = link.url
      ? `<a href="${link.url}" target="_blank" rel="noopener">open this page in a browser tab</a>`
      : "open this page in a browser tab";
    notice(
      `<strong>This window will not give the microphone.</strong> App windows here are not allowed to ` +
      `use it (${error.name}). To speak into it, ${where} and press record there. ` +
      `Everything else on this page works as it is.`
    );
    return;
  }

  chunks = [];
  const wanted = pickRecorderMimeType();
  // Hold the recorder and its type in locals: the stop event fires after `recorder` is
  // cleared, so reading them off the module-level handle there would throw and the
  // recording would be lost without a word.
  const instance = new MediaRecorder(stream, wanted ? { mimeType: wanted } : undefined);
  recorder = instance;

  instance.addEventListener("dataavailable", (event) => {
    if (event.data && event.data.size) chunks.push(event.data);
  });
  instance.addEventListener("error", (event) => {
    finishRecording(stream);
    fail(`The recorder stopped unexpectedly (${(event.error && event.error.name) || "unknown"}).`);
  });
  instance.addEventListener("stop", () => {
    finishRecording(stream);
    try {
      // iOS Safari often leaves instance.mimeType empty until dataavailable fires.
      const actualMime = (chunks[0] && chunks[0].type) || instance.mimeType || wanted || "audio/mp4";
      const blob = new Blob(chunks, { type: actualMime });
      if (blob.size < 500) {
        fail("That was too short to hear. Hold on a moment longer before pressing again.");
        return;
      }
      sendBlob(blob, filenameForMime(actualMime), "Sending what you said to Wispr…");
    } catch (error) {
      fail(`The recording could not be packaged up: ${error.message}`);
    }
  });

  // If iOS interrupts the mic (screen lock, phone call, switching apps), stop cleanly so we keep the audio.
  stream.getAudioTracks().forEach((track) => {
    track.addEventListener("ended", () => {
      if (recorder === instance && instance.state !== "inactive") stopRecording();
    });
  });

  // Do not pass a timeslice: iOS Safari produces one clean MP4 container when started without one.
  instance.start();
  startedAt = Date.now();
  showRecording(true);
  startMeter(stream);
  acquireWakeLock();
  tick();
  ticker = setInterval(tick, 100);
}

function stopRecording() {
  // finishRecording runs on the stop event, which also clears `recorder`.
  if (recorder && recorder.state !== "inactive") recorder.stop();
}

els.record.addEventListener("click", () => {
  els.notice.style.display = "none";
  if (recorder) stopRecording(); else startRecording();
});

// ---- Drawing one run ---------------------------------------------------------------------

function renderSide(target, segments, fallback) {
  target.textContent = "";
  if (!segments || !segments.length) { target.textContent = fallback || ""; return; }
  for (const part of segments) {
    let node;
    if (part.kind === "dropped") { node = document.createElement("s"); node.className = "dropped"; }
    else if (part.kind === "changed") { node = document.createElement("mark"); node.className = "changed"; }
    else if (part.kind === "added") { node = document.createElement("mark"); node.className = "added"; }
    else node = document.createElement("span");
    node.textContent = part.text;
    target.appendChild(node);
  }
}

function show(run) {
  currentFormattedText = run.formatted || run.raw || "";
  renderSide(els.raw, run.diff && run.diff.left, run.raw);
  renderSide(els.fmt, run.diff && run.diff.right, run.formatted);
  if (els.copyFmt) els.copyFmt.textContent = "Copy";
  const edits = run.diff ? run.diff.edits : 0;
  els.facts.textContent =
    `${run.audio_seconds}s of audio · back in ${run.round_trip_seconds}s · ` +
    `${edits} edit${edits === 1 ? "" : "s"} by ${run.origin || "Wispr"} · ${run.server || "Wispr"}`;
  els.player.src = `/api/runs/${run.id}/audio`;
  els.rawjson.textContent = "Loading…";
  fetch(`/api/runs/${run.id}/raw`)
    .then((response) => response.text())
    .then((text) => { els.rawjson.textContent = text; })
    .catch(() => { els.rawjson.textContent = "The saved reply could not be read."; });
  els.result.style.display = "block";
}

if (els.copyFmt) {
  els.copyFmt.addEventListener("click", async () => {
    if (!currentFormattedText) return;
    try {
      if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
        await navigator.clipboard.writeText(currentFormattedText);
      } else {
        const ta = document.createElement("textarea");
        ta.value = currentFormattedText;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
      }
      els.copyFmt.textContent = "Copied";
      setTimeout(() => { els.copyFmt.textContent = "Copy"; }, 1800);
    } catch {
      els.copyFmt.textContent = "Select text to copy";
    }
  });
}

async function loadRuns({ showLatest = false } = {}) {
  const { runs } = await call("/api/runs");
  if (showLatest && runs.length) show(runs[0]);
  els.runs.textContent = "";
  els.past.style.display = runs.length > 1 ? "block" : "none";
  for (const run of runs.slice(showLatest ? 1 : 0)) {
    const row = document.createElement("div");
    row.className = "run";
    row.dataset.runId = run.id;
    const when = document.createElement("time");
    when.textContent = new Date(run.at * 1000).toLocaleString();
    const what = document.createElement("span");
    what.textContent = run.formatted || run.raw;
    row.append(when, what);
    row.addEventListener("click", () => { show(run); window.scrollTo({ top: 0, behavior: "smooth" }); });
    els.runs.appendChild(row);
  }
}

// ---- Sending -----------------------------------------------------------------------------

async function run(message, path, options) {
  setBusy(message);
  try {
    show(await call(path, options));
    await Promise.all([loadRuns(), loadStatus()]);
  } catch (error) {
    fail(error.message);
  } finally {
    setBusy("");
  }
}

function sendBlob(blob, filename, message) {
  if (!blob) return;
  const form = new FormData();
  form.append("audio", blob, filename || "recording.m4a");
  run(message || `Sending ${filename || "recording"} to Wispr…`, "/api/transcribe", { method: "POST", body: form });
}

function sendFile(file, message) {
  if (!file) return;
  sendBlob(file, file.name || "recording.m4a", message);
}

els.pick.addEventListener("click", () => els.file.click());
els.file.addEventListener("change", () => { sendFile(els.file.files[0]); els.file.value = ""; });
els.sample.addEventListener("click", () => run("Sending the clip to Wispr…", "/api/sample", { method: "POST" }));
els.say.addEventListener("click", () => {
  const text = window.prompt(
    "What should your Mac say out loud?",
    "Um, so I think we should, uh, move the team meeting to Thursday at three.",
  );
  if (!text) return;
  run("Asking your Mac to speak it…", "/api/speak", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
});

// The whole page takes a dropped recording; no target to aim at.
document.addEventListener("dragover", (event) => { event.preventDefault(); document.body.classList.add("dragging"); });
document.addEventListener("dragleave", (event) => { if (!event.relatedTarget) document.body.classList.remove("dragging"); });
document.addEventListener("drop", (event) => {
  event.preventDefault();
  document.body.classList.remove("dragging");
  sendFile(event.dataTransfer.files[0]);
});

// ---- The sign-in pill ----------------------------------------------------------------------

function left(seconds) {
  if (seconds == null) return "";
  if (seconds > 172800) return `${Math.round(seconds / 86400)} days left`;
  if (seconds > 7200) return `${Math.round(seconds / 3600)} hours left`;
  return `${Math.max(0, Math.round(seconds / 60))} min left`;
}

async function loadStatus() {
  try {
    const state = await call("/api/status");
    if (els.sayWrap) els.sayWrap.hidden = !state.deskrun_available;
    els.dot.className = `dot ${state.signed_in ? (state.needs_mac ? "warn" : "ok") : "bad"}`;
    els.signin.textContent = state.signed_in
      ? `${state.email || "Signed in"} · ${left(state.seconds_left)}`
      : "No Wispr sign-in";
    if (state.needs_mac && !state.signed_in) {
      notice(
        'The Wispr sign-in here is missing or expired. Make sure Wispr Flow is signed in on your Mac ' +
        'and <code>~/Library/Application Support/Wispr Flow</code> is shared, then ' +
        '<button type="button" id="refresh-signin">refresh sign-in</button>.'
      );
      const btn = document.getElementById("refresh-signin");
      if (btn) {
        btn.addEventListener("click", async () => {
          btn.disabled = true;
          btn.textContent = "refreshing…";
          try {
            await call("/api/refresh-signin", { method: "POST" });
            els.notice.style.display = "none";
            await loadStatus();
          } catch (error) {
            fail(error.message);
            btn.disabled = false;
            btn.textContent = "refresh sign-in";
          }
        });
      }
    }
  } catch {
    els.dot.className = "dot bad";
    els.signin.textContent = "Cannot reach the app";
  }
}

loadStatus();
loadRuns({ showLatest: true }).catch(() => {});
