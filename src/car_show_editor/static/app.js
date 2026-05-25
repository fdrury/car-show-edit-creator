// Car Show Editor - single-page UI

const state = {
  step: "setup",
  projectName: null,
  project: null,        // server-side project object
};

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const root = $("#app");

const api = {
  async createProject(name, songPath, clipsFolder, signal) {
    const r = await fetch("/api/projects", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name, song_path: songPath, clips_folder: clipsFolder }),
      signal,
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async getProject(name) {
    const r = await fetch(`/api/projects/${encodeURIComponent(name)}`);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async saveProject(name, proj) {
    const r = await fetch(`/api/projects/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(proj),
    });
    if (!r.ok) throw new Error(await r.text());
  },
  async deleteProject(name) {
    const r = await fetch(`/api/projects/${encodeURIComponent(name)}`, { method: "DELETE" });
    if (!r.ok) throw new Error(await r.text());
  },
  async detectBeats(name, signal) {
    const r = await fetch(`/api/projects/${encodeURIComponent(name)}/detect_beats`, { method: "POST", signal });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async startRender(name) {
    const r = await fetch(`/api/projects/${encodeURIComponent(name)}/render`, { method: "POST" });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async renderStatus(jobId) {
    const r = await fetch(`/api/render/${encodeURIComponent(jobId)}`);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async listProjects() {
    const r = await fetch("/api/projects");
    return r.json();
  },
  mediaUrl(name, path) {
    return `/media/${encodeURIComponent(name)}?path=${encodeURIComponent(path)}`;
  },
};

function setStep(step) {
  state.step = step;
  updateBreadcrumbs();
  render();
}

// Enable/disable each breadcrumb based on what's currently loaded, and wire clicks.
function updateBreadcrumbs() {
  const hasProject = !!state.project;
  const hasBeats = hasProject && state.project.bpm > 0;
  const enabled = {
    setup: true,
    beats: hasProject,
    review: hasBeats,
    arrange: hasBeats,
  };
  $$("#steps span").forEach(el => {
    const step = el.dataset.step;
    el.classList.toggle("active", step === state.step);
    el.classList.toggle("disabled", !enabled[step]);
    el.onclick = (enabled[step] && step !== state.step) ? () => setStep(step) : null;
  });
}

async function saveProject() {
  await api.saveProject(state.projectName, state.project);
  updateBreadcrumbs();
}

// =================== SETUP SCREEN ===================

function renderSetup() {
  root.innerHTML = `
    <h2>New project</h2>
    <div class="setup-form">
      <label>Project name</label>
      <input id="proj-name" type="text" placeholder="e.g. supercar_sunday_2026_05" />
      <label>Song file (absolute path)</label>
      <input id="song-path" type="text" placeholder="C:\\Users\\Fred\\Music\\song.mp3" />
      <label>Clips folder (absolute path)</label>
      <input id="clips-folder" type="text" placeholder="C:\\Users\\Fred\\Videos\\car_show" />
      <div class="row" style="margin-top:16px;">
        <button id="btn-create">Create &amp; analyze</button>
        <button id="btn-cancel" class="danger" style="display:none;">Cancel</button>
        <span id="setup-status" style="color:#aaa;"></span>
      </div>
    </div>
    <h2 style="margin-top:32px;">Existing projects</h2>
    <div id="projects-list" style="color:#aaa;">loading...</div>
  `;

  api.listProjects().then(({ projects }) => {
    const list = $("#projects-list");
    if (!projects.length) {
      list.textContent = "(none)";
      return;
    }
    list.innerHTML = projects.map(n => `<div class="row"><button class="secondary" data-open="${n}">${n}</button></div>`).join("");
    $$("button[data-open]", list).forEach(b => {
      b.onclick = async () => {
        state.projectName = b.dataset.open;
        state.project = await api.getProject(state.projectName);
        // Decide which step to resume on
        if (!state.project.bpm) setStep("beats");
        else setStep("review");
      };
    });
  });

  const btn = $("#btn-create");
  const cancelBtn = $("#btn-cancel");
  const status = $("#setup-status");
  const inputs = ["#proj-name", "#song-path", "#clips-folder"].map(s => $(s));

  function setBusy(label) {
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span>${label}`;
    cancelBtn.style.display = "";
    inputs.forEach(i => { i.disabled = true; });
  }
  function setIdle() {
    btn.disabled = false;
    btn.innerHTML = "Create &amp; analyze";
    cancelBtn.style.display = "none";
    inputs.forEach(i => { i.disabled = false; });
  }
  function setStatus(text, kind) {
    status.innerHTML = text ? `<span class="status-pill${kind ? " " + kind : ""}">${text}</span>` : "";
  }

  btn.onclick = async () => {
    const name = $("#proj-name").value.trim();
    const songPath = $("#song-path").value.trim();
    const clipsFolder = $("#clips-folder").value.trim();
    if (!name || !songPath || !clipsFolder) {
      setStatus("Fill all fields", "error");
      return;
    }

    const ctrl = new AbortController();
    let createdName = null;
    cancelBtn.onclick = async () => {
      ctrl.abort();
      setStatus("Cancelling...");
      if (createdName) {
        try { await api.deleteProject(createdName); } catch (_) {}
      }
      setIdle();
      setStatus("Cancelled", "error");
    };

    setBusy("Creating project...");
    setStatus("Scanning clips folder + reading song...");
    try {
      const created = await api.createProject(name, songPath, clipsFolder, ctrl.signal);
      createdName = created.name;
      state.projectName = created.name;

      setBusy("Detecting beats...");
      setStatus(`Project created (${created.n_clips} clips). Running librosa — this can take ~30s on first analysis.`);

      const beats = await api.detectBeats(state.projectName, ctrl.signal);
      setStatus(`BPM ${beats.bpm.toFixed(1)} · ${beats.n_beats} beats · ${created.n_clips} clips`, "success");
      state.project = await api.getProject(state.projectName);
      setIdle();
      setStep("beats");
    } catch (e) {
      if (e.name === "AbortError") return;   // cancel flow already handled UI
      setIdle();
      setStatus("Error: " + e.message, "error");
    }
  };
}

// =================== BEATS SCREEN ===================

let beatPulseLoop = null;   // cancel handle for current pulse animation

function regridBeats(bpm, anchorTime) {
  // Uniform grid centered on anchorTime, spanning [0, duration).
  const p = state.project;
  const period = 60.0 / bpm;
  const before = [];
  for (let t = anchorTime - period; t >= 0; t -= period) before.unshift(t);
  const after = [];
  for (let t = anchorTime; t < p.duration; t += period) after.push(t);
  p.bpm = bpm;
  p.beat_times = [...before, ...after];
  p.start_beat_index = before.length;
}

function shiftAllBeats(deltaSec) {
  const p = state.project;
  let times = p.beat_times.map(t => t + deltaSec);
  // drop beats that landed outside the song
  let dropFront = 0;
  while (dropFront < times.length && times[dropFront] < 0) dropFront++;
  times = times.slice(dropFront).filter(t => t < p.duration);
  p.beat_times = times;
  p.start_beat_index = Math.min(Math.max(0, p.start_beat_index - dropFront), times.length - 1);
}

function renderBeats() {
  const p = state.project;
  if (!p) { setStep("setup"); return; }
  root.innerHTML = `
    <h2>Confirm beats &amp; pick start</h2>
    <p style="color:#aaa; font-size:13px; margin-top:0;">
      Play the song and watch the circle. If it pulses on the beat, detection is good.
      If it's slightly ahead/behind, use the nudge buttons. If totally off, override the BPM.
    </p>
    <div class="beats-main">
      <div>
        <div class="row">
          <span>BPM: <strong id="bpm-val">${p.bpm.toFixed(2)}</strong></span>
          <button id="btn-redetect" class="secondary">Re-detect</button>
          <label style="margin:0;">Override BPM:</label>
          <input id="bpm-override" type="number" step="0.1" value="${p.bpm.toFixed(2)}" style="width:90px;" />
          <button id="btn-apply-bpm" class="secondary">Apply</button>
        </div>
        <p style="color:#aaa; font-size:13px;">Click anywhere on the waveform to pick the "beat 1" start.</p>
        <div class="waveform-wrap">
          <canvas id="waveform" width="2000" height="140"></canvas>
          <div id="ticks"></div>
        </div>
        <audio id="song-audio" controls preload="auto" style="width:100%; margin-top:8px;" src="${api.mediaUrl(state.projectName, p.song_path)}"></audio>
        <div class="nudge-group" style="margin-top:14px;">
          <label>Nudge grid:</label>
          <button class="secondary" data-nudge="-0.050">−50ms</button>
          <button class="secondary" data-nudge="-0.010">−10ms</button>
          <button class="secondary" data-nudge="0.010">+10ms</button>
          <button class="secondary" data-nudge="0.050">+50ms</button>
        </div>
        <div class="nudge-group">
          <label>Nudge BPM:</label>
          <button class="secondary" data-bpm="-1">−1</button>
          <button class="secondary" data-bpm="-0.1">−0.1</button>
          <button class="secondary" data-bpm="0.1">+0.1</button>
          <button class="secondary" data-bpm="1">+1</button>
          <span style="color:#888; margin-left:8px; font-size:12px;">(half/double if librosa was off by 2x)</span>
          <button class="secondary" data-bpm-mul="0.5">÷2</button>
          <button class="secondary" data-bpm-mul="2">×2</button>
        </div>
      </div>
      <div>
        <div id="beat-circle" class="beat-circle">▶ Play song</div>
      </div>
    </div>
    <div class="beat-controls">
      <span>Start beat: <strong id="start-beat">${p.start_beat_index}</strong> @</span>
      <input id="start-time-input" type="number" step="0.001" min="0" value="${(p.beat_times[p.start_beat_index] || 0).toFixed(3)}" style="width:90px;" />
      <span style="color:#888; font-size:12px;">s (snaps to nearest beat on apply)</span>
      <button id="btn-apply-start" class="secondary">Apply</button>
      <button id="btn-start-earliest" class="secondary">⇤ earliest</button>
    </div>
    <div class="beat-controls" style="margin-top:8px; padding-top:8px; border-top:1px solid #2c2c2f;">
      <label style="margin:0;">Default segment length (beats):</label>
      <input id="default-len" type="number" step="0.5" min="0.5" value="${p.default_segment_beats}" style="width:80px;" title="0.5 increments"/>
      <button id="btn-bulk-len" class="secondary">Apply to all existing segments</button>
      <label style="margin:0;">Row offset (beats):</label>
      <select id="row-offset">
        ${[0,1,2,3,4].map(v => `<option value="${v}" ${v===p.row_offset_beats?"selected":""}>${v}</option>`).join("")}
      </select>
      <label style="margin:0;"><input type="checkbox" id="fill-gap" ${p.fill_initial_bot_gap === false ? "" : "checked"} /> fill bot gap at start</label>
      <label style="margin:0;">Song fade-out (beats):</label>
      <select id="song-fade">
        ${[0,2,4,8].map(v => `<option value="${v}" ${v===p.song_fade_out_beats?"selected":""}>${v}</option>`).join("")}
      </select>
      <span style="color:#888; font-size:12px;">
        Visible cut every <strong>${(p.row_offset_beats || p.default_segment_beats) || "?"} beat(s)</strong>
      </span>
      <button id="btn-to-review">Next: review clips →</button>
    </div>
  `;

  drawWaveform();
  drawBeatTicks();
  startBeatPulse();

  $("#waveform").onclick = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const xFrac = (e.clientX - rect.left) / rect.width;
    const t = xFrac * p.duration;
    let bestIdx = 0, bestDist = Infinity;
    for (let i = 0; i < p.beat_times.length; i++) {
      const d = Math.abs(p.beat_times[i] - t);
      if (d < bestDist) { bestDist = d; bestIdx = i; }
    }
    p.start_beat_index = bestIdx;
    $("#start-beat").textContent = bestIdx;
    const inp = $("#start-time-input");
    if (inp) inp.value = p.beat_times[bestIdx].toFixed(3);
    drawBeatTicks();
    saveProject();
    const a = $("#song-audio");
    if (a) a.currentTime = p.beat_times[bestIdx];
  };

  $("#btn-redetect").onclick = async () => {
    $("#bpm-val").textContent = "(detecting...)";
    await api.detectBeats(state.projectName);
    state.project = await api.getProject(state.projectName);
    renderBeats();
  };
  $("#btn-apply-bpm").onclick = async () => {
    const v = parseFloat($("#bpm-override").value);
    if (v > 0) {
      const anchor = p.beat_times[p.start_beat_index] || 0;
      regridBeats(v, anchor);
      await saveProject();
      renderBeats();
    }
  };

  $$("button[data-nudge]").forEach(b => {
    b.onclick = async () => {
      shiftAllBeats(parseFloat(b.dataset.nudge));
      await saveProject();
      renderBeats();
    };
  });
  $$("button[data-bpm]").forEach(b => {
    b.onclick = async () => {
      const anchor = p.beat_times[p.start_beat_index] || 0;
      regridBeats(Math.max(20, p.bpm + parseFloat(b.dataset.bpm)), anchor);
      await saveProject();
      renderBeats();
    };
  });
  $$("button[data-bpm-mul]").forEach(b => {
    b.onclick = async () => {
      const anchor = p.beat_times[p.start_beat_index] || 0;
      regridBeats(Math.max(20, p.bpm * parseFloat(b.dataset.bpmMul)), anchor);
      await saveProject();
      renderBeats();
    };
  });

  $("#default-len").onchange = (e) => {
    let v = parseFloat(e.target.value);
    if (!isFinite(v) || v < 0.5) v = 0.5;
    v = Math.round(v * 2) / 2;
    p.default_segment_beats = v;
    saveProject().then(renderBeats);
  };
  $("#btn-bulk-len").onclick = async () => {
    if (!confirm(`Set all ${p.segments.length} segments to length ${p.default_segment_beats} beats?`)) return;
    for (const s of p.segments) s.length_beats = p.default_segment_beats;
    await saveProject();
    renderBeats();
  };
  $("#row-offset").onchange = (e) => { p.row_offset_beats = parseInt(e.target.value, 10); saveProject().then(renderBeats); };
  $("#fill-gap").onchange = (e) => { p.fill_initial_bot_gap = e.target.checked; saveProject(); };
  $("#song-fade").onchange = (e) => { p.song_fade_out_beats = parseInt(e.target.value, 10); saveProject(); };

  function snapStartToTime(t) {
    if (!p.beat_times.length) return;
    let bestIdx = 0, bestDist = Infinity;
    for (let i = 0; i < p.beat_times.length; i++) {
      const d = Math.abs(p.beat_times[i] - t);
      if (d < bestDist) { bestDist = d; bestIdx = i; }
    }
    p.start_beat_index = bestIdx;
  }
  $("#btn-apply-start").onclick = async () => {
    const t = parseFloat($("#start-time-input").value);
    if (!isNaN(t) && t >= 0) {
      snapStartToTime(t);
      await saveProject();
      renderBeats();
    }
  };
  $("#start-time-input").onkeydown = (e) => { if (e.key === "Enter") $("#btn-apply-start").click(); };
  $("#btn-start-earliest").onclick = async () => {
    // Snap to whichever detected beat is closest to t=0 (the earliest playable start).
    if (p.beat_times.length) {
      p.start_beat_index = 0;
      await saveProject();
      renderBeats();
    }
  };

  $("#btn-to-review").onclick = async () => { await saveProject(); setStep("review"); };
}

function startBeatPulse() {
  // cancel any previous loop
  if (beatPulseLoop) { beatPulseLoop.cancelled = true; beatPulseLoop = null; }
  const audio = $("#song-audio");
  const circle = $("#beat-circle");
  if (!audio || !circle) return;
  const loop = { cancelled: false };
  beatPulseLoop = loop;

  let lastBeatTime = -1;
  let nextBeatIdx = 0;
  let flashUntil = 0;
  let strongFlashUntil = 0;

  audio.addEventListener("play", () => {
    // re-seek nextBeatIdx to current time
    const p = state.project;
    const t = audio.currentTime;
    nextBeatIdx = p.beat_times.findIndex(b => b > t);
    if (nextBeatIdx < 0) nextBeatIdx = p.beat_times.length;
    circle.textContent = "";
  });
  audio.addEventListener("pause", () => {
    if (!loop.cancelled) circle.textContent = "▶ Play song";
  });
  audio.addEventListener("seeked", () => {
    const p = state.project;
    const t = audio.currentTime;
    nextBeatIdx = p.beat_times.findIndex(b => b > t);
    if (nextBeatIdx < 0) nextBeatIdx = p.beat_times.length;
  });

  function step() {
    if (loop.cancelled || !document.body.contains(circle)) return;
    if (audio.paused) {
      requestAnimationFrame(step);
      return;
    }
    const p = state.project;
    const t = audio.currentTime;
    while (nextBeatIdx < p.beat_times.length && p.beat_times[nextBeatIdx] <= t) {
      const idx = nextBeatIdx;
      lastBeatTime = p.beat_times[idx];
      // Flash for EXACTLY half a beat (so the dim happens right on every half-beat).
      // Use the actual interval to the next detected beat so it stays musical under tempo drift.
      const nextBeat = idx + 1 < p.beat_times.length
        ? p.beat_times[idx + 1]
        : lastBeatTime + (p.bpm > 0 ? 60.0 / p.bpm : 0.5);
      const halfBeatSec = (nextBeat - lastBeatTime) / 2;
      const lagSec = Math.max(0, t - lastBeatTime);   // rAF lag since the beat actually fired
      const remainingMs = Math.max(0, (halfBeatSec - lagSec) * 1000);
      flashUntil = performance.now() + remainingMs;
      if (idx % 4 === 0) strongFlashUntil = flashUntil;
      nextBeatIdx++;
    }
    const now = performance.now();
    circle.classList.toggle("flash", now < flashUntil);
    circle.classList.toggle("flash-strong", now < strongFlashUntil);
    requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function drawWaveform() {
  // Light placeholder: just draw a simple gradient since we don't decode PCM client-side.
  const canvas = $("#waveform");
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "#1f1f24");
  grad.addColorStop(0.5, "#2a2a32");
  grad.addColorStop(1, "#1f1f24");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#3a3a44";
  ctx.fillRect(0, h/2 - 1, w, 2);
}

function drawBeatTicks() {
  const p = state.project;
  const wrap = $("#ticks");
  wrap.innerHTML = "";
  const canvas = $("#waveform");
  const canvasRect = canvas.getBoundingClientRect();
  const wrapRect = wrap.parentElement.getBoundingClientRect();
  // Position ticks absolutely within waveform-wrap
  p.beat_times.forEach((t, i) => {
    const x = (t / p.duration) * canvasRect.width;
    const div = document.createElement("div");
    div.className = "beat-tick" + (i === p.start_beat_index ? " start" : "") + (i % 4 === 0 ? " beat-strong" : "");
    div.style.left = (x + (canvasRect.left - wrapRect.left)) + "px";
    wrap.appendChild(div);
  });
}

// =================== REVIEW SCREEN ===================

let reviewActiveClip = null;     // path of clip currently shown

function renderReview() {
  const p = state.project;
  if (!p) { setStep("setup"); return; }
  // Source clips from the project (independent of segments — a clip can have 0 segments and stay listed).
  // Fallback to segment-derived list for older projects loaded before the `clips` field existed.
  const source = (p.clips && p.clips.length)
    ? [...p.clips]
    : [...new Set(p.segments.map(s => s.clip_path))];
  const clips = source.sort((a, b) => {
    const an = a.split(/[\\/]/).pop();
    const bn = b.split(/[\\/]/).pop();
    return an.localeCompare(bn, undefined, { numeric: true, sensitivity: "base" });
  });
  if (!clips.includes(reviewActiveClip)) reviewActiveClip = clips[0];

  root.innerHTML = `
    <h2>Review clips — set in-point, length, rotation, reverse</h2>
    <div class="clip-grid">
      <div class="clip-list" id="clip-list"></div>
      <div class="clip-review" id="clip-review"></div>
    </div>
    <div class="row" style="margin-top:24px;">
      <button class="secondary" id="btn-back-beats">← Beats</button>
      <button id="btn-to-arrange">Next: arrange &amp; render →</button>
    </div>
  `;

  const listEl = $("#clip-list");
  listEl.innerHTML = clips.map(cp => {
    const count = p.segments.filter(s => s.clip_path === cp).length;
    const name = cp.split(/[\\/]/).pop();
    const cls = cp === reviewActiveClip ? "active" : "";
    return `<div class="clip-list-item ${cls}" data-clip="${encodeURIComponent(cp)}"><span>${name}</span><span class="count">${count} seg</span></div>`;
  }).join("");
  $$(".clip-list-item", listEl).forEach(it => {
    it.onclick = () => {
      reviewActiveClip = decodeURIComponent(it.dataset.clip);
      // Update .active without rebuilding the list (keeps scroll position).
      $$(".clip-list-item", listEl).forEach(o => o.classList.toggle("active", o === it));
      renderClipDetail();
    };
  });

  renderClipDetail();

  $("#btn-back-beats").onclick = () => setStep("beats");
  $("#btn-to-arrange").onclick = async () => { await saveProject(); setStep("arrange"); };
}

function renderClipDetail() {
  const p = state.project;
  const cp = reviewActiveClip;
  if (!cp) return;
  const segs = p.segments.filter(s => s.clip_path === cp);
  const detail = $("#clip-review");
  detail.innerHTML = `
    <video id="clip-video" controls muted src="${api.mediaUrl(state.projectName, cp)}"></video>
    <div class="seg-timeline-wrap">
      <canvas id="seg-timeline" width="1600" height="32"></canvas>
    </div>
    <div class="seg-readout" id="seg-readout">In: <span class="in-time">—</span> → Out: <span class="out-time">—</span></div>
    <div class="row" style="margin-top:4px;">
      <button id="btn-set-in">Mark in-point</button>
      <button id="btn-jump-out" class="secondary">Jump to out</button>
      <label style="margin:0;">Length (beats):</label>
      <input id="cur-len" type="number" step="0.5" min="0.5" value="2" style="width:80px;" title="0.5 increments"/>
      <label style="margin:0;"><input type="checkbox" id="cur-rot"> rotate 180°</label>
      <label style="margin:0;"><input type="checkbox" id="cur-rev"> reverse</label>
      <button id="btn-preview" class="secondary">Preview slowed</button>
    </div>
    <div class="row" style="margin-top:4px; padding:8px; background:#222; border-radius:4px;">
      <label style="margin:0;"><input type="checkbox" id="cur-aud"> include audio</label>
      <label style="margin:0;">speed:</label>
      <select id="cur-speed">
        <option value="auto">auto</option>
        <option value="0.25">0.25×</option>
        <option value="0.5">0.5×</option>
        <option value="1">1×</option>
        <option value="2">2×</option>
      </select>
      <label style="margin:0;">fade in (s):</label>
      <input id="cur-fi" type="number" step="0.05" min="0" value="0" style="width:70px;" />
      <label style="margin:0;">fade out (s):</label>
      <input id="cur-fo" type="number" step="0.05" min="0" value="0" style="width:70px;" />
      <label style="margin:0;">gain (dB):</label>
      <input id="cur-gain" type="number" step="0.5" value="0" style="width:70px;" />
      <button id="btn-add-seg">Add segment</button>
    </div>
    <table class="segments-table">
      <thead><tr><th>#</th><th>In</th><th>Length</th><th>Speed</th><th>Rot</th><th>Rev</th><th>Audio</th><th></th></tr></thead>
      <tbody id="seg-rows"></tbody>
    </table>
  `;

  // Editing state for the next segment to be added
  const seed = segs[0];
  const editing = {
    in_time: seed?.in_time ?? 0,
    length_beats: seed?.length_beats ?? p.default_segment_beats,
    rotate_180: seed?.rotate_180 ?? false,
    reverse: seed?.reverse ?? false,
    audio_enabled: seed?.audio_enabled ?? false,
    audio_fade_in: seed?.audio_fade_in ?? 0,
    audio_fade_out: seed?.audio_fade_out ?? 0,
    audio_gain_db: seed?.audio_gain_db ?? 0,
    slowdown: (seed && seed.slowdown !== undefined) ? seed.slowdown : null,   // null = auto
  };
  $("#cur-len").value = String(editing.length_beats);
  $("#cur-rot").checked = editing.rotate_180;
  $("#cur-rev").checked = editing.reverse;
  $("#cur-aud").checked = editing.audio_enabled;
  $("#cur-speed").value = editing.slowdown === null ? "auto" : String(editing.slowdown);
  $("#cur-fi").value = editing.audio_fade_in;
  $("#cur-fo").value = editing.audio_fade_out;
  $("#cur-gain").value = editing.audio_gain_db;

  const video = $("#clip-video");
  const beatDur = p.bpm > 0 ? 60.0 / p.bpm : 0;

  function effSlow() {
    return editing.slowdown !== null ? editing.slowdown : (editing.audio_enabled ? 1.0 : p.slowdown);
  }
  function screenSec() { return editing.length_beats * beatDur; }
  function sourceSec() { return screenSec() * effSlow(); }
  function outTime() { return editing.in_time + sourceSec(); }

  function paintReadout() {
    const sec = screenSec(), src = sourceSec();
    $("#seg-readout").innerHTML = `
      In: <span class="in-time">${editing.in_time.toFixed(3)}s</span>
      → Out: <span class="out-time">${outTime().toFixed(3)}s</span>
      &nbsp;·&nbsp; ${editing.length_beats} beats
      &nbsp;·&nbsp; screen ${sec.toFixed(3)}s
      &nbsp;·&nbsp; source ${src.toFixed(3)}s
      &nbsp;·&nbsp; BPM ${p.bpm.toFixed(1)}
    `;
    drawSegTimeline();
  }

  function drawSegTimeline() {
    const canvas = $("#seg-timeline");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    const dur = video.duration && isFinite(video.duration) ? video.duration : 0;
    ctx.fillStyle = "#15151a";
    ctx.fillRect(0, 0, w, h);
    if (dur <= 0) {
      ctx.fillStyle = "#666";
      ctx.fillText("(loading...)", 6, h / 2 + 4);
      return;
    }
    // Beat ticks on the source timeline: where each output beat lands when this clip is slowed.
    // At slowdown=0.5, one output beat = 0.5 × beat_dur of source.
    const period = beatDur > 0 ? beatDur * p.slowdown : 0;
    if (period > 0) {
      ctx.fillStyle = "#2a2a32";
      for (let t = 0; t < dur; t += period) {
        ctx.fillRect((t / dur) * w, 0, 1, h);
      }
    }
    // in→out highlight
    const inX = (editing.in_time / dur) * w;
    const outX = (Math.min(outTime(), dur) / dur) * w;
    ctx.fillStyle = "rgba(108, 204, 238, 0.25)";
    ctx.fillRect(inX, 0, outX - inX, h);
    // in marker
    ctx.fillStyle = "#6ce";
    ctx.fillRect(inX - 1, 0, 2, h);
    // out marker
    ctx.fillStyle = "#fc6";
    ctx.fillRect(outX - 1, 0, 2, h);
    // playhead
    const ph = (video.currentTime / dur) * w;
    ctx.fillStyle = "white";
    ctx.fillRect(ph - 1, 0, 1, h);
  }

  paintReadout();

  // Click the timeline to scrub the video
  $("#seg-timeline").onclick = (e) => {
    const dur = video.duration;
    if (!dur) return;
    const rect = e.currentTarget.getBoundingClientRect();
    video.currentTime = ((e.clientX - rect.left) / rect.width) * dur;
  };

  video.addEventListener("loadedmetadata", paintReadout);
  video.addEventListener("timeupdate", drawSegTimeline);

  $("#btn-set-in").onclick = () => { editing.in_time = video.currentTime; paintReadout(); };
  $("#btn-jump-out").onclick = () => { video.currentTime = Math.min(outTime(), video.duration || outTime()); };
  $("#cur-len").onchange = (e) => {
    let v = parseFloat(e.target.value);
    if (!isFinite(v) || v < 0.5) v = 0.5;
    v = Math.round(v * 2) / 2;   // snap to 0.5
    e.target.value = String(v);
    editing.length_beats = v;
    paintReadout();
  };
  $("#cur-rot").onchange = (e) => { editing.rotate_180 = e.target.checked; };
  $("#cur-rev").onchange = (e) => { editing.reverse = e.target.checked; };
  $("#cur-aud").onchange = (e) => {
    editing.audio_enabled = e.target.checked;
    // No mutation here — when speed is "auto", effSlow() resolves to 1.0 (audio on) or project default (off).
    // If the user explicitly picked a speed, we leave their choice alone.
    paintReadout();
  };
  $("#cur-speed").onchange = (e) => {
    editing.slowdown = e.target.value === "auto" ? null : parseFloat(e.target.value);
    paintReadout();
  };
  $("#cur-fi").onchange = (e) => { editing.audio_fade_in = parseFloat(e.target.value) || 0; };
  $("#cur-fo").onchange = (e) => { editing.audio_fade_out = parseFloat(e.target.value) || 0; };
  $("#cur-gain").onchange = (e) => { editing.audio_gain_db = parseFloat(e.target.value) || 0; };

  $("#btn-preview").onclick = () => {
    const slow = effSlow();
    const sec = screenSec();
    video.currentTime = editing.in_time;
    video.playbackRate = slow;
    video.muted = !editing.audio_enabled;
    video.play();
    setTimeout(() => { video.pause(); video.playbackRate = 1.0; video.muted = true; }, sec * 1000);
  };

  // Re-paint just the segments table (and rewire its delete buttons), without rebuilding
  // the video element / editing controls — so the user's in-progress in-point survives.
  function paintSegments() {
    const liveSegs = state.project.segments.filter(s => s.clip_path === cp);
    const tbody = $("#seg-rows");
    tbody.innerHTML = liveSegs.map((s, i) => {
      const audDesc = s.audio_enabled
        ? `${s.audio_fade_in || 0}↗ / ${s.audio_fade_out || 0}↘${s.audio_gain_db ? ` ${s.audio_gain_db}dB` : ""}`
        : "—";
      const eff = effectiveSlowdown(s, p.slowdown);
      const speedLabel = s.slowdown == null ? `auto (${eff}×)` : `${eff}×`;
      return `
      <tr>
        <td>${i + 1}</td>
        <td>${s.in_time.toFixed(2)}s</td>
        <td>${s.length_beats}</td>
        <td>${speedLabel}</td>
        <td>${s.rotate_180 ? "✓" : ""}</td>
        <td>${s.reverse ? "✓" : ""}</td>
        <td>${audDesc}</td>
        <td><button class="danger" data-del="${s.id}">×</button></td>
      </tr>`;
    }).join("");
    $$("button[data-del]", tbody).forEach(b => {
      b.onclick = async () => {
        state.project.segments = state.project.segments.filter(s => s.id !== b.dataset.del);
        await saveProject();
        paintSegments();
        updateClipListCounts();
      };
    });
  }

  function updateClipListCounts() {
    $$("#clip-list .clip-list-item").forEach(item => {
      const itemCp = decodeURIComponent(item.dataset.clip);
      const count = state.project.segments.filter(s => s.clip_path === itemCp).length;
      const badge = item.querySelector(".count");
      if (badge) badge.textContent = `${count} seg`;
    });
  }

  $("#btn-add-seg").onclick = async () => {
    const currentSegs = state.project.segments.filter(s => s.clip_path === cp);
    // Mutate the auto-seeded default segment if it's untouched; else add a new one nearby.
    const seed = currentSegs.length === 1 && currentSegs[0].in_time === 0
                  && currentSegs[0].length_beats === p.default_segment_beats
                  && !currentSegs[0].rotate_180 && !currentSegs[0].reverse && !currentSegs[0].audio_enabled
      ? currentSegs[0] : null;
    // payload omits `row` so we don't overwrite the existing one when mutating.
    const payload = {
      clip_path: cp,
      in_time: editing.in_time,
      length_beats: editing.length_beats,
      rotate_180: editing.rotate_180,
      reverse: editing.reverse,
      audio_enabled: editing.audio_enabled,
      audio_fade_in: editing.audio_fade_in,
      audio_fade_out: editing.audio_fade_out,
      audio_gain_db: editing.audio_gain_db,
      slowdown: editing.slowdown,   // null = auto-couple to audio
    };
    if (seed) {
      Object.assign(seed, payload);
    } else {
      // Row: same as the last existing segment for this clip; default to alternating-balance otherwise.
      const lastForClip = currentSegs[currentSegs.length - 1];
      const newRow = lastForClip?.row ?? (
        p.segments.filter(s => s.row === "top").length
          <= p.segments.filter(s => s.row === "bottom").length ? "top" : "bottom"
      );
      const newSeg = { id: Math.random().toString(16).slice(2, 10), row: newRow, ...payload };
      // Insertion position — avoid "bumped to last" by placing it near existing segments for this clip:
      //   if any exist, right after the last one. Otherwise, after the alphabetically-prior clip's last segment.
      let insertAt = p.segments.length;
      if (currentSegs.length > 0) {
        insertAt = p.segments.lastIndexOf(lastForClip) + 1;
      } else {
        const clipsAlpha = [...(p.clips || [])].sort((a, b) =>
          a.split(/[\\/]/).pop().localeCompare(b.split(/[\\/]/).pop(), undefined, { numeric: true, sensitivity: "base" })
        );
        const cpAlphaIdx = clipsAlpha.indexOf(cp);
        let anchor = null;
        for (let i = cpAlphaIdx - 1; i >= 0 && !anchor; i--) {
          const candidates = p.segments.filter(s => s.clip_path === clipsAlpha[i]);
          if (candidates.length) anchor = candidates[candidates.length - 1];
        }
        insertAt = anchor ? p.segments.lastIndexOf(anchor) + 1 : 0;
      }
      p.segments.splice(insertAt, 0, newSeg);
    }
    await saveProject();
    paintSegments();
    updateClipListCounts();
  };

  paintSegments();
}

// =================== ARRANGE SCREEN ===================

function rowFor(seg, idx) {
  if (seg.row) return seg.row;
  return idx % 2 === 0 ? "top" : "bottom";
}

// Per-segment effective slowdown — mirrors render._effective_slowdown.
function effectiveSlowdown(seg, projectSlowdown) {
  if (seg.slowdown !== null && seg.slowdown !== undefined) return seg.slowdown;
  if (seg.audio_enabled) return 1.0;
  return projectSlowdown;
}

// Output-timeline seconds for `n` beats past start_beat_index — interpolates real beat_times
// so the JS preview cuts on the same beats as the render (avoids drift across long songs).
function beatToTime(p, n) {
  const beatDur = p.bpm > 0 ? 60.0 / p.bpm : 0;
  const bt = p.beat_times || [];
  if (!bt.length) return n * beatDur;
  const start = p.start_beat_index || 0;
  const base = bt[start];
  const absBeat = start + n;
  if (absBeat <= 0) return absBeat * beatDur - base;
  if (absBeat >= bt.length - 1) {
    const extra = (absBeat - (bt.length - 1)) * beatDur;
    return bt[bt.length - 1] + extra - base;
  }
  const lo = Math.floor(absBeat);
  const frac = absBeat - lo;
  return (bt[lo] + frac * (bt[lo + 1] - bt[lo])) - base;
}

// Compute every segment's effective length and output-timeline start time, mirroring the render pipeline.
function computeArrangement() {
  const p = state.project;
  const beatDur = p.bpm > 0 ? 60.0 / p.bpm : 0;
  const rows = [];
  let autoIdx = 0;
  for (const s of p.segments) {
    if (s.row === "top") rows.push("top");
    else if (s.row === "bottom") rows.push("bottom");
    else { rows.push(autoIdx % 2 === 0 ? "top" : "bottom"); autoIdx++; }
  }
  // User-set lengths are honored as-is. fill_initial_bot_gap controls whether bot starts at t=0.
  const fill = p.fill_initial_bot_gap !== false;
  const lens = p.segments.map(s => s.length_beats);
  // Per-segment screen_sec is derived from the actual beat_times (not uniform beatDur) so cuts
  // stay locked to the real beats even when librosa's detected tempo varies slightly.
  const starts = [];
  let topBeat = 0;
  let botBeat = fill ? 0 : (p.row_offset_beats || 0);
  const segScreenSec = new Array(p.segments.length);
  for (let i = 0; i < p.segments.length; i++) {
    if (rows[i] === "top") {
      starts.push(beatToTime(p, topBeat));
      segScreenSec[i] = beatToTime(p, topBeat + lens[i]) - beatToTime(p, topBeat);
      topBeat += lens[i];
    } else {
      starts.push(beatToTime(p, botBeat));
      segScreenSec[i] = beatToTime(p, botBeat + lens[i]) - beatToTime(p, botBeat);
      botBeat += lens[i];
    }
  }
  const outDur = Math.max(beatToTime(p, topBeat), beatToTime(p, botBeat));
  return { rows, lens, segScreenSec, starts, beatDur, outDur };
}

let arrangePreview = null;   // controller for active live preview

const PIXELS_PER_BEAT = 24;   // vertical pixels per beat in arrange columns

function renderArrange() {
  const p = state.project;
  if (!p) { setStep("setup"); return; }
  const { outDur } = computeArrangement();
  const songRemaining = p.duration - (p.beat_times[p.start_beat_index] || 0);

  root.innerHTML = `
    <h2>Arrange &amp; render</h2>
    <div class="summary">
      Segments: <strong>${p.segments.length}</strong> &nbsp;|&nbsp;
      Output: <strong>${outDur.toFixed(1)}s</strong> &nbsp;|&nbsp;
      Song after start: <strong>${songRemaining.toFixed(1)}s</strong> &nbsp;|&nbsp;
      <span style="color:#888;">Drag clips between/within columns to arrange.</span>
    </div>
    <div class="arrange-3col">
      <div class="arrange-scroll">
        <div class="preview-cursor" id="preview-cursor">
          <div class="preview-cursor-handle" id="preview-cursor-handle" title="Drag to scrub (when paused)"></div>
        </div>
        <div class="arrange-col arrange-col-top" data-row="top">
          <h3>Top row</h3>
          <div class="col-stack" id="col-top"></div>
        </div>
        <div class="arrange-col arrange-col-ruler">
          <h3>—</h3>
          <div class="col-stack" id="col-ruler"></div>
        </div>
        <div class="arrange-col arrange-col-bot" data-row="bottom">
          <h3>Bottom row</h3>
          <div class="col-stack" id="col-bot"></div>
        </div>
      </div>
      <div class="arrange-col preview-col">
        <h3>Preview</h3>
        <div class="preview-stack">
          <div class="preview-row" data-row="top">
            <video class="preview-vid" data-row="top" data-buf="a" muted playsinline preload="auto"></video>
            <video class="preview-vid" data-row="top" data-buf="b" muted playsinline preload="auto"></video>
            <video class="preview-vid" data-row="top" data-buf="c" muted playsinline preload="auto"></video>
          </div>
          <div class="preview-row" data-row="bottom">
            <video class="preview-vid" data-row="bottom" data-buf="a" muted playsinline preload="auto"></video>
            <video class="preview-vid" data-row="bottom" data-buf="b" muted playsinline preload="auto"></video>
            <video class="preview-vid" data-row="bottom" data-buf="c" muted playsinline preload="auto"></video>
          </div>
        </div>
        <audio id="preview-audio" preload="auto" src="${api.mediaUrl(state.projectName, p.song_path)}"></audio>
        <div class="preview-controls">
          <label style="margin:0; font-size:12px;">From #</label>
          <input id="preview-from" type="number" min="1" max="${p.segments.length}" value="1" style="width:60px;" />
          <button id="btn-preview-play">▶ Play</button>
          <button id="btn-preview-pause" class="secondary" disabled>❚❚ Pause</button>
          <button id="btn-preview-stop" class="secondary" disabled>■ Stop</button>
        </div>
        <div class="preview-now" id="preview-now">stopped</div>
        <p style="color:#888; font-size:11px; margin-top:8px; text-align:center;">
          Reverse not shown · switching may flicker briefly.
        </p>
      </div>
    </div>
    <div class="row" style="margin-top:16px;">
      <button class="secondary" id="btn-back-review">← Review</button>
      <button id="btn-render">Render to MP4</button>
      <span id="render-status" style="color:#aaa;"></span>
    </div>
    <div id="render-result"></div>
  `;

  paintColumns();
  wirePreview();
  // (Top and bot share a single scrollbar via the parent .arrange-scroll, so no JS scroll sync needed.)

  $("#btn-back-review").onclick = () => { stopPreview(); setStep("review"); };
  $("#btn-render").onclick = async () => {
    stopPreview();
    $("#render-status").textContent = "starting...";
    const { job_id } = await api.startRender(state.projectName);
    poll(job_id);
  };

  async function poll(jobId) {
    while (true) {
      await new Promise(r => setTimeout(r, 1000));
      const s = await api.renderStatus(jobId);
      $("#render-status").textContent = `${s.status} (${(s.progress * 100).toFixed(0)}%)`;
      if (s.status === "done" || s.status === "error") {
        $("#render-result").innerHTML = `
          <div class="render-output">
            <div><strong>${s.status === "done" ? "Render complete!" : "Render failed."}</strong></div>
            ${s.output ? `<div>Output: <a href="/api/output/${encodeURIComponent(s.output.split(/[\\/]/).pop())}" target="_blank">${s.output}</a></div>` : ""}
            <details><summary>Log</summary><pre class="log">${(s.log || []).join("\n")}</pre></details>
          </div>`;
        break;
      }
    }
  }
}

// Render the top & bottom columns with proportional-height segment blocks.
function paintColumns() {
  const p = state.project;
  const { lens } = computeArrangement();
  const fill = p.fill_initial_bot_gap !== false;

  // Segment indices grouped by row in segments[] order
  const topIdx = [];
  const botIdx = [];
  p.segments.forEach((s, i) => {
    if (s.row === "bottom") botIdx.push(i);
    else topIdx.push(i);   // null or "top" both go top
  });

  const colTop = $("#col-top");
  const colBot = $("#col-bot");
  colTop.innerHTML = "";
  colBot.innerHTML = "";

  // Optional leading gap visualization in bot column when fill is OFF and offset > 0
  if (!fill && p.row_offset_beats > 0) {
    const pad = document.createElement("div");
    pad.className = "col-bot-pad";
    pad.style.height = (p.row_offset_beats * PIXELS_PER_BEAT) + "px";
    pad.title = `${p.row_offset_beats}-beat leading gap (turn on "fill bot gap" in Beats to remove)`;
    colBot.appendChild(pad);
  }

  function blockFor(segIdx, displayBeats) {
    const s = p.segments[segIdx];
    const name = s.clip_path.split(/[\\/]/).pop();
    const flags = [
      s.rotate_180 ? "rot" : "",
      s.reverse ? "rev" : "",
      s.audio_enabled ? `♪${s.audio_fade_in||0}/${s.audio_fade_out||0}` : "",
    ].filter(Boolean).join(" ");
    const el = document.createElement("div");
    el.className = "seg-block";
    el.draggable = true;
    el.dataset.idx = String(segIdx);
    el.style.height = (displayBeats * PIXELS_PER_BEAT) + "px";
    // Position number stays in the tooltip — used by the "Preview from #" input on the right.
    el.title = `#${segIdx + 1}  ${name}  ${displayBeats}b  @${s.in_time.toFixed(2)}s${flags ? " · " + flags : ""}`;
    el.innerHTML = `
      <div class="seg-head">
        <span class="seg-name" data-cp="${encodeURIComponent(s.clip_path)}" title="Open ${name} in Review">${name}</span>
        <span class="seg-beats">${displayBeats}b</span>
      </div>
      <div class="seg-info">@${s.in_time.toFixed(2)}s${flags ? ` · <span class="seg-flags">${flags}</span>` : ""}</div>
    `;
    return el;
  }

  topIdx.forEach(i => colTop.appendChild(blockFor(i, lens[i])));
  botIdx.forEach(i => colBot.appendChild(blockFor(i, lens[i])));

  // Ruler column: measure.beat labels at every beat (gold-highlighted on measure starts).
  const ruler = $("#col-ruler");
  if (ruler) {
    ruler.innerHTML = "";
    const topTotal = topIdx.reduce((acc, i) => acc + lens[i], 0);
    const botTotal = botIdx.reduce((acc, i) => acc + lens[i], 0);
    const totalBeats = Math.max(topTotal, botTotal);
    for (let b = 0; b < totalBeats; b += 0.5) {
      // Skip half-beat ticks (they'd just clutter); only label whole beats.
      if (b % 1 !== 0) continue;
      const measure = Math.floor(b / 4) + 1;
      const beatInMeasure = (b % 4) + 1;
      const tick = document.createElement("div");
      tick.className = "ruler-tick" + (beatInMeasure === 1 ? " ruler-measure" : "");
      tick.style.height = PIXELS_PER_BEAT + "px";
      tick.textContent = `${measure}.${beatInMeasure}`;
      ruler.appendChild(tick);
    }
  }

  // Empty-drop area at end of each column (for dropping at the end)
  for (const col of [colTop, colBot]) {
    const drop = document.createElement("div");
    drop.className = "col-empty-drop";
    drop.dataset.row = col.parentElement.dataset.row;
    drop.textContent = "drop here";
    col.appendChild(drop);
  }

  wireDragAndDrop();
  precachePreviewBuffers();
}

// Eagerly load the first 3 segments of each row into the preview buffers so Play is instant.
// Re-run on every paintColumns so the buffers stay in sync with arrangement edits.
function precachePreviewBuffers() {
  const p = state.project;
  if (!p || !p.segments || !p.segments.length || !document.querySelector(".preview-vid")) return;
  // Skip while preview is running — startPreview manages buffers itself.
  if (arrangePreview && !arrangePreview.cancelled) return;
  const rows = [];
  for (const s of p.segments) rows.push(s.row === "bottom" ? "bottom" : "top");
  for (const rowName of ["top", "bottom"]) {
    const segList = [];
    for (let i = 0; i < rows.length; i++) if (rows[i] === rowName) segList.push(i);
    ["a", "b", "c"].forEach((key, k) => {
      const v = document.querySelector(`.preview-vid[data-row="${rowName}"][data-buf="${key}"]`);
      if (!v) return;
      const segIdx = k < segList.length ? segList[k] : -1;
      if (segIdx < 0) {
        if (v.dataset.url) {
          v.removeAttribute("src");
          delete v.dataset.url;
          v.classList.remove("rotated", "active");
          v.load();
        }
        return;
      }
      const seg = p.segments[segIdx];
      const url = api.mediaUrl(state.projectName, seg.clip_path);
      const slow = effectiveSlowdown(seg, p.slowdown);
      v.classList.toggle("rotated", !!seg.rotate_180);
      // Show the first segment of each row as a still frame so the preview pane isn't blank.
      if (k === 0) v.classList.add("active"); else v.classList.remove("active");
      const sameSrc = v.dataset.url === url;
      if (!sameSrc) {
        v.dataset.url = url;
        v.src = url;
        v.preload = "auto";
        v.muted = true;
      }
      const doSeek = () => {
        try { v.currentTime = seg.in_time; } catch (_) {}
        v.playbackRate = slow;
        try { v.pause(); } catch (_) {}
      };
      if (sameSrc && v.readyState >= 1) doSeek();
      else v.addEventListener("loadedmetadata", doSeek, { once: true });
    });
  }
}

function wireDragAndDrop() {
  let dragIdx = null;

  // Clip-name click → jump to Review with that clip selected.
  $$(".seg-block .seg-name").forEach(el => {
    el.onclick = (e) => {
      e.stopPropagation();
      const cp = decodeURIComponent(el.dataset.cp);
      reviewActiveClip = cp;
      stopPreview();
      setStep("review");
    };
  });

  $$(".seg-block").forEach(el => {
    el.ondragstart = (e) => {
      dragIdx = parseInt(el.dataset.idx, 10);
      el.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", String(dragIdx));
    };
    el.ondragend = () => { el.classList.remove("dragging"); $$(".seg-block, .col-empty-drop").forEach(x => x.classList.remove("drop-before", "drop-after", "drop-target")); };
    el.ondragover = (e) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const before = (e.clientY - rect.top) < rect.height / 2;
      el.classList.toggle("drop-before", before);
      el.classList.toggle("drop-after", !before);
    };
    el.ondragleave = () => el.classList.remove("drop-before", "drop-after");
    el.ondrop = (e) => {
      e.preventDefault();
      if (dragIdx == null) return;
      const targetIdx = parseInt(el.dataset.idx, 10);
      const rect = el.getBoundingClientRect();
      const insertBefore = (e.clientY - rect.top) < rect.height / 2;
      const targetRow = el.closest(".arrange-col").dataset.row;
      moveSegment(dragIdx, targetIdx, insertBefore, targetRow);
    };
  });

  $$(".col-empty-drop").forEach(drop => {
    drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("drop-target"); };
    drop.ondragleave = () => drop.classList.remove("drop-target");
    drop.ondrop = (e) => {
      e.preventDefault();
      drop.classList.remove("drop-target");
      if (dragIdx == null) return;
      // Drop at end of this column
      const targetRow = drop.dataset.row;
      moveSegmentToEnd(dragIdx, targetRow);
    };
  });
}

async function moveSegment(srcIdx, targetIdx, insertBefore, targetRow) {
  if (srcIdx === targetIdx) return;
  const arr = state.project.segments;
  const [moved] = arr.splice(srcIdx, 1);
  moved.row = targetRow;
  let adjustedTarget = targetIdx;
  if (srcIdx < targetIdx) adjustedTarget -= 1;
  const insertAt = insertBefore ? adjustedTarget : adjustedTarget + 1;
  arr.splice(insertAt, 0, moved);
  await saveProject();
  paintColumns();
}

async function moveSegmentToEnd(srcIdx, targetRow) {
  const arr = state.project.segments;
  const [moved] = arr.splice(srcIdx, 1);
  moved.row = targetRow;
  arr.push(moved);
  await saveProject();
  paintColumns();
}

// ---------------- Arrange preview ----------------

function wirePreview() {
  $("#btn-preview-play").onclick = () => {
    const fromInput = $("#preview-from");
    const fromIdx = Math.max(0, Math.min((parseInt(fromInput?.value, 10) || 1) - 1, state.project.segments.length - 1));
    startPreview(fromIdx);
  };
  $("#btn-preview-pause").onclick = () => {
    if (!arrangePreview) return;
    if (arrangePreview.paused) resumePreview();
    else pausePreview();
  };
  $("#btn-preview-stop").onclick = stopPreview;
}

function pausePreview() {
  if (!arrangePreview || arrangePreview.paused) return;
  arrangePreview.paused = true;
  const audio = $("#preview-audio");
  if (audio) { try { audio.pause(); } catch (_) {} }
  $$(".preview-vid.active").forEach(v => { try { v.pause(); } catch (_) {} });
  const pauseBtn = $("#btn-preview-pause");
  if (pauseBtn) pauseBtn.textContent = "▶ Resume";
  const cursor = $("#preview-cursor");
  if (cursor) cursor.classList.add("paused");
  // (Cursor & highlights stay in place — the rAF loop keeps running but t doesn't advance.)
}

function resumePreview() {
  if (!arrangePreview || !arrangePreview.paused) return;
  arrangePreview.paused = false;
  const audio = $("#preview-audio");
  if (audio) { audio.play().catch(() => {}); }
  $$(".preview-vid.active").forEach(v => { v.play().catch(() => {}); });
  const pauseBtn = $("#btn-preview-pause");
  if (pauseBtn) pauseBtn.textContent = "❚❚ Pause";
  const cursor = $("#preview-cursor");
  if (cursor) cursor.classList.remove("paused");
}

function startPreview(fromIdx = 0) {
  stopPreview();
  const p = state.project;
  const { rows, lens, segScreenSec, starts, beatDur, outDur } = computeArrangement();
  const songStart = p.beat_times[p.start_beat_index] || 0;
  const segOffset = (fromIdx >= 0 && fromIdx < starts.length) ? starts[fromIdx] : 0;

  const audio = $("#preview-audio");
  const nowEl = $("#preview-now");
  const playBtn = $("#btn-preview-play");
  const stopBtn = $("#btn-preview-stop");
  const pauseBtn = $("#btn-preview-pause");

  // ===== 3-buffer rotation per row =====
  // bufs[0] is always the currently-visible/playing buffer.
  // bufs[1] holds the *next* segment in this row (preloaded).
  // bufs[2] holds the segment *after that* (preloaded — gives short half-beat segments time to load).
  // On a sequential transition, we rotate left: bufs.shift()→push, then load the new "+2 ahead" onto bufs[2].
  function listFor(rowName) {
    const out = [];
    for (let i = 0; i < rows.length; i++) if (rows[i] === rowName) out.push(i);
    return out;
  }
  function bufsFor(rowName) {
    return ["a", "b", "c"].map(k =>
      document.querySelector(`.preview-vid[data-row="${rowName}"][data-buf="${k}"]`),
    );
  }
  const db = {
    top:    { bufs: bufsFor("top"),    segIdxOnBuf: [-1, -1, -1], segList: listFor("top"),    listPos: -1 },
    bottom: { bufs: bufsFor("bottom"), segIdxOnBuf: [-1, -1, -1], segList: listFor("bottom"), listPos: -1 },
  };

  function preloadSegment(videoEl, segIdx) {
    if (segIdx < 0) {
      try { videoEl.pause(); } catch (_) {}
      videoEl.removeAttribute("src");
      delete videoEl.dataset.url;
      videoEl.classList.remove("rotated");
      videoEl.load();
      return;
    }
    const seg = p.segments[segIdx];
    const url = api.mediaUrl(state.projectName, seg.clip_path);
    const slow = effectiveSlowdown(seg, p.slowdown);
    videoEl.classList.toggle("rotated", !!seg.rotate_180);
    const sameSrc = videoEl.dataset.url === url;
    if (!sameSrc) {
      videoEl.dataset.url = url;
      videoEl.src = url;
      videoEl.preload = "auto";
    }
    const doSeek = () => {
      try { videoEl.currentTime = seg.in_time; } catch (_) {}
      videoEl.playbackRate = slow;
    };
    if (sameSrc && videoEl.readyState >= 1) doSeek();
    else videoEl.addEventListener("loadedmetadata", doSeek, { once: true });
    try { videoEl.pause(); } catch (_) {}
  }

  function playSegmentDirect(videoEl, segIdx, offsetIntoSeg) {
    if (segIdx < 0) { try { videoEl.pause(); } catch (_) {} return; }
    const seg = p.segments[segIdx];
    const slow = effectiveSlowdown(seg, p.slowdown);
    const url = api.mediaUrl(state.projectName, seg.clip_path);
    if (videoEl.dataset.url !== url) {
      videoEl.dataset.url = url;
      videoEl.src = url;
    }
    const sourceT = seg.in_time + Math.max(0, offsetIntoSeg) * slow;
    try { videoEl.currentTime = sourceT; } catch (_) {}
    videoEl.playbackRate = slow;
    videoEl.classList.toggle("rotated", !!seg.rotate_180);
    videoEl.play().catch(() => {});
  }

  function pauseAllBufs(s) {
    s.bufs.forEach(b => { try { b.pause(); } catch (_) {} });
  }
  function showOnly(s, idx) {
    s.bufs.forEach((b, i) => b.classList.toggle("active", i === idx));
  }
  function curListPosFor(s, t) {
    for (let j = 0; j < s.segList.length; j++) {
      const i = s.segList[j];
      if (t >= starts[i] && t < starts[i] + segScreenSec[i]) return j;
    }
    return -1;
  }

  // Full reset: load 3 buffers from listPos onward and play bufs[0].
  function jumpRow(rowName, listPos, t) {
    const s = db[rowName];
    pauseAllBufs(s);
    const want = [0, 1, 2].map(k => {
      const lp = listPos + k;
      return (lp >= 0 && lp < s.segList.length) ? s.segList[lp] : -1;
    });
    for (let k = 0; k < 3; k++) {
      s.segIdxOnBuf[k] = want[k];
      if (k === 0) continue;   // bufs[0] is loaded via playSegmentDirect below
      preloadSegment(s.bufs[k], want[k]);
    }
    if (listPos >= 0 && want[0] >= 0) {
      playSegmentDirect(s.bufs[0], want[0], t - starts[want[0]]);
      showOnly(s, 0);
    } else {
      showOnly(s, -1);
    }
    s.listPos = listPos;
  }

  // Sequential advance: rotate buffers, promote bufs[1] to active, load new far-future onto recycled bufs[2].
  function advanceRow(rowName) {
    const s = db[rowName];
    const newListPos = s.listPos + 1;
    if (newListPos >= s.segList.length) {
      pauseAllBufs(s);
      showOnly(s, -1);
      s.listPos = newListPos;
      return;
    }
    try { s.bufs[0].pause(); } catch (_) {}
    s.bufs[0].classList.remove("active");
    // Rotate left: old active goes to end (recycled).
    s.bufs.push(s.bufs.shift());
    s.segIdxOnBuf.push(s.segIdxOnBuf.shift());
    const newActive = s.bufs[0];
    const newActiveSeg = s.segIdxOnBuf[0];
    if (newActiveSeg >= 0) {
      if (newActive.readyState >= 1) {
        // Fast path: buffer pre-seeked, just play.
        const seg = p.segments[newActiveSeg];
        newActive.playbackRate = effectiveSlowdown(seg, p.slowdown);
        newActive.play().catch(() => {});
      } else {
        // Buffer wasn't ready in time — fall back to direct play.
        playSegmentDirect(newActive, newActiveSeg, 0);
      }
      newActive.classList.add("active");
    }
    // Load the seg 2 ahead of the new active onto the recycled bufs[2].
    const farListPos = newListPos + 2;
    const farSeg = farListPos < s.segList.length ? s.segList[farListPos] : -1;
    s.segIdxOnBuf[2] = farSeg;
    preloadSegment(s.bufs[2], farSeg);
    s.listPos = newListPos;
  }

  // Past-end-of-song guard: if the requested start is past where the song will play,
  // tell the user instead of silently starting from the beginning (audio gets clamped + ended fires).
  const songRemaining = Math.max(0, p.duration - songStart);
  if (segOffset >= songRemaining - 0.05) {
    const nowEarly = $("#preview-now");
    if (nowEarly) nowEarly.textContent = `seg #${fromIdx + 1} starts past end of song (${segOffset.toFixed(1)}s ≥ ${songRemaining.toFixed(1)}s available)`;
    return;
  }

  audio.currentTime = songStart + segOffset;
  audio.play().catch(() => {});
  playBtn.disabled = true;
  stopBtn.disabled = false;
  if (pauseBtn) { pauseBtn.disabled = false; pauseBtn.textContent = "❚❚ Pause"; }

  jumpRow("top", curListPosFor(db.top, segOffset), segOffset);
  jumpRow("bottom", curListPosFor(db.bottom, segOffset), segOffset);

  let curTop = db.top.listPos >= 0 ? db.top.segList[db.top.listPos] : -1;
  let curBot = db.bottom.listPos >= 0 ? db.bottom.segList[db.bottom.listPos] : -1;
  updatePlayingHighlight(curTop, curBot);

  const ctrl = { cancelled: false };
  arrangePreview = ctrl;

  // Wire cursor handle for scrubbing while paused — MUST come after arrangePreview is set
  // so cleanupDrag can be attached.
  const handle = $("#preview-cursor-handle");
  if (handle) {
    let drag = null;
    handle.onmousedown = (e) => {
      if (!arrangePreview || !arrangePreview.paused) return;
      e.preventDefault();
      // Capture the current cursor beat-position to anchor the drag in beat-space.
      const currentT = audio.currentTime - songStart;
      let startBeats = 0, cum = 0;
      for (const i of db.top.segList) {
        const segSec = segScreenSec[i];
        if (currentT < starts[i] + segSec) {
          startBeats = cum + Math.max(0, (currentT - starts[i]) / segSec) * lens[i];
          break;
        }
        cum += lens[i];
        startBeats = cum;
      }
      drag = { startY: e.clientY, startBeats };
    };
    const onMove = (e) => {
      if (!drag) return;
      // dy maps to a delta in beat units (visual blocks are PIXELS_PER_BEAT per beat).
      // Convert beat delta to a time delta by interpolating real beat_times.
      const dy = e.clientY - drag.startY;
      const startBeats = drag.startBeats;
      const newBeats = Math.max(0, startBeats + (dy / PIXELS_PER_BEAT));
      const newT = Math.max(0, Math.min(beatToTime(p, newBeats), outDur - 0.01));
      audio.currentTime = songStart + newT;
    };
    const onUp = () => { drag = null; };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    ctrl.cleanupDrag = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
  }

  function step() {
    if (ctrl.cancelled) return;
    if (!document.body.contains(audio)) { stopPreview(); return; }
    const t = audio.currentTime - songStart;
    if (t >= outDur || audio.ended) { stopPreview(); return; }

    for (const rowName of ["top", "bottom"]) {
      const s = db[rowName];
      const expected = curListPosFor(s, t);
      if (expected === s.listPos) continue;
      if (expected === s.listPos + 1) advanceRow(rowName);
      else jumpRow(rowName, expected, t);   // out-of-order — hard reset
    }

    const topIdx = db.top.listPos >= 0 && db.top.listPos < db.top.segList.length ? db.top.segList[db.top.listPos] : -1;
    const botIdx = db.bottom.listPos >= 0 && db.bottom.listPos < db.bottom.segList.length ? db.bottom.segList[db.bottom.listPos] : -1;
    if (topIdx !== curTop || botIdx !== curBot) {
      curTop = topIdx; curBot = botIdx;
      updatePlayingHighlight(topIdx, botIdx);
    }
    // Cursor position in BEATS along the top row (matches the uniformly-sized visual blocks).
    let cursorBeats = -1;
    let cum = 0;
    for (const i of db.top.segList) {
      const segSec = segScreenSec[i];
      if (t < starts[i]) { cursorBeats = cum; break; }
      if (t < starts[i] + segSec) {
        cursorBeats = cum + ((t - starts[i]) / segSec) * lens[i];
        break;
      }
      cum += lens[i];
    }
    if (cursorBeats < 0) cursorBeats = cum;
    updatePlayheadCursor(cursorBeats);
    nowEl.textContent = `t=${t.toFixed(2)}s  top #${topIdx >= 0 ? topIdx+1 : "—"}  bot #${botIdx >= 0 ? botIdx+1 : "—"}`;
    requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function updatePlayingHighlight(topIdx, botIdx) {
  $$(".seg-block.playing").forEach(el => el.classList.remove("playing"));
  for (const idx of [topIdx, botIdx]) {
    if (idx < 0) continue;
    const el = document.querySelector(`.seg-block[data-idx="${idx}"]`);
    if (el) el.classList.add("playing");
  }
}

function updatePlayheadCursor(cursorBeats) {
  // Cursor y is derived from BEAT-count progress through the top row, not from absolute time,
  // so it stays glued to the visually-uniform blocks even when the song's beats aren't uniform.
  const cursor = $("#preview-cursor");
  const stack = $("#col-top");
  const scroll = $(".arrange-scroll");
  if (!cursor || !stack || !scroll || cursorBeats < 0) return;
  const baseY = stack.offsetTop;
  const y = baseY + cursorBeats * PIXELS_PER_BEAT;
  cursor.style.top = y + "px";
  cursor.classList.add("active");
  const margin = 60;
  if (y < scroll.scrollTop + margin) scroll.scrollTop = Math.max(0, y - margin);
  else if (y > scroll.scrollTop + scroll.clientHeight - margin) scroll.scrollTop = y - scroll.clientHeight + margin;
}

function clearPlayingHighlightAndCursor() {
  $$(".seg-block.playing").forEach(el => el.classList.remove("playing"));
  const cursor = $("#preview-cursor");
  if (cursor) cursor.classList.remove("active", "paused");
}

function stopPreview() {
  if (arrangePreview) {
    arrangePreview.cancelled = true;
    if (arrangePreview.cleanupDrag) arrangePreview.cleanupDrag();
    arrangePreview = null;
  }
  clearPlayingHighlightAndCursor();
  const audio = $("#preview-audio");
  const playBtn = $("#btn-preview-play");
  const stopBtn = $("#btn-preview-stop");
  const nowEl = $("#preview-now");
  if (audio) { try { audio.pause(); } catch (_) {} }
  // Pause all buffers but keep their loaded srcs — precachePreviewBuffers will restore the
  // first-segment thumbnail. Re-using loaded buffers also makes the next Play instant.
  $$(".preview-vid").forEach(v => {
    try { v.pause(); } catch (_) {}
    v.classList.remove("active");
  });
  if (playBtn) playBtn.disabled = false;
  if (stopBtn) stopBtn.disabled = true;
  const pauseBtn = $("#btn-preview-pause");
  if (pauseBtn) { pauseBtn.disabled = true; pauseBtn.textContent = "❚❚ Pause"; }
  if (nowEl) nowEl.textContent = "stopped";
  precachePreviewBuffers();
}

// =================== ROUTER ===================

function render() {
  if (state.step === "setup") renderSetup();
  else if (state.step === "beats") renderBeats();
  else if (state.step === "review") renderReview();
  else if (state.step === "arrange") renderArrange();
}

updateBreadcrumbs();
render();
