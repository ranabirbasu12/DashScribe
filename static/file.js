// static/file.js
// Owns the entire #file-mode page lifecycle. No innerHTML — all DOM via createElement.
(function () {
    let ws = null;
    let currentJob = null;            // {job_id}
    let currentPayload = null;        // unified transcript payload
    let optionsDefaults = null;
    let sidebarVisible = true;

    const el = (id) => document.getElementById(id);
    const dropzone = () => el("file-dropzone");
    const stateEmpty = () => el("file-empty");
    const stateWorking = () => el("file-transcribing");
    const stateResult = () => el("file-result-view");

    function setState(name) {
        for (const s of [stateEmpty(), stateWorking(), stateResult()]) {
            s.classList.add("hidden");
        }
        ({ empty: stateEmpty(), working: stateWorking(), result: stateResult() }[name]).classList.remove("hidden");
    }

    function clearChildren(node) {
        while (node.firstChild) node.removeChild(node.firstChild);
    }

    function getOptions() {
        return {
            engine: el("opt-engine").value,
            quality_preset: el("opt-quality").value,
            language: el("opt-language").value,
            task: el("opt-task").value,
            diarization_enabled: el("opt-diarize").checked,
            diarization_engine: document.getElementById("enhanced-diarize-toggle")?.checked
                ? "pyannote-community-1" : "sherpa-onnx",
            speaker_count: el("opt-speakers").value === "auto" ? "auto" : parseInt(el("opt-speakers").value, 10),
            initial_prompt: el("opt-prompt").value,
            timestamp_granularity: el("opt-ts").value,
            temperature: parseFloat(el("opt-temp").value || 0),
            beam_size: el("opt-beam").value ? parseInt(el("opt-beam").value, 10) : null,
        };
    }

    function applyOptions(o) {
        if (!o) return;
        if (o.engine) el("opt-engine").value = o.engine;
        if (o.quality_preset) el("opt-quality").value = o.quality_preset;
        if (o.language) el("opt-language").value = o.language;
        if (o.task) el("opt-task").value = o.task;
        el("opt-diarize").checked = !!o.diarization_enabled;
        el("opt-speakers").value = String(o.speaker_count ?? "auto");
        el("opt-prompt").value = o.initial_prompt || "";
        if (o.timestamp_granularity) el("opt-ts").value = o.timestamp_granularity;
        if (typeof o.temperature === "number") el("opt-temp").value = o.temperature;
        if (o.beam_size) el("opt-beam").value = o.beam_size;
    }

    async function loadDefaults() {
        try {
            const r = await fetch("/api/file-job/options-defaults");
            optionsDefaults = await r.json();
            applyOptions(optionsDefaults);
        } catch (_) { /* ignore */ }
    }

    function persistOptions() {
        const opts = getOptions();
        fetch("/api/file-job/options-defaults", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(opts),
        }).catch(() => {});
    }

    function startJobFromPath(path) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        setState("working");
        ws.send(JSON.stringify({ action: "start_file_job", path, options: getOptions() }));
    }

    async function startJobFromUrl(url) {
        const r = await fetch("/api/file-job/from-url", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url }),
        });
        if (!r.ok) {
            window.alert("URL fetch failed");
            return;
        }
        const { path } = await r.json();
        startJobFromPath(path);
    }

    function bindEmptyState() {
        const dz = dropzone();
        ["dragenter", "dragover"].forEach(evt => {
            dz.addEventListener(evt, (e) => { e.preventDefault(); dz.classList.add("drag-over"); });
        });
        ["dragleave", "drop"].forEach(evt => {
            dz.addEventListener(evt, (e) => { e.preventDefault(); dz.classList.remove("drag-over"); });
        });
        dz.addEventListener("drop", (e) => {
            const file = e.dataTransfer.files[0];
            if (!file) return;
            if (file.path) {
                startJobFromPath(file.path);
            } else {
                window.alert("Please use the Browse button — drop only works in the desktop app.");
            }
        });

        el("file-browse-btn").addEventListener("click", async () => {
            const r = await fetch("/api/browse-file");
            const data = await r.json();
            if (data.path) startJobFromPath(data.path);
        });

        el("file-url").addEventListener("keydown", (e) => {
            if (e.key === "Enter" && e.target.value.trim()) {
                startJobFromUrl(e.target.value.trim());
            }
        });

        el("file-sample-btn").addEventListener("click", () => {
            startJobFromPath("__sample__");
        });
    }

    function bindSidebar() {
        ["opt-engine", "opt-quality", "opt-language", "opt-task", "opt-diarize",
         "opt-speakers", "opt-prompt", "opt-ts", "opt-temp", "opt-beam"].forEach(id => {
            el(id).addEventListener("change", persistOptions);
        });
        el("file-sidebar-toggle").addEventListener("click", () => {
            sidebarVisible = !sidebarVisible;
            document.querySelector(".file-layout").classList.toggle("sidebar-collapsed", !sidebarVisible);
        });
        document.addEventListener("keydown", (e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "0") {
                e.preventDefault();
                el("file-sidebar-toggle").click();
            }
        });
    }

    function setWs(newWs) { ws = newWs; }

    function fmtClock(seconds) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);
        return [h, m, s].map(n => String(n).padStart(2, "0")).join(":");
    }

    function speakerById(id) {
        return (currentPayload?.speakers || []).find(s => s.id === id);
    }

    function buildSpeakerChip(turn) {
        const sp = speakerById(turn.speaker_id) || { label: turn.speaker_id, color: "#888" };
        const chip = document.createElement("span");
        chip.className = "speaker-chip";
        chip.style.setProperty("--speaker-color", sp.color);
        chip.textContent = sp.label;
        chip.addEventListener("click", () => beginRenameSpeaker(turn.speaker_id, chip));
        return chip;
    }

    function buildWordSpan(text, start, end, isLow) {
        const span = document.createElement("span");
        span.className = "transcript-word" + (isLow ? " low-confidence" : "");
        span.dataset.start = String(start);
        span.dataset.end = String(end);
        span.textContent = text + " ";
        span.addEventListener("click", () => seekTo(start));
        return span;
    }

    function renderTranscript() {
        const root = el("file-transcript");
        clearChildren(root);
        if (!currentPayload) return;

        // Group consecutive same-speaker segments into turns
        const turns = [];
        for (const seg of currentPayload.segments) {
            const last = turns[turns.length - 1];
            if (last && last.speaker_id === seg.speaker_id) {
                last.segments.push(seg);
            } else {
                turns.push({ speaker_id: seg.speaker_id, start: seg.start, segments: [seg] });
            }
        }

        for (const turn of turns) {
            const turnEl = document.createElement("div");
            turnEl.className = "transcript-turn";
            turnEl.dataset.speakerId = turn.speaker_id;

            const header = document.createElement("div");
            header.className = "transcript-turn-header";
            header.appendChild(buildSpeakerChip(turn));

            const ts = document.createElement("span");
            ts.className = "transcript-timestamp";
            ts.textContent = fmtClock(turn.start);
            ts.addEventListener("click", () => seekTo(turn.start));
            header.appendChild(ts);
            turnEl.appendChild(header);

            const textEl = document.createElement("div");
            textEl.className = "transcript-text";
            textEl.contentEditable = "plaintext-only";
            textEl.addEventListener("blur", persistEdits);

            for (const seg of turn.segments) {
                if (seg.words && seg.words.length) {
                    for (const w of seg.words) {
                        textEl.appendChild(buildWordSpan(w.text, w.start, w.end, w.prob < 0.5));
                    }
                } else {
                    const isLow = (seg.no_speech_prob ?? 0) > 0.4;
                    textEl.appendChild(buildWordSpan(seg.text, seg.start, seg.end, isLow));
                }
            }
            turnEl.appendChild(textEl);
            root.appendChild(turnEl);
        }
    }

    function beginRenameSpeaker(speakerId, chipEl) {
        const sp = speakerById(speakerId);
        if (!sp) return;
        const input = document.createElement("input");
        input.className = "speaker-chip-input";
        input.value = sp.label;
        chipEl.replaceWith(input);
        input.focus();
        input.select();
        const finish = (commit) => {
            if (commit && input.value.trim() && input.value !== sp.label) {
                sp.label = input.value.trim();
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({
                        action: "update_speaker_label",
                        job_id: currentJob.job_id,
                        speaker_id: speakerId,
                        label: sp.label,
                    }));
                }
            }
            renderTranscript();
        };
        input.addEventListener("keydown", (e) => {
            if (e.key === "Enter") { e.preventDefault(); finish(true); }
            else if (e.key === "Escape") finish(false);
        });
        input.addEventListener("blur", () => finish(true));
    }

    function persistEdits() {
        if (!currentPayload || !ws || ws.readyState !== WebSocket.OPEN) return;
        // Replace each turn's first segment text with the edited concatenation; blank its tail segments.
        const turnEls = el("file-transcript").querySelectorAll(".transcript-turn");
        const segments = [];
        let segIdx = 0;
        for (const turnEl of turnEls) {
            const speakerId = turnEl.dataset.speakerId;
            const text = turnEl.querySelector(".transcript-text").innerText.trim();
            const turnSegments = [];
            while (segIdx < currentPayload.segments.length &&
                   currentPayload.segments[segIdx].speaker_id === speakerId) {
                turnSegments.push(currentPayload.segments[segIdx]);
                segIdx++;
            }
            if (turnSegments.length === 0) continue;
            turnSegments[0].text = text;
            for (let i = 1; i < turnSegments.length; i++) turnSegments[i].text = "";
            for (const s of turnSegments) segments.push(s);
        }
        currentPayload.segments = segments;
        ws.send(JSON.stringify({
            action: "save_transcript_edits",
            job_id: currentJob.job_id,
            segments,
        }));
    }

    function seekTo(seconds) {
        const a = el("file-audio");
        if (!a.src) return;
        a.currentTime = seconds;
        a.play().catch(() => {});
    }

    function bindAudioSync() {
        const audio = el("file-audio");
        let raf = 0;
        const tick = () => {
            const t = audio.currentTime;
            const words = el("file-transcript").querySelectorAll(".transcript-word");
            for (const w of words) {
                const start = parseFloat(w.dataset.start);
                const end = parseFloat(w.dataset.end);
                if (t >= start && t < end) w.classList.add("playing");
                else w.classList.remove("playing");
            }
            raf = requestAnimationFrame(tick);
        };
        audio.addEventListener("play", () => { cancelAnimationFrame(raf); tick(); });
        audio.addEventListener("pause", () => cancelAnimationFrame(raf));
    }

    function showResult(payload) {
        currentPayload = payload;
        const filename = (payload.audio_path || "").split("/").pop();
        el("file-result-filename").textContent = filename;
        el("file-result-meta").textContent =
            fmtClock(payload.duration_seconds) + " • " + payload.speakers.length + " speaker(s) • " + payload.engine;
        el("file-audio").src = "/api/file-job/" + currentJob.job_id + "/audio";
        renderTranscript();
        setState("result");
    }

    function showWorking() {
        clearChildren(el("file-stage-list"));
        el("file-progress-fill").style.width = "0%";
        el("file-progress-message").textContent = "Starting…";
        setState("working");
    }

    function updateProgress(msg) {
        const stages = ["probed", "extracting", "transcribing", "diarizing", "done"];
        const labels = {
            probed: "Loaded", extracting: "Extracting audio",
            transcribing: "Transcribing", diarizing: "Identifying speakers", done: "Done",
        };
        const list = el("file-stage-list");
        clearChildren(list);
        const cur = stages.indexOf(msg.stage);
        for (let i = 0; i < stages.length; i++) {
            const div = document.createElement("div");
            div.className = "stage" + (i < cur ? " done" : (i === cur ? " active" : ""));
            div.textContent = labels[stages[i]];
            list.appendChild(div);
        }
        el("file-progress-fill").style.width = (msg.percent || 0) + "%";
        el("file-progress-message").textContent = msg.message || "";
    }

    function bindCopyAndExport() {
        el("file-copy-all").addEventListener("click", async () => {
            if (!currentPayload) return;
            const text = currentPayload.segments.map(s => s.text).join(" ");
            await navigator.clipboard.writeText(text);
        });
        el("export-save").addEventListener("click", async () => {
            if (!currentJob) return;
            const fmt = el("export-format").value;
            const dest = pickSaveDest(fmt);
            if (!dest) return;
            const r = await fetch("/api/file-job/" + currentJob.job_id + "/export", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ format: fmt, dest_path: dest }),
            });
            if (!r.ok) window.alert("Export failed");
        });
        el("file-cancel-btn").addEventListener("click", () => {
            if (!currentJob || !ws || ws.readyState !== WebSocket.OPEN) return;
            ws.send(JSON.stringify({ action: "cancel_file_job", job_id: currentJob.job_id }));
        });
    }

    function pickSaveDest(fmt) {
        // Phase 1: derive dest path from source path automatically (sibling file).
        if (!currentPayload?.audio_path) return null;
        const stem = currentPayload.audio_path.replace(/\.[^.]+$/, "");
        return stem + "." + fmt;
    }

    function init() {
        ws = window.__appWebSocket || null;
        loadDefaults();
        bindEmptyState();
        bindSidebar();
        bindAudioSync();
        bindCopyAndExport();
        setState("empty");
    }

    document.addEventListener("DOMContentLoaded", init);

    window.__fileMode = {
        startJobFromPath, setState, setWs,
        setJob: (j) => { currentJob = j; },
        showResult, showWorking, updateProgress,
    };
})();
