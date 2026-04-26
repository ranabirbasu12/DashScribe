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

    function init() {
        ws = window.__appWebSocket || null;
        loadDefaults();
        bindEmptyState();
        bindSidebar();
        setState("empty");
    }

    document.addEventListener("DOMContentLoaded", init);

    // Exported here so app.js can route messages and so Task 10 can extend us.
    window.__fileMode = {
        startJobFromPath, setState, setWs,
        setJob: (j) => { currentJob = j; },
        clearChildren,
        // Result-rendering hooks added in Task 10:
        showResult: () => {},
        showWorking: () => {},
        updateProgress: () => {},
    };
})();
