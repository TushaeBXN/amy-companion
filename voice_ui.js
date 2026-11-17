// Talk mode for Amy's web page: headset conversation.
//   mic -> voice-activity detection -> /stt (Whisper) -> sendMsg() -> her reply streams in
//   -> each finished sentence goes to /tts and plays while the next one is written.
// Talking over her stops her at once, and /heard tells the server only what you actually heard.
// Loaded by amy_server.py's page after the main script; it reuses that page's globals.
(() => {
  const TARGET_RATE = 16000;
  const SILENCE_MS = 900;        // pause that ends your turn
  const MIN_SPEECH_MS = 350;     // shorter than this is a cough/click, ignored
  const PRE_ROLL_MS = 400;       // keep audio from just before you started
  const START_FRAMES = 2;        // consecutive loud buffers to count as speech

  let on = false;
  let stream = null, ctx = null, proc = null, srcNode = null;
  let noiseFloor = 0.008;
  let state = "off";             // off | listening | hearing | thinking | speaking

  // ── capture ────────────────────────────────────────────────────────────────
  let preRoll = [];              // [{data, ms}]
  let seg = null;                // {chunks:[Float32Array], ms, quietMs}
  let loudRun = 0;
  let stoppedAt = 0;             // when you stopped talking (for the timing readout)

  // ── playback ───────────────────────────────────────────────────────────────
  let playQueue = [];            // [{text, p: Promise<Blob|null>}]
  let playing = null;            // {audio, text}
  let spokenSoFar = "";          // sentences that finished playing in this reply
  let replyText = "";            // what the current reply has produced so far
  let spokenIdx = 0;             // how much of replyText has been queued for speech
  let streamDone = true;
  let firstAudioLogged = true;
  let muted = false;             // after you interrupt, ignore the rest of her old reply
  let replyEpoch = 0;            // bumps when we cancel, so old fetches are ignored

  // ── UI ─────────────────────────────────────────────────────────────────────
  const btn = document.createElement("button");
  btn.className = "icon-btn";
  btn.id = "talkBtn";
  btn.title = "Talk to Amy (headset). Click again to stop.";
  btn.textContent = "🎙";
  const chip = document.createElement("div");
  chip.style.cssText = "position:fixed;right:14px;bottom:70px;padding:5px 11px;border-radius:14px;" +
    "font-size:12px;background:#1b2733;color:#9fc4e0;border:1px solid #2c4256;display:none;z-index:50";
  document.body.appendChild(chip);
  const bar = document.getElementById("inputbar");
  bar.insertBefore(btn, document.getElementById("input"));

  function setState(s, extra) {
    state = s;
    const label = { off: "", listening: "listening…", hearing: "hearing you…", thinking: "thinking…", speaking: "speaking…" }[s];
    chip.style.display = on ? "" : "none";
    chip.textContent = (label || "") + (extra ? "  " + extra : "");
    btn.style.background = on ? (s === "hearing" ? "#2e7d4f" : s === "speaking" ? "#2d5f8a" : "#3a4a5a") : "";
  }

  // ── turn on / off ──────────────────────────────────────────────────────────
  btn.addEventListener("click", () => (on ? stopTalk() : startTalk()));

  async function startTalk() {
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (e) {
      appendSys("Microphone not available: " + e.message + " (allow it in the browser's address bar)");
      return;
    }
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    await ctx.resume();
    srcNode = ctx.createMediaStreamSource(stream);
    proc = ctx.createScriptProcessor(2048, 1, 1);
    proc.onaudioprocess = onAudio;
    srcNode.connect(proc);
    proc.connect(ctx.destination);   // must be connected for the callback to run; output is silent
    on = true;
    preRoll = []; seg = null; loudRun = 0;
    setState("listening", "(warming up…)");
    fetch("/voice/warm", { method: "POST" }).then(() => { if (on && state === "listening") setState("listening"); }).catch(() => {});
    appendSys("🎙 Talk mode on. Just speak; talk over her any time to interrupt.");
  }

  function stopTalk() {
    on = false;
    cancelSpeech(false);
    try { proc && proc.disconnect(); srcNode && srcNode.disconnect(); } catch {}
    try { stream && stream.getTracks().forEach(t => t.stop()); } catch {}
    try { ctx && ctx.close(); } catch {}
    stream = ctx = proc = srcNode = null;
    seg = null;
    setState("off");
    appendSys("🎙 Talk mode off.");
  }

  // ── listening ──────────────────────────────────────────────────────────────
  function rms(buf) {
    let s = 0;
    for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i];
    return Math.sqrt(s / buf.length);
  }

  function onAudio(e) {
    if (!on) return;
    const data = new Float32Array(e.inputBuffer.getChannelData(0));   // copy
    const ms = (data.length / ctx.sampleRate) * 1000;
    const level = rms(data);
    const speaking = state === "speaking";
    // Louder bar to start while she's talking, so her own voice leaking into the mic doesn't cut her off.
    const thr = Math.max(0.015, noiseFloor * (speaking ? 5 : 3));
    const loud = level > thr;

    if (!seg) {
      if (!loud) noiseFloor = noiseFloor * 0.97 + level * 0.03;     // learn the room
      preRoll.push({ data, ms });
      let total = preRoll.reduce((a, c) => a + c.ms, 0);
      while (total > PRE_ROLL_MS && preRoll.length > 1) total -= preRoll.shift().ms;
      loudRun = loud ? loudRun + 1 : 0;
      if (loudRun >= START_FRAMES) {
        // You started talking.
        if (state === "speaking" || playQueue.length || playing) bargeIn();
        seg = { chunks: preRoll.map(p => p.data), ms: preRoll.reduce((a, c) => a + c.ms, 0), quietMs: 0, speechMs: 0 };
        preRoll = [];
        setState("hearing");
      }
    } else {
      seg.chunks.push(data);
      seg.ms += ms;
      if (loud) { seg.quietMs = 0; seg.speechMs += ms; } else { seg.quietMs += ms; }
      if (seg.quietMs >= SILENCE_MS) finishSegment();
    }
  }

  function finishSegment() {
    const s = seg; seg = null; loudRun = 0;
    stoppedAt = performance.now() - s.quietMs;      // when speech actually ended
    if (s.speechMs < MIN_SPEECH_MS) { setState(waiting ? "thinking" : "listening"); return; }
    setState("thinking", "(transcribing)");
    const wav = toWav(s.chunks, ctx.sampleRate);
    fetch("/stt", { method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav })
      .then(r => r.json())
      .then(d => {
        const text = (d.text || "").trim();
        if (!text) { setState(waiting ? "thinking" : "listening"); return; }
        console.log("[talk] heard:", text, `(${Math.round(performance.now() - stoppedAt)}ms after you stopped)`);
        submit(text);
      })
      .catch(() => { setState("listening"); });
  }

  function submit(text) {
    firstAudioLogged = false;
    if (waiting) {                 // she's still working on the last thing: send this one when she's done
      queuedMsg = text;
      input.classList.add("queued");
      muted = false;
      return;
    }
    muted = false;
    input.value = text;
    setState("thinking");
    sendMsg();
  }

  // 16 kHz mono 16-bit WAV from float chunks
  function toWav(chunks, srcRate) {
    let n = 0; chunks.forEach(c => (n += c.length));
    const all = new Float32Array(n); let o = 0;
    chunks.forEach(c => { all.set(c, o); o += c.length; });
    const ratio = srcRate / TARGET_RATE;
    const outLen = Math.floor(all.length / ratio);
    const buf = new ArrayBuffer(44 + outLen * 2);
    const v = new DataView(buf);
    const w = (off, str) => { for (let i = 0; i < str.length; i++) v.setUint8(off + i, str.charCodeAt(i)); };
    w(0, "RIFF"); v.setUint32(4, 36 + outLen * 2, true); w(8, "WAVE"); w(12, "fmt ");
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, TARGET_RATE, true); v.setUint32(28, TARGET_RATE * 2, true);
    v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, "data"); v.setUint32(40, outLen * 2, true);
    for (let i = 0; i < outLen; i++) {                       // average each source window (cheap low-pass)
      const a = Math.floor(i * ratio), b = Math.max(a + 1, Math.floor((i + 1) * ratio));
      let sum = 0; for (let j = a; j < b && j < all.length; j++) sum += all[j];
      const x = Math.max(-1, Math.min(1, sum / (b - a)));
      v.setInt16(44 + i * 2, x < 0 ? x * 0x8000 : x * 0x7fff, true);
    }
    return buf;
  }

  // ── speaking ───────────────────────────────────────────────────────────────
  const CLOSER = /^(?:just |and )?(?:let me know|feel free|i'?m (?:here|happy|ready)|happy to|hope that helps|if you(?:'d| would) like|want me to)\b/i;

  function clean(t) {
    return t.replace(/```[\s\S]*?```/g, " ")
      .replace(/https?:\/\/\S+/g, " link ")
      .replace(/[*_`#>~|]/g, "")
      .replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, "")
      .replace(/\s+/g, " ").trim();
  }

  function splitSentences(t) {          // [complete sentences], remainder
    const out = []; let last = 0;
    const re = /[.!?]+["')\]]*\s+/g; let m;
    while ((m = re.exec(t))) { out.push(t.slice(last, m.index + m[0].length).trim()); last = m.index + m[0].length; }
    return [out, t.slice(last)];
  }

  function fetchTTS(text, epoch) {
    return fetch("/tts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) })
      .then(r => (r.ok ? r.blob() : null)).then(b => (epoch === replyEpoch ? b : null)).catch(() => null);
  }

  function enqueue(text) {
    const t = clean(text);
    if (!t || !/\w/.test(t)) return;
    playQueue.push({ text: t, p: fetchTTS(t, replyEpoch) });
    pump();
  }

  async function pump() {
    if (playing || !playQueue.length) {
      if (!playing && !playQueue.length && streamDone && !waiting) setState(on ? "listening" : "off");
      return;
    }
    const item = playQueue.shift();
    playing = { text: item.text, audio: null };
    setState("speaking");
    const epoch = replyEpoch;
    const blob = await item.p;
    if (epoch !== replyEpoch || !blob) { playing = null; return pump(); }
    const audio = new Audio(URL.createObjectURL(blob));
    playing.audio = audio;
    if (!firstAudioLogged && stoppedAt) {
      firstAudioLogged = true;
      const t = ((performance.now() - stoppedAt) / 1000).toFixed(1);
      console.log(`[talk] first word ${t}s after you stopped`);
      setState("speaking", `(first word ${t}s)`);
    }
    audio.onended = () => { spokenSoFar += (spokenSoFar ? " " : "") + item.text; playing = null; pump(); };
    audio.onerror = () => { playing = null; pump(); };
    audio.play().catch(() => { playing = null; pump(); });
  }

  // You talked over her: stop now, forget the unspoken rest, and tell the server what you heard.
  function bargeIn() {
    const heard = spokenSoFar;
    muted = true;
    cancelSpeech(true);
    if (waiting) interruptAmy();                        // stop her still writing the rest
    fetch("/heard", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: heard }) });
  }

  function cancelSpeech(keepState) {
    replyEpoch++;
    playQueue = [];
    if (playing && playing.audio) { try { playing.audio.pause(); } catch {} }
    playing = null;
    replyText = ""; spokenIdx = 0; spokenSoFar = "";
    if (!keepState) setState(on ? "listening" : "off");
  }

  // ── hook into the page's streaming reply ──────────────────────────────────
  const _appendChunk = window.appendChunk;
  window.appendChunk = function (bubble, text) {
    _appendChunk(bubble, text);
    if (!on || muted) return;
    if (streamDone) { replyText = ""; spokenIdx = 0; spokenSoFar = ""; streamDone = false; }
    replyText += text;
    const [sents] = splitSentences(replyText.slice(spokenIdx));
    // Hold back the newest finished sentence: it might be the closing question/offer we skip at the end.
    while (sents.length > 1) {
      const s = sents.shift();
      spokenIdx += replyText.slice(spokenIdx).indexOf(s) + s.length;
      enqueue(s);
    }
  };

  const _setWaiting = window.setWaiting;
  window.setWaiting = function (v) {
    _setWaiting(v);
    if (!on) return;
    if (v) { streamDone = false; return; }
    if (muted) { streamDone = true; replyText = ""; spokenIdx = 0; setState("listening"); return; }
    // Reply finished: speak what's left, minus a trailing closer.
    const rest = replyText.slice(spokenIdx).trim();
    streamDone = true;
    if (rest) {
      const [sents, tail] = splitSentences(rest);
      const all = tail.trim() ? sents.concat([tail.trim()]) : sents;
      const earlier = spokenSoFar + playQueue.map(q => q.text).join(" ") + (playing ? playing.text : "");
      const last = all[all.length - 1] || "";
      const isCloser = all.length >= 1 && earlier.length + all.slice(0, -1).join(" ").length >= 40 &&
        (/\?\s*$/.test(last) || CLOSER.test(last));
      (isCloser ? all.slice(0, -1) : all).forEach(enqueue);
    }
    replyText = ""; spokenIdx = 0;
    if (!playing && !playQueue.length) setState("listening"); else pump();
  };

  // Tell the server this is a spoken conversation so she writes for the ear.
  const _fetch = window.fetch.bind(window);
  window.fetch = function (url, opts) {
    if (on && typeof url === "string" && url.startsWith("/chat/stream") && opts && typeof opts.body === "string") {
      try { const b = JSON.parse(opts.body); b.voice = true; opts = { ...opts, body: JSON.stringify(b) }; } catch {}
    }
    return _fetch(url, opts);
  };
})();
