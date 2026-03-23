(function () {
    // --- Onboarding ---
    const onboardingOverlay = document.getElementById('onboarding-overlay');
    const onboardingPermissions = document.getElementById('onboarding-permissions');
    const onboardingModels = document.getElementById('onboarding-models');
    const onboardingContinueBtn = document.getElementById('onboarding-continue-btn');
    const onboardingSkipBtn = document.getElementById('onboarding-skip-btn');
    const onboardingModelsLabel = document.getElementById('onboarding-models-label');
    const onboardingTitle = document.querySelector('.onboarding-title');
    const onboardingSubtitle = document.querySelector('.onboarding-subtitle');
    let onboardingPollTimer = null;
    let onboardingVisible = false;
    let regrantMode = false;

    const CHECK_SVG = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>';
    const WARNING_SVG = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/></svg>';
    const SPINNER_SVG = '<svg width="12" height="12" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="3" stroke-dasharray="31.4 31.4" stroke-linecap="round"><animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="1s" repeatCount="indefinite"/></circle></svg>';

    // NOTE: innerHTML usage below is safe — all dynamic user text is escaped via
    // escapeHtml() or textContent. SVG icons and structural markup are static templates.

    function renderPermissionItem(key, perm) {
        var item = document.createElement('div');
        item.className = 'onboarding-item' + (perm.granted ? ' granted' : '');

        var iconClass = perm.granted ? 'granted' : (perm.required ? 'pending' : 'optional');
        var icon = perm.granted ? CHECK_SVG : WARNING_SVG;
        var statusText = perm.granted ? 'Granted' : (perm.required ? 'Required' : 'Optional');
        var statusClass = perm.granted ? '' : (perm.required ? 'pending' : '');

        var actionHtml = '';
        if (!perm.granted) {
            if (key === 'microphone' && perm.not_determined) {
                actionHtml = '<button class="onboarding-grant-btn" data-action="request-mic">Allow</button>';
            } else {
                actionHtml = '<button class="onboarding-grant-btn" data-url="' + perm.settings_url + '">Open Settings</button>';
            }
        }

        item.innerHTML =
            '<div class="onboarding-status-icon ' + iconClass + '">' + icon + '</div>' +
            '<div class="onboarding-item-info">' +
                '<div class="onboarding-item-name">' + escapeHtml(perm.name) + '</div>' +
                '<div class="onboarding-item-desc">' + escapeHtml(perm.description) + '</div>' +
                '<div class="onboarding-item-status ' + statusClass + '">' + escapeHtml(statusText) + '</div>' +
            '</div>' +
            actionHtml;

        return item;
    }

    function renderModelItem(key, model) {
        var item = document.createElement('div');
        item.className = 'onboarding-item' + (model.ready ? ' granted' : '');

        var iconClass, icon, statusText, statusClass;
        if (model.ready) {
            iconClass = 'granted';
            icon = CHECK_SVG;
            statusText = 'Ready';
            statusClass = '';
        } else {
            var st = model.status || 'loading';
            if (st === 'downloading') {
                iconClass = 'loading';
                icon = SPINNER_SVG;
                statusText = model.message || 'Downloading...';
                statusClass = 'loading';
            } else if (st === 'error') {
                iconClass = 'pending';
                icon = WARNING_SVG;
                statusText = model.message || 'Error';
                statusClass = 'pending';
            } else {
                iconClass = 'loading';
                icon = SPINNER_SVG;
                statusText = model.message || 'Loading...';
                statusClass = 'loading';
            }
        }

        item.innerHTML =
            '<div class="onboarding-status-icon ' + iconClass + '">' + icon + '</div>' +
            '<div class="onboarding-item-info">' +
                '<div class="onboarding-item-name">' + escapeHtml(model.name) + '</div>' +
                '<div class="onboarding-item-desc">' + escapeHtml(model.description) + '</div>' +
                '<div class="onboarding-item-status ' + statusClass + '">' + escapeHtml(statusText) + '</div>' +
            '</div>';

        return item;
    }

    function updateOnboarding(data) {
        var permOrder = ['microphone', 'accessibility', 'screen_recording'];

        if (regrantMode) {
            // Re-grant mode: only show missing required permissions
            onboardingTitle.textContent = 'Permissions Required';
            onboardingSubtitle.textContent = 'Some permissions were reset after updating. Please re-grant them below.';
            onboardingSkipBtn.style.display = 'none';
            onboardingModelsLabel.style.display = 'none';
            onboardingModels.style.display = 'none';

            onboardingPermissions.innerHTML = '';
            for (var i = 0; i < permOrder.length; i++) {
                var key = permOrder[i];
                var perm = data.permissions[key];
                if (perm && !perm.granted && perm.required) {
                    onboardingPermissions.appendChild(renderPermissionItem(key, perm));
                }
            }

            // In re-grant mode, Continue only needs required permissions (not model)
            var allRequiredGranted = permOrder.every(function (k) {
                var p = data.permissions[k];
                return !p || !p.required || p.granted;
            });
            onboardingContinueBtn.disabled = !allRequiredGranted;
        } else {
            // First-launch onboarding: show everything
            onboardingTitle.textContent = 'Welcome to DashScribe';
            onboardingSubtitle.textContent = 'A few things to set up before you start dictating.';
            onboardingSkipBtn.style.display = '';
            onboardingModelsLabel.style.display = '';
            onboardingModels.style.display = '';

            onboardingPermissions.innerHTML = '';
            for (var i = 0; i < permOrder.length; i++) {
                var key = permOrder[i];
                var perm = data.permissions[key];
                if (perm) {
                    onboardingPermissions.appendChild(renderPermissionItem(key, perm));
                }
            }

            onboardingModels.innerHTML = '';
            var modelKeys = Object.keys(data.models);
            for (var j = 0; j < modelKeys.length; j++) {
                onboardingModels.appendChild(renderModelItem(modelKeys[j], data.models[modelKeys[j]]));
            }

            var allRequiredGranted = permOrder.every(function (k) {
                var p = data.permissions[k];
                return !p || !p.required || p.granted;
            });
            var whisperReady = data.models.whisper && data.models.whisper.ready;
            onboardingContinueBtn.disabled = !(allRequiredGranted && whisperReady);
        }

        // Wire action buttons
        onboardingPermissions.querySelectorAll('.onboarding-grant-btn').forEach(function (btn) {
            btn.onclick = async function (e) {
                e.stopPropagation();
                var url = btn.dataset.url;
                var action = btn.dataset.action;
                if (action === 'request-mic') {
                    await fetch('/api/permissions/request-microphone', { method: 'POST' });
                } else if (url) {
                    await fetch('/api/permissions/open-settings', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ url: url }),
                    });
                }
            };
        });
    }

    async function pollOnboarding() {
        try {
            var resp = await fetch('/api/permissions');
            var data = await resp.json();

            var micOk = data.permissions.microphone && data.permissions.microphone.granted;
            var accOk = data.permissions.accessibility && data.permissions.accessibility.granted;

            if (data.onboarding_complete) {
                if (micOk && accOk) {
                    regrantMode = false;
                    hideOnboarding();
                    return;
                }
                // Onboarding was completed before, but required permissions are now missing
                regrantMode = true;
            } else {
                regrantMode = false;
            }

            if (!onboardingVisible) {
                showOnboarding();
            }
            updateOnboarding(data);
        } catch (e) {
            // Server not ready yet
        }
    }

    function showOnboarding() {
        onboardingVisible = true;
        onboardingOverlay.classList.remove('hidden');
        if (!onboardingPollTimer) {
            onboardingPollTimer = setInterval(pollOnboarding, 2000);
        }
    }

    function hideOnboarding() {
        onboardingVisible = false;
        onboardingOverlay.classList.add('hidden');
        if (onboardingPollTimer) {
            clearInterval(onboardingPollTimer);
            onboardingPollTimer = null;
        }
    }

    async function dismissOnboarding() {
        await fetch('/api/permissions/dismiss-onboarding', { method: 'POST' });
        hideOnboarding();
    }

    onboardingContinueBtn.addEventListener('click', dismissOnboarding);
    onboardingSkipBtn.addEventListener('click', dismissOnboarding);

    // Kick off onboarding check
    pollOnboarding();

    // --- Main App ---
    const micBtn = document.getElementById('mic-btn');
    const micLabel = document.getElementById('mic-label');
    const toast = document.getElementById('toast');
    const modelStatus = document.getElementById('model-status');
    const latencyEl = document.getElementById('latency');
    const progressContainer = document.getElementById('progress-container');
    const progressFill = document.getElementById('progress-fill');
    const progressMessage = document.getElementById('progress-message');

    let ws = null;
    let isRecording = false;
    let modelReady = false;
    let statusPollTimer = null;
    let reconnectTimer = null;
    let historyRefreshTimer = null;
    let toastHideTimer = null;
    let recordStartTime = 0;
    const MIN_RECORD_MS = 300;

    function setMicDisabled(disabled) {
        if (disabled) {
            micBtn.classList.add('disabled');
            micLabel.textContent = 'Model loading...';
        } else {
            micBtn.classList.remove('disabled');
            micLabel.textContent = 'Hold to Record';
        }
    }

    function updateModelState(msg) {
        const status = msg.status || (msg.ready ? 'ready' : 'loading');
        const message = msg.message || '';

        modelReady = msg.ready;
        setMicDisabled(!modelReady);

        const dot = document.createElement('span');
        const label = document.createTextNode(' ');

        modelStatus.classList.remove('status-ready', 'status-loading', 'status-error');

        if (status === 'ready') {
            dot.className = 'dot ready';
            modelStatus.textContent = '';
            modelStatus.classList.add('status-ready');
            modelStatus.appendChild(dot);
            modelStatus.appendChild(document.createTextNode(' Transcriber Ready'));
            progressContainer.classList.add('hidden');
            stopStatusPolling();
            updateLlmStatusCapsule(); // Check LLM status once transcriber is ready
        } else if (status === 'downloading') {
            dot.className = 'dot downloading';
            modelStatus.textContent = '';
            modelStatus.classList.add('status-loading');
            modelStatus.appendChild(dot);
            modelStatus.appendChild(document.createTextNode(' Downloading...'));
            progressFill.className = 'progress-fill downloading';
            progressMessage.className = 'progress-msg downloading';
            progressMessage.textContent = message || 'Downloading model...';
            progressContainer.classList.remove('hidden');
        } else if (status === 'loading') {
            dot.className = 'dot loading';
            modelStatus.textContent = '';
            modelStatus.classList.add('status-loading');
            modelStatus.appendChild(dot);
            modelStatus.appendChild(document.createTextNode(' Loading...'));
            progressFill.className = 'progress-fill loading';
            progressMessage.className = 'progress-msg loading';
            progressMessage.textContent = message || 'Loading model into memory...';
            progressContainer.classList.remove('hidden');
        } else if (status === 'error') {
            dot.className = 'dot error';
            modelStatus.textContent = '';
            modelStatus.classList.add('status-error');
            modelStatus.appendChild(dot);
            modelStatus.appendChild(document.createTextNode(' Error'));
            progressFill.className = 'progress-fill error';
            progressMessage.className = 'progress-msg error';
            progressMessage.textContent = message || 'Failed to load model';
            progressContainer.classList.remove('hidden');
            stopStatusPolling();
        } else {
            dot.className = 'dot loading';
            modelStatus.textContent = '';
            modelStatus.appendChild(dot);
            modelStatus.appendChild(document.createTextNode(' Initializing...'));
            progressFill.className = 'progress-fill loading';
            progressMessage.className = 'progress-msg';
            progressMessage.textContent = message || 'Initializing...';
            progressContainer.classList.remove('hidden');
        }
    }

    var llmStatusEl = document.getElementById('llm-status');

    var llmCapsulePollTimer = null;

    function updateLlmStatusCapsule(statusOverride) {
        if (!llmStatusEl) return;
        fetch('/api/llm/status').then(function (r) { return r.json(); }).then(function (data) {
            var s = statusOverride || data.status;
            var dot = document.createElement('span');
            llmStatusEl.textContent = '';
            llmStatusEl.className = 'model-status';
            var needsPoll = false;

            if (!data.available) {
                dot.className = 'dot';
                llmStatusEl.classList.add('llm-off');
                llmStatusEl.appendChild(dot);
                llmStatusEl.appendChild(document.createTextNode(' LLM Off'));
            } else if (data.loaded || s === 'ready') {
                dot.className = 'dot ready';
                llmStatusEl.classList.add('llm-ready');
                llmStatusEl.appendChild(dot);
                llmStatusEl.appendChild(document.createTextNode(' LLM Ready'));
            } else if (s === 'downloading') {
                dot.className = 'dot downloading';
                llmStatusEl.classList.add('llm-downloading');
                llmStatusEl.appendChild(dot);
                llmStatusEl.appendChild(document.createTextNode(' LLM Downloading...'));
                needsPoll = true;
            } else if (s === 'loading') {
                dot.className = 'dot loading';
                llmStatusEl.classList.add('llm-loading');
                llmStatusEl.appendChild(dot);
                llmStatusEl.appendChild(document.createTextNode(' LLM Loading...'));
                needsPoll = true;
            } else if (s === 'error') {
                dot.className = 'dot error';
                llmStatusEl.classList.add('llm-error');
                llmStatusEl.appendChild(dot);
                llmStatusEl.appendChild(document.createTextNode(' LLM Error'));
            } else {
                // idle — check if AI features are even enabled
                var aiToggle = document.getElementById('ai-features-toggle');
                if (aiToggle && aiToggle.checked && data.cached) {
                    dot.className = 'dot loading';
                    llmStatusEl.classList.add('llm-loading');
                    llmStatusEl.appendChild(dot);
                    llmStatusEl.appendChild(document.createTextNode(' LLM Standby'));
                    needsPoll = true;
                } else {
                    dot.className = 'dot';
                    llmStatusEl.classList.add('llm-off');
                    llmStatusEl.appendChild(dot);
                    llmStatusEl.appendChild(document.createTextNode(' LLM Off'));
                }
            }

            // Keep polling until LLM reaches a terminal state (ready, error, or off)
            if (needsPoll && !llmCapsulePollTimer) {
                llmCapsulePollTimer = setInterval(function () {
                    updateLlmStatusCapsule();
                }, 2000);
            } else if (!needsPoll && llmCapsulePollTimer) {
                clearInterval(llmCapsulePollTimer);
                llmCapsulePollTimer = null;
            }
        }).catch(function () {});
    }

    function pollStatus() {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action: 'status' }));
        }
    }

    function startStatusPolling() {
        if (statusPollTimer) return;
        statusPollTimer = setInterval(pollStatus, 1000);
    }

    function stopStatusPolling() {
        if (statusPollTimer) {
            clearInterval(statusPollTimer);
            statusPollTimer = null;
        }
    }

    function scheduleReconnect() {
        if (reconnectTimer) return;
        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            connect();
        }, 1000);
    }

    function connect() {
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            return;
        }
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(protocol + '//' + location.host + '/ws');

        ws.onopen = () => {
            if (reconnectTimer) {
                clearTimeout(reconnectTimer);
                reconnectTimer = null;
            }
            pollStatus();
            startStatusPolling();
        };

        ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);

            if (msg.type === 'status') {
                if (msg.status === 'recording') {
                    micBtn.classList.add('recording');
                    micBtn.classList.remove('transcribing');
                    if (sessionArea) sessionArea.classList.add('recording');
                    micLabel.textContent = 'Recording...';
                } else if (msg.status === 'transcribing') {
                    micBtn.classList.remove('recording');
                    micBtn.classList.add('transcribing');
                    if (sessionArea) sessionArea.classList.remove('recording');
                    micLabel.textContent = 'Transcribing...';
                }
            } else if (msg.type === 'result') {
                micBtn.classList.remove('recording', 'transcribing');
                if (sessionArea) sessionArea.classList.remove('recording');
                micLabel.textContent = 'Hold to Record';
                latencyEl.textContent = msg.latency + 's';
                if (msg.inserted) {
                    showToast('Inserted text (' + currentRepasteDisplay + ' to re-paste)');
                } else {
                    showToast('Saved for re-paste: ' + currentRepasteDisplay);
                }
                loadHistory(false);
                isRecording = false;
            } else if (msg.type === 'error') {
                micBtn.classList.remove('recording', 'transcribing');
                if (sessionArea) sessionArea.classList.remove('recording');
                micLabel.textContent = 'Hold to Record';
                isRecording = false;
                if (fileTranscribing) {
                    fileTranscribing = false;
                    transcribeBtn.disabled = !filePathInput.value.trim() || !modelReady;
                    fileProgress.classList.add('hidden');
                } else {
                    showToast(msg.message || 'Error');
                }
            } else if (msg.type === 'model_status') {
                updateModelState(msg);
            } else if (msg.type === 'file_status') {
                fileTranscribing = true;
                transcribeBtn.disabled = true;
                fileResult.classList.add('hidden');
                fileProgress.classList.remove('hidden');
                fileProgressMsg.textContent = msg.message || 'Transcribing file...';
            } else if (msg.type === 'file_result') {
                fileTranscribing = false;
                transcribeBtn.disabled = !filePathInput.value.trim() || !modelReady;
                fileProgress.classList.add('hidden');
                fileResult.classList.remove('hidden');
                fileResultText.textContent = 'Transcription saved (' + msg.latency + 's)';
                fileOutputPath.textContent = msg.output_path;
                loadHistory(false);
            }
        };

        ws.onclose = () => {
            stopStatusPolling();
            ws = null;
            scheduleReconnect();
        };
    }

    function showToast(message) {
        toast.textContent = message || 'Copied to clipboard';
        toast.classList.remove('hidden');
        if (toastHideTimer) {
            clearTimeout(toastHideTimer);
        }
        toastHideTimer = setTimeout(() => {
            toast.classList.add('hidden');
            toastHideTimer = null;
        }, 2000);
    }

    function stopRecording() {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        if (!isRecording) return;
        isRecording = false;
        const elapsed = Date.now() - recordStartTime;
        if (elapsed < MIN_RECORD_MS) {
            setTimeout(() => {
                ws.send(JSON.stringify({ action: 'stop' }));
            }, MIN_RECORD_MS - elapsed);
        } else {
            ws.send(JSON.stringify({ action: 'stop' }));
        }
    }

    // Push-to-talk: mousedown = start, mouseup = stop
    micBtn.addEventListener('mousedown', (e) => {
        e.preventDefault();
        if (!modelReady) return;
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        if (isRecording) return;
        isRecording = true;
        recordStartTime = Date.now();
        ws.send(JSON.stringify({ action: 'start' }));
    });

    micBtn.addEventListener('mouseup', (e) => {
        e.preventDefault();
        stopRecording();
    });

    micBtn.addEventListener('mouseleave', (e) => {
        stopRecording();
    });

    // --- File Transcription ---
    const filePathInput = document.getElementById('file-path');
    const browseBtn = document.getElementById('browse-btn');
    const transcribeBtn = document.getElementById('transcribe-btn');
    const fileProgress = document.getElementById('file-progress');
    const fileProgressMsg = document.getElementById('file-progress-msg');
    const fileResult = document.getElementById('file-result');
    const fileResultText = document.getElementById('file-result-text');
    const fileOutputPath = document.getElementById('file-output-path');
    let fileTranscribing = false;

    filePathInput.addEventListener('input', () => {
        transcribeBtn.disabled = !filePathInput.value.trim() || !modelReady || fileTranscribing;
    });

    browseBtn.addEventListener('click', async () => {
        const resp = await fetch('/api/browse-file');
        const data = await resp.json();
        if (data.path) {
            filePathInput.value = data.path;
            transcribeBtn.disabled = !modelReady || fileTranscribing;
        }
    });

    transcribeBtn.addEventListener('click', () => {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        if (!filePathInput.value.trim() || fileTranscribing) return;
        ws.send(JSON.stringify({ action: 'transcribe_file', path: filePathInput.value.trim() }));
    });

    // --- History ---
    const statStreak = document.getElementById('stat-streak');
    const statWords = document.getElementById('stat-words');
    const statWpm = document.getElementById('stat-wpm');
    const sessionArea = document.querySelector('.session-area');
    const historyList = document.getElementById('history-list');
    const historySearch = document.getElementById('history-search');
    const loadMoreBtn = document.getElementById('load-more-btn');
    let activeMode = 'dictate';
    let historyOffset = 0;
    const HISTORY_PAGE = 50;
    let totalHistory = 0;
    let historyLoading = false;
    let lastRenderedDayKey = null;

    function formatCompactWords(value) {
        const n = Number(value) || 0;
        if (n >= 1000) {
            const compact = Math.round((n / 1000) * 10) / 10;
            return compact.toString().replace(/\.0$/, '') + 'K';
        }
        return String(n);
    }

    function formatStreak(value) {
        const days = Math.max(0, Number(value) || 0);
        const months = Math.floor(days / 30);
        const remDays = days % 30;

        if (months === 0) {
            return String(remDays) + 'D';
        }
        if (remDays === 0) {
            return String(months) + 'M';
        }
        return String(months) + 'M ' + String(remDays) + 'D';
    }

    function formatWpm(value) {
        const n = Number(value) || 0;
        return n.toFixed(1);
    }

    async function loadUsageStats() {
        try {
            const resp = await fetch('/api/history/stats');
            const data = await resp.json();
            statStreak.textContent = formatStreak(data.streak_days || 0);
            statWords.textContent = formatCompactWords(data.total_words || 0);
            statWpm.textContent = formatWpm(data.words_per_minute || 0);
        } catch (e) { /* ignore */ }
    }

    function formatTime(isoString) {
        const d = new Date(isoString);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    function dayKeyFromDate(date) {
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        return year + '-' + month + '-' + day;
    }

    function dayKeyFromIso(isoString) {
        return dayKeyFromDate(new Date(isoString));
    }

    function parseDayKey(dayKey) {
        const [year, month, day] = dayKey.split('-').map(Number);
        return new Date(year, month - 1, day);
    }

    function formatHistoryDay(dayKey) {
        const now = new Date();
        const todayKey = dayKeyFromDate(now);
        const yesterday = new Date(now);
        yesterday.setDate(yesterday.getDate() - 1);
        const yesterdayKey = dayKeyFromDate(yesterday);
        if (dayKey === todayKey) return 'Today';
        if (dayKey === yesterdayKey) return 'Yesterday';
        return parseDayKey(dayKey).toLocaleDateString([], {
            month: 'long',
            day: 'numeric',
            year: 'numeric',
        });
    }

    function createHistoryDayHeader(dayLabel) {
        const div = document.createElement('div');
        div.className = 'history-day-header';
        div.textContent = dayLabel;
        return div;
    }

    // SVG icons for the raw/formatted toggle
    var SVG_UNDO = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M12.5 8c-2.65 0-5.05.99-6.9 2.6L2 7v9h9l-3.62-3.62c1.39-1.16 3.16-1.88 5.12-1.88 3.54 0 6.55 2.31 7.6 5.5l2.37-.78C21.08 11.03 17.15 8 12.5 8z"/></svg>';
    var SVG_REDO = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M18.4 10.6C16.55 8.99 14.15 8 11.5 8c-4.65 0-8.58 3.03-9.96 7.22L3.9 16c1.05-3.19 4.05-5.5 7.6-5.5 1.95 0 3.73.72 5.12 1.88L13 16h9V7l-3.6 3.6z"/></svg>';

    function createHistoryEntry(entry) {
        var source = (entry.source || 'dictation').toLowerCase() === 'file' ? 'file' : 'dictation';
        var sourceLabel = source === 'file' ? 'File transcription' : 'Dictation';
        var hasRaw = entry.raw_text && entry.raw_text !== entry.text;
        var showingFormatted = true;

        var div = document.createElement('div');
        div.className = 'history-entry';

        // Source badge
        var badge = document.createElement('span');
        badge.className = 'history-source-badge ' + source;
        badge.title = sourceLabel;
        if (source === 'file') {
            badge.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"></path><path d="M14 3v5h5"></path></svg>';
        } else {
            badge.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3z"></path><path d="M17 11a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2z"></path></svg>';
        }

        // Time
        var time = document.createElement('span');
        time.className = 'history-time';
        time.textContent = formatTime(entry.timestamp);

        // Text (safe — uses textContent)
        var text = document.createElement('span');
        text.className = 'history-text';
        text.textContent = entry.text;

        // Actions container
        var actions = document.createElement('span');
        actions.className = 'history-actions';

        // Toggle raw/formatted button (only if AI formatting was applied)
        if (hasRaw) {
            var toggleBtn = document.createElement('button');
            toggleBtn.className = 'history-toggle-btn';
            toggleBtn.setAttribute('aria-label', 'Show original');
            toggleBtn.title = 'Show original (undo AI formatting)';
            toggleBtn.innerHTML = SVG_UNDO;
            toggleBtn.addEventListener('click', function() {
                if (showingFormatted) {
                    text.textContent = entry.raw_text;
                    toggleBtn.innerHTML = SVG_REDO;
                    toggleBtn.title = 'Show formatted (redo AI formatting)';
                    toggleBtn.setAttribute('aria-label', 'Show formatted');
                    showingFormatted = false;
                } else {
                    text.textContent = entry.text;
                    toggleBtn.innerHTML = SVG_UNDO;
                    toggleBtn.title = 'Show original (undo AI formatting)';
                    toggleBtn.setAttribute('aria-label', 'Show original');
                    showingFormatted = true;
                }
            });
            actions.appendChild(toggleBtn);
        }

        // Copy button (static SVG icon — safe)
        var copyBtn = document.createElement('button');
        copyBtn.className = 'history-copy-btn';
        copyBtn.setAttribute('aria-label', 'Copy');
        copyBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>';
        copyBtn.addEventListener('click', function() {
            navigator.clipboard.writeText(text.textContent);
            showToast('Copied to clipboard');
        });
        actions.appendChild(copyBtn);

        div.appendChild(badge);
        div.appendChild(time);
        div.appendChild(text);
        div.appendChild(actions);
        return div;
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function appendHistoryEntries(entries) {
        entries.forEach(function(entry, i) {
            var dayKey = dayKeyFromIso(entry.timestamp);
            if (dayKey !== lastRenderedDayKey) {
                historyList.appendChild(createHistoryDayHeader(formatHistoryDay(dayKey)));
                lastRenderedDayKey = dayKey;
            }
            var el = createHistoryEntry(entry);
            el.style.animationDelay = (i * 0.03) + 's';
            historyList.appendChild(el);
        });
    }

    async function loadHistory(append) {
        if (historyLoading) return;
        historyLoading = true;
        if (!append) {
            historyOffset = 0;
            historyList.textContent = '';
            lastRenderedDayKey = null;
        }
        try {
            const resp = await fetch('/api/history?limit=' + HISTORY_PAGE + '&offset=' + historyOffset);
            const data = await resp.json();
            totalHistory = data.total;
            appendHistoryEntries(data.entries);
            historyOffset += data.entries.length;
            loadMoreBtn.classList.toggle('hidden', historyOffset >= totalHistory);
            if (!append) {
                loadUsageStats();
            }
        } finally {
            historyLoading = false;
        }
    }

    async function searchHistory(query) {
        if (!query) {
            loadHistory(false);
            return;
        }
        historyList.textContent = '';
        lastRenderedDayKey = null;
        const resp = await fetch('/api/history/search?q=' + encodeURIComponent(query));
        const data = await resp.json();
        appendHistoryEntries(data.entries);
        loadMoreBtn.classList.add('hidden');
    }

    let searchTimeout = null;
    historySearch.addEventListener('input', () => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => searchHistory(historySearch.value.trim()), 300);
    });

    loadMoreBtn.addEventListener('click', () => loadHistory(true));

    function startHistoryRefresh() {
        if (historyRefreshTimer) return;
        historyRefreshTimer = setInterval(() => {
            if (!isRecording && activeMode !== 'settings') {
                loadHistory(false);
                loadUsageStats();
            }
        }, 10000);
    }

    function stopHistoryRefresh() {
        if (historyRefreshTimer) {
            clearInterval(historyRefreshTimer);
            historyRefreshTimer = null;
        }
    }

    // --- Sidebar Navigation ---
    var settingsOverlay = document.getElementById('settings-overlay');
    var settingsCloseBtn = document.getElementById('settings-close-btn');

    function setMode(mode) {
        if (mode === 'settings') {
            settingsOverlay.classList.remove('hidden');
            return;
        }
        activeMode = mode;
        document.querySelectorAll('.sidebar-item').forEach(function(btn) {
            btn.classList.toggle('active', btn.dataset.mode === mode);
        });
        document.querySelectorAll('.page').forEach(function(p) {
            p.classList.remove('active');
        });
        var target = document.getElementById(mode + '-mode');
        if (target) target.classList.add('active');
    }

    function closeSettings() {
        settingsOverlay.classList.add('hidden');
    }

    // Settings sidebar tab switching
    function switchSettingsTab(tabName) {
        document.querySelectorAll('.settings-tab-panel').forEach(function(panel) {
            panel.classList.add('hidden');
        });
        document.querySelectorAll('.settings-sidebar-item').forEach(function(btn) {
            btn.classList.remove('active');
        });
        var panel = document.getElementById('settings-tab-' + tabName);
        if (panel) panel.classList.remove('hidden');
        var btn = document.querySelector('[data-settings-tab="' + tabName + '"]');
        if (btn) btn.classList.add('active');
    }

    document.querySelectorAll('.settings-sidebar-item').forEach(function(btn) {
        btn.addEventListener('click', function() {
            switchSettingsTab(btn.dataset.settingsTab);
        });
    });

    settingsCloseBtn.addEventListener('click', closeSettings);
    settingsOverlay.addEventListener('click', function(e) {
        if (e.target === settingsOverlay) closeSettings();
    });

    document.querySelectorAll('.sidebar-item').forEach(function(btn) {
        btn.addEventListener('click', function() {
            setMode(btn.dataset.mode);
        });
    });

    // --- Settings: Theme + Hotkey + Insertion ---
    const themeButtons = Array.from(document.querySelectorAll('.theme-option'));
    const hotkeyBtn = document.getElementById('hotkey-capture-btn');
    const hotkeyDisplay = document.getElementById('hotkey-display');
    const hotkeyResetBtn = document.getElementById('hotkey-reset-btn');
    const autoInsertToggle = document.getElementById('auto-insert-toggle');
    const repasteBtn = document.getElementById('repaste-capture-btn');
    const repasteDisplay = document.getElementById('repaste-display');
    const repasteResetBtn = document.getElementById('repaste-reset-btn');
    const systemThemeQuery = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

    let captureTarget = null; // 'hotkey' | 'repaste' | null
    let capturePollId = null;
    let captureTimeoutId = null;
    let currentRepasteDisplay = 'Cmd+Option+V';
    let pendingCaptureSave = null;
    let currentThemeMode = 'auto';
    let systemThemeListener = null;

    function resolveThemeMode(mode) {
        if (mode === 'light' || mode === 'dark') return mode;
        if (!systemThemeQuery) return 'dark';
        return systemThemeQuery.matches ? 'dark' : 'light';
    }

    function applyThemeMode(mode) {
        const normalized = (mode === 'light' || mode === 'dark' || mode === 'auto') ? mode : 'auto';
        currentThemeMode = normalized;
        const effective = resolveThemeMode(normalized);
        document.body.classList.remove('theme-light', 'theme-dark');
        document.body.classList.add('theme-' + effective);
        themeButtons.forEach(btn => {
            const isActive = btn.dataset.themeMode === normalized;
            btn.classList.toggle('active', isActive);
            btn.setAttribute('aria-checked', isActive ? 'true' : 'false');
        });
    }

    async function loadThemeSettings() {
        try {
            const resp = await fetch('/api/settings/theme');
            const data = await resp.json();
            applyThemeMode(data.theme || 'auto');
        } catch (e) {
            applyThemeMode('auto');
        }
    }

    async function saveThemeMode(mode) {
        applyThemeMode(mode);
        try {
            const resp = await fetch('/api/settings/theme', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ theme: mode }),
            });
            const data = await resp.json();
            if (data.ok) {
                applyThemeMode(data.theme || mode);
            } else {
                await loadThemeSettings();
            }
        } catch (e) {
            await loadThemeSettings();
        }
    }

    async function loadHotkey() {
        try {
            const resp = await fetch('/api/settings/hotkey');
            const data = await resp.json();
            hotkeyDisplay.textContent = data.display || 'Right Option';
        } catch (e) { /* ignore */ }
    }

    async function saveHotkey(serialized) {
        try {
            const resp = await fetch('/api/settings/hotkey', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key: serialized }),
            });
            const data = await resp.json();
            if (data.ok) {
                hotkeyDisplay.textContent = data.display;
            } else {
                hotkeyDisplay.textContent = 'Invalid key';
                setTimeout(loadHotkey, 1500);
            }
        } catch (e) {
            hotkeyDisplay.textContent = 'Error';
            setTimeout(loadHotkey, 1500);
        }
    }

    async function loadInsertionSettings() {
        try {
            const resp = await fetch('/api/settings/insertion');
            const data = await resp.json();
            autoInsertToggle.checked = !!data.auto_insert;
            currentRepasteDisplay = data.repaste_display || 'Cmd+Option+V';
            repasteDisplay.textContent = currentRepasteDisplay;
        } catch (e) { /* ignore */ }
    }

    async function saveAutoInsert(enabled) {
        try {
            const resp = await fetch('/api/settings/insertion/auto-insert', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: enabled }),
            });
            const data = await resp.json();
            if (!data.ok) {
                await loadInsertionSettings();
            }
        } catch (e) {
            await loadInsertionSettings();
        }
    }

    async function saveRepasteKey(serialized) {
        try {
            const resp = await fetch('/api/settings/insertion/repaste-key', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key: serialized }),
            });
            const data = await resp.json();
            if (data.ok) {
                currentRepasteDisplay = data.repaste_display;
                repasteDisplay.textContent = data.repaste_display;
            } else {
                repasteDisplay.textContent = 'Invalid key';
                setTimeout(loadInsertionSettings, 1200);
            }
        } catch (e) {
            repasteDisplay.textContent = 'Error';
            setTimeout(loadInsertionSettings, 1200);
        }
    }

    async function startCapture(target) {
        if (captureTarget) return;
        if (pendingCaptureSave) {
            clearTimeout(pendingCaptureSave);
            pendingCaptureSave = null;
        }
        if (captureTimeoutId) {
            clearTimeout(captureTimeoutId);
            captureTimeoutId = null;
        }
        captureTarget = target;

        if (target === 'hotkey') {
            hotkeyBtn.classList.add('capturing');
            hotkeyDisplay.textContent = 'Press shortcut...';
        } else {
            repasteBtn.classList.add('capturing');
            repasteDisplay.textContent = 'Press shortcut...';
        }

        // Tell backend to start global key capture
        await fetch('/api/settings/hotkey/capture', { method: 'POST' });

        // Poll for captured key
        capturePollId = setInterval(async () => {
            try {
                const resp = await fetch('/api/settings/hotkey/capture');
                const data = await resp.json();
                if (data.captured) {
                    clearInterval(capturePollId);
                    capturePollId = null;
                    if (captureTimeoutId) {
                        clearTimeout(captureTimeoutId);
                        captureTimeoutId = null;
                    }
                    const targetNow = captureTarget;
                    captureTarget = null;
                    hotkeyBtn.classList.remove('capturing');
                    repasteBtn.classList.remove('capturing');
                    const keyDisplay = data.display || data.key || '';
                    if (targetNow === 'hotkey') {
                        hotkeyDisplay.textContent = 'Will be: ' + keyDisplay;
                    } else if (targetNow === 'repaste') {
                        repasteDisplay.textContent = 'Will be: ' + keyDisplay;
                    }
                    pendingCaptureSave = setTimeout(() => {
                        pendingCaptureSave = null;
                        if (targetNow === 'hotkey') {
                            saveHotkey(data.key);
                        } else if (targetNow === 'repaste') {
                            saveRepasteKey(data.key);
                        }
                    }, 450);
                }
            } catch (e) { /* ignore */ }
        }, 100);

        // Timeout after 10 seconds
        captureTimeoutId = setTimeout(() => {
            if (captureTarget) {
                clearInterval(capturePollId);
                capturePollId = null;
                const targetNow = captureTarget;
                captureTarget = null;
                hotkeyBtn.classList.remove('capturing');
                repasteBtn.classList.remove('capturing');
                if (pendingCaptureSave) {
                    clearTimeout(pendingCaptureSave);
                    pendingCaptureSave = null;
                }
                fetch('/api/settings/hotkey/capture', { method: 'DELETE' });
                if (targetNow === 'hotkey') {
                    loadHotkey();
                } else {
                    loadInsertionSettings();
                }
            }
            captureTimeoutId = null;
        }, 10000);
    }

    hotkeyBtn.addEventListener('click', () => {
        startCapture('hotkey');
    });

    hotkeyResetBtn.addEventListener('click', () => {
        saveHotkey('alt_r');
    });

    autoInsertToggle.addEventListener('change', () => {
        saveAutoInsert(autoInsertToggle.checked);
    });

    repasteBtn.addEventListener('click', () => {
        startCapture('repaste');
    });

    repasteResetBtn.addEventListener('click', () => {
        saveRepasteKey('char:v');
    });

    themeButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            saveThemeMode(btn.dataset.themeMode);
        });
    });

    if (systemThemeQuery) {
        systemThemeListener = () => {
            if (currentThemeMode === 'auto') applyThemeMode('auto');
        };
        if (typeof systemThemeQuery.addEventListener === 'function') {
            systemThemeQuery.addEventListener('change', systemThemeListener);
        } else if (typeof systemThemeQuery.addListener === 'function') {
            systemThemeQuery.addListener(systemThemeListener);
        }
    }

    // --- Updates ---
    const updateBanner = document.getElementById('update-banner');
    const updateVersionEl = document.getElementById('update-version');
    const updateNotesEl = document.getElementById('update-notes');
    const updateProgressRow = document.getElementById('update-progress-row');
    const updateProgressFill = document.getElementById('update-progress-fill');
    const updateProgressText = document.getElementById('update-progress-text');
    const updateErrorEl = document.getElementById('update-error');
    const updateInstallBtn = document.getElementById('update-install-btn');
    const updateDismissBtn = document.getElementById('update-dismiss-btn');
    const updateSkipBtn = document.getElementById('update-skip-btn');
    const currentVersionEl = document.getElementById('current-version');
    const updateStatusText = document.getElementById('update-status-text');
    const autoUpdateToggle = document.getElementById('auto-update-toggle');
    const checkUpdateBtn = document.getElementById('check-update-btn');

    let updatePollTimer = null;
    let updateDismissed = false;
    let knownUpdateVersion = null;

    function loadVersion() {
        fetch('/api/version')
            .then(r => r.json())
            .then(data => {
                if (data.version) {
                    if (currentVersionEl) currentVersionEl.textContent = data.version;
                    var sidebarVersion = document.getElementById('settings-version');
                    if (sidebarVersion) sidebarVersion.textContent = 'v' + data.version;
                }
            })
            .catch(() => {});
    }

    function loadUpdateSettings() {
        fetch('/api/update/settings')
            .then(r => r.json())
            .then(data => {
                if (autoUpdateToggle) {
                    autoUpdateToggle.checked = data.auto_check !== false;
                }
            })
            .catch(() => {});
    }

    function pollUpdateStatus() {
        fetch('/api/update/status')
            .then(r => r.json())
            .then(data => {
                var status = data.status;

                // Update settings panel status text
                if (updateStatusText) {
                    if (status === 'checking') {
                        updateStatusText.textContent = 'Checking...';
                        updateStatusText.className = 'update-status-text';
                    } else if (status === 'available' || status === 'downloading' || status === 'verifying' || status === 'ready') {
                        updateStatusText.textContent = 'Update available: v' + data.latest_version;
                        updateStatusText.className = 'update-status-text available';
                    } else if (status === 'installing') {
                        updateStatusText.textContent = 'Installing...';
                        updateStatusText.className = 'update-status-text';
                    } else if (status === 'error') {
                        updateStatusText.textContent = 'Error: ' + (data.error || 'Unknown');
                        updateStatusText.className = 'update-status-text error';
                    } else {
                        updateStatusText.textContent = 'Up to date';
                        updateStatusText.className = 'update-status-text';
                    }
                }

                // Show/hide banner based on status
                if (status === 'available' || status === 'downloading' || status === 'verifying' || status === 'ready' || status === 'installing') {
                    // Reset dismissed flag if a new version appears
                    if (data.latest_version !== knownUpdateVersion) {
                        knownUpdateVersion = data.latest_version;
                        updateDismissed = false;
                    }

                    if (!updateDismissed) {
                        updateVersionEl.textContent = 'v' + data.latest_version;

                        // Show truncated release notes
                        if (data.release && data.release.notes) {
                            var lines = data.release.notes.split('\n').slice(0, 3).join('\n');
                            updateNotesEl.textContent = lines.length > 200 ? lines.substring(0, 200) + '...' : lines;
                            updateNotesEl.classList.remove('hidden');
                        } else {
                            updateNotesEl.classList.add('hidden');
                        }

                        // Progress bar
                        if (status === 'downloading') {
                            var pct = Math.round((data.progress || 0) * 100);
                            updateProgressFill.style.width = pct + '%';
                            updateProgressText.textContent = pct + '%';
                            updateProgressRow.classList.remove('hidden');
                        } else if (status === 'verifying') {
                            updateProgressFill.style.width = '100%';
                            updateProgressText.textContent = 'Verifying...';
                            updateProgressRow.classList.remove('hidden');
                        } else {
                            updateProgressRow.classList.add('hidden');
                        }

                        // Error
                        if (status === 'error' || data.error) {
                            updateErrorEl.textContent = data.error || 'An error occurred';
                            updateErrorEl.classList.remove('hidden');
                        } else {
                            updateErrorEl.classList.add('hidden');
                        }

                        // Button states
                        if (status === 'available') {
                            updateInstallBtn.textContent = 'Download & Install';
                            updateInstallBtn.disabled = false;
                            updateSkipBtn.classList.remove('hidden');
                        } else if (status === 'downloading') {
                            updateInstallBtn.textContent = 'Cancel';
                            updateInstallBtn.disabled = false;
                            updateSkipBtn.classList.add('hidden');
                        } else if (status === 'verifying') {
                            updateInstallBtn.textContent = 'Verifying...';
                            updateInstallBtn.disabled = true;
                            updateSkipBtn.classList.add('hidden');
                        } else if (status === 'ready') {
                            updateInstallBtn.textContent = 'Install & Restart';
                            updateInstallBtn.disabled = false;
                            updateSkipBtn.classList.add('hidden');
                        } else if (status === 'installing') {
                            updateInstallBtn.textContent = 'Restarting...';
                            updateInstallBtn.disabled = true;
                            updateSkipBtn.classList.add('hidden');
                        }

                        updateBanner.classList.remove('hidden');
                    }
                } else {
                    updateBanner.classList.add('hidden');
                    if (status === 'idle') {
                        updateDismissed = false;
                    }
                }
            })
            .catch(() => {});
    }

    function startUpdatePolling() {
        pollUpdateStatus();
        updatePollTimer = setInterval(pollUpdateStatus, 5000);
    }

    function stopUpdatePolling() {
        if (updatePollTimer) {
            clearInterval(updatePollTimer);
            updatePollTimer = null;
        }
    }

    // Update button: action depends on current label
    if (updateInstallBtn) {
        updateInstallBtn.addEventListener('click', () => {
            var label = updateInstallBtn.textContent;
            if (label === 'Cancel') {
                fetch('/api/update/cancel', { method: 'POST' });
            } else if (label === 'Install & Restart') {
                fetch('/api/update/install', { method: 'POST' });
                updateInstallBtn.textContent = 'Restarting...';
                updateInstallBtn.disabled = true;
            } else if (label === 'Download & Install') {
                fetch('/api/update/download', { method: 'POST' });
            }
        });
    }

    if (updateDismissBtn) {
        updateDismissBtn.addEventListener('click', () => {
            updateDismissed = true;
            updateBanner.classList.add('hidden');
        });
    }

    if (updateSkipBtn) {
        updateSkipBtn.addEventListener('click', () => {
            if (knownUpdateVersion) {
                fetch('/api/update/skip', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ version: knownUpdateVersion }),
                });
            }
            updateDismissed = true;
            updateBanner.classList.add('hidden');
        });
    }

    if (checkUpdateBtn) {
        checkUpdateBtn.addEventListener('click', () => {
            checkUpdateBtn.disabled = true;
            checkUpdateBtn.textContent = 'Checking...';
            fetch('/api/update/check', { method: 'POST' })
                .then(() => {
                    setTimeout(() => {
                        checkUpdateBtn.disabled = false;
                        checkUpdateBtn.textContent = 'Check for Updates';
                        pollUpdateStatus();
                    }, 2000);
                })
                .catch(() => {
                    checkUpdateBtn.disabled = false;
                    checkUpdateBtn.textContent = 'Check for Updates';
                });
        });
    }

    if (autoUpdateToggle) {
        autoUpdateToggle.addEventListener('change', () => {
            fetch('/api/update/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ auto_check: autoUpdateToggle.checked }),
            });
        });
    }

    function teardown() {
        hideOnboarding();
        stopStatusPolling();
        stopHistoryRefresh();
        stopUpdatePolling();

        if (searchTimeout) {
            clearTimeout(searchTimeout);
            searchTimeout = null;
        }
        if (toastHideTimer) {
            clearTimeout(toastHideTimer);
            toastHideTimer = null;
        }
        if (reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
        if (capturePollId) {
            clearInterval(capturePollId);
            capturePollId = null;
        }
        if (captureTimeoutId) {
            clearTimeout(captureTimeoutId);
            captureTimeoutId = null;
        }
        if (pendingCaptureSave) {
            clearTimeout(pendingCaptureSave);
            pendingCaptureSave = null;
        }
        if (captureTarget) {
            captureTarget = null;
            hotkeyBtn.classList.remove('capturing');
            repasteBtn.classList.remove('capturing');
            fetch('/api/settings/hotkey/capture', { method: 'DELETE' });
        }
        if (systemThemeQuery && systemThemeListener) {
            if (typeof systemThemeQuery.removeEventListener === 'function') {
                systemThemeQuery.removeEventListener('change', systemThemeListener);
            } else if (typeof systemThemeQuery.removeListener === 'function') {
                systemThemeQuery.removeListener(systemThemeListener);
            }
        }
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            ws.onclose = null;
            ws.close();
        }
        ws = null;
    }

    window.addEventListener('beforeunload', teardown);

    // --- Smart Features Settings ---
    var smartCleanupToggle = document.getElementById('smart-cleanup-toggle');
    var contextFormattingToggle = document.getElementById('context-formatting-toggle');
    var snippetList = document.getElementById('snippet-list');
    var addSnippetBtn = document.getElementById('add-snippet-btn');
    var dictionaryInput = document.getElementById('dictionary-input');
    var dictionaryAddBtn = document.getElementById('dictionary-add-btn');
    var dictionaryTags = document.getElementById('dictionary-tags');

    // NOTE: innerHTML usage below is safe — all dynamic user text uses textContent.
    // SVG icons and &times; are static templates only.

    // --- AI Features (master toggle + sub-toggles) ---

    var aiFeaturesToggle = document.getElementById('ai-features-toggle');
    var aiFeaturesSub = document.getElementById('ai-features-sub');
    var llmDownloadPollTimer = null;

    function setSubTogglesEnabled(enabled) {
        smartCleanupToggle.disabled = !enabled;
        contextFormattingToggle.disabled = !enabled;
        if (enabled) {
            aiFeaturesSub.classList.remove('disabled');
        } else {
            aiFeaturesSub.classList.add('disabled');
        }
    }

    async function loadSmartCleanup() {
        try {
            var resp = await fetch('/api/settings/smart-cleanup');
            var data = await resp.json();
            smartCleanupToggle.checked = !!data.enabled;
        } catch (e) { /* ignore */ }
    }

    async function loadContextFormatting() {
        try {
            var resp = await fetch('/api/settings/context-formatting');
            var data = await resp.json();
            contextFormattingToggle.checked = !!data.enabled;
        } catch (e) { /* ignore */ }
    }

    async function loadAiFeaturesState() {
        try {
            var resp = await fetch('/api/llm/status');
            var data = await resp.json();
            if (data.loaded) {
                aiFeaturesToggle.checked = true;
                setSubTogglesEnabled(true);
            } else if (data.cached) {
                // Cached but not loaded — will load on first use
                aiFeaturesToggle.checked = true;
                setSubTogglesEnabled(true);
            } else if (data.status === 'downloading' || data.status === 'loading') {
                aiFeaturesToggle.checked = true;
                setSubTogglesEnabled(false);
                showLlmProgressToast();
                startLlmPoll();
            } else {
                aiFeaturesToggle.checked = false;
                setSubTogglesEnabled(false);
            }
        } catch (e) {
            aiFeaturesToggle.checked = false;
            setSubTogglesEnabled(false);
        }
    }

    aiFeaturesToggle.addEventListener('change', async function () {
        if (!aiFeaturesToggle.checked) {
            // Turning off — disable sub-toggles and turn off features
            setSubTogglesEnabled(false);
            smartCleanupToggle.checked = false;
            contextFormattingToggle.checked = false;
            fetch('/api/settings/smart-cleanup', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: false }),
            });
            fetch('/api/settings/context-formatting', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: false }),
            });
            updateLlmStatusCapsule();
            return;
        }

        // Turning on — check model status
        try {
            var resp = await fetch('/api/llm/status');
            var data = await resp.json();

            if (data.loaded || data.cached) {
                // Ready or cached — enable sub-toggles
                setSubTogglesEnabled(true);
                if (!data.loaded) {
                    // Trigger load in background
                    fetch('/api/llm/download', { method: 'POST' });
                }
                return;
            }

            // Not cached — show confirmation
            aiFeaturesToggle.checked = false;
            showLlmConfirmModal(function () {
                aiFeaturesToggle.checked = true;
                setSubTogglesEnabled(false);
                fetch('/api/llm/download', { method: 'POST' });
                showLlmProgressToast();
                startLlmPoll();
            });
        } catch (e) {
            aiFeaturesToggle.checked = false;
        }
    });

    smartCleanupToggle.addEventListener('change', function () {
        fetch('/api/settings/smart-cleanup', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: smartCleanupToggle.checked }),
        });
    });

    contextFormattingToggle.addEventListener('change', function () {
        fetch('/api/settings/context-formatting', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: contextFormattingToggle.checked }),
        });
    });

    // --- LLM download UI helpers ---

    function showLlmConfirmModal(onConfirm) {
        var old = document.getElementById('llm-confirm-overlay');
        if (old) old.remove();

        var overlay = document.createElement('div');
        overlay.id = 'llm-confirm-overlay';
        overlay.className = 'llm-confirm-overlay';

        var card = document.createElement('div');
        card.className = 'llm-confirm-card';

        var title = document.createElement('h3');
        title.textContent = 'Download Required';
        title.style.cssText = 'margin:0 0 8px;font-size:15px;color:var(--text-primary);';

        var desc = document.createElement('p');
        desc.textContent = 'AI features require a local language model (~600 MB). The download happens in the background \u2014 we\u2019ll let you know when it\u2019s ready.';
        desc.style.cssText = 'margin:0 0 16px;font-size:13px;color:var(--text-secondary);line-height:1.5;';

        var actions = document.createElement('div');
        actions.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;';

        var cancelBtn = document.createElement('button');
        cancelBtn.textContent = 'Cancel';
        cancelBtn.className = 'btn-secondary';
        cancelBtn.style.cssText = 'padding:6px 16px;font-size:13px;';
        cancelBtn.addEventListener('click', function () { overlay.remove(); });

        var okBtn = document.createElement('button');
        okBtn.textContent = 'Download';
        okBtn.className = 'btn-primary';
        okBtn.style.cssText = 'padding:6px 16px;font-size:13px;';
        okBtn.addEventListener('click', function () { overlay.remove(); onConfirm(); });

        actions.appendChild(cancelBtn);
        actions.appendChild(okBtn);
        card.appendChild(title);
        card.appendChild(desc);
        card.appendChild(actions);
        overlay.appendChild(card);
        document.body.appendChild(overlay);

        overlay.addEventListener('click', function (e) {
            if (e.target === overlay) overlay.remove();
        });
    }

    function showLlmProgressToast() {
        var existing = document.getElementById('llm-progress-toast');
        if (existing) return;

        var toast = document.createElement('div');
        toast.id = 'llm-progress-toast';
        toast.className = 'llm-progress-toast';
        toast.innerHTML =
            '<div class="llm-progress-header">' +
            '<span class="llm-progress-title">Downloading language model...</span>' +
            '</div>' +
            '<div class="llm-progress-bar-track"><div id="llm-progress-bar-fill" class="llm-progress-bar-fill"></div></div>' +
            '<span id="llm-progress-msg" class="llm-progress-msg">Starting download...</span>';
        document.body.appendChild(toast);
    }

    function updateLlmProgressToast(status, message, progress) {
        var toast = document.getElementById('llm-progress-toast');
        if (!toast) return;
        var titleEl = toast.querySelector('.llm-progress-title');
        var msgEl = document.getElementById('llm-progress-msg');
        var fillEl = document.getElementById('llm-progress-bar-fill');

        if (status === 'downloading') {
            var pct = Math.round((progress || 0) * 100);
            if (titleEl) titleEl.textContent = 'Downloading language model... ' + pct + '%';
            if (msgEl) msgEl.textContent = message || 'Downloading...';
            if (fillEl) {
                fillEl.className = 'llm-progress-bar-fill';
                fillEl.style.width = pct + '%';
                fillEl.style.background = '';
            }
        } else if (status === 'loading') {
            if (titleEl) titleEl.textContent = 'Loading model...';
            if (msgEl) msgEl.textContent = message || 'Loading into memory...';
            if (fillEl) fillEl.className = 'llm-progress-bar-fill loading';
        } else if (status === 'ready') {
            if (titleEl) titleEl.textContent = 'AI features are ready';
            if (msgEl) msgEl.textContent = 'You can now enable Smart Cleanup and Context Formatting.';
            if (fillEl) { fillEl.className = 'llm-progress-bar-fill'; fillEl.style.width = '100%'; fillEl.style.background = 'var(--green, #22c55e)'; }
        } else if (status === 'error') {
            if (titleEl) titleEl.textContent = 'Download failed';
            if (msgEl) msgEl.textContent = message || 'Error';
            if (fillEl) { fillEl.className = 'llm-progress-bar-fill'; fillEl.style.width = '100%'; fillEl.style.background = 'var(--red, #ef4444)'; }
        }
    }

    function dismissLlmProgressToast() {
        var toast = document.getElementById('llm-progress-toast');
        if (toast) {
            toast.style.opacity = '0';
            setTimeout(function () { toast.remove(); }, 300);
        }
    }

    function startLlmPoll() {
        if (llmDownloadPollTimer) return;
        llmDownloadPollTimer = setInterval(async function () {
            try {
                var resp = await fetch('/api/llm/status');
                var data = await resp.json();
                updateLlmProgressToast(data.status, data.message, data.progress);

                updateLlmStatusCapsule(data.status);
                if (data.status === 'ready') {
                    clearInterval(llmDownloadPollTimer);
                    llmDownloadPollTimer = null;
                    setSubTogglesEnabled(true);
                    updateLlmProgressToast('ready', '');
                    setTimeout(dismissLlmProgressToast, 4000);
                } else if (data.status === 'error') {
                    clearInterval(llmDownloadPollTimer);
                    llmDownloadPollTimer = null;
                    aiFeaturesToggle.checked = false;
                    setSubTogglesEnabled(false);
                    updateLlmStatusCapsule('error');
                    setTimeout(dismissLlmProgressToast, 5000);
                }
            } catch (e) { /* ignore */ }
        }, 1500);
    }

    // Snippets
    var snippetsData = [];

    function createSvgIcon(pathMarkup) {
        var wrapper = document.createElement('span');
        wrapper.style.display = 'inline-flex';
        wrapper.style.alignItems = 'center';
        // Static SVG icon markup only — no user data
        wrapper.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + pathMarkup + '</svg>'; // safe: static SVG paths only
        return wrapper.firstChild;
    }

    var ICON_EDIT = '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>';
    var ICON_DELETE = '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>';
    var ICON_CHECK = '<polyline points="20 6 9 17 4 12"/>';
    var ICON_CLOSE = '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>';

    function renderSnippets() {
        snippetList.textContent = '';
        snippetsData.forEach(function (snip, idx) {
            var item = document.createElement('div');
            item.className = 'snippet-item';

            var trigger = document.createElement('span');
            trigger.className = 'snippet-trigger';
            trigger.textContent = snip.trigger;

            var arrow = document.createElement('span');
            arrow.className = 'snippet-arrow';
            arrow.textContent = '\u2192';

            var expansion = document.createElement('span');
            expansion.className = 'snippet-expansion';
            expansion.textContent = snip.expansion;

            var actions = document.createElement('span');
            actions.className = 'snippet-actions';

            var editBtn = document.createElement('button');
            editBtn.className = 'btn-icon';
            editBtn.title = 'Edit';
            editBtn.appendChild(createSvgIcon(ICON_EDIT));
            editBtn.addEventListener('click', function () { showSnippetEditRow(idx); });

            var delBtn = document.createElement('button');
            delBtn.className = 'btn-icon danger';
            delBtn.title = 'Delete';
            delBtn.appendChild(createSvgIcon(ICON_DELETE));
            delBtn.addEventListener('click', function () { deleteSnippet(idx); });

            actions.appendChild(editBtn);
            actions.appendChild(delBtn);
            item.appendChild(trigger);
            item.appendChild(arrow);
            item.appendChild(expansion);
            item.appendChild(actions);
            snippetList.appendChild(item);
        });
    }

    function showSnippetEditRow(idx) {
        var existing = snippetsData[idx];
        var row = document.createElement('div');
        row.className = 'snippet-edit-row';

        var triggerInput = document.createElement('input');
        triggerInput.type = 'text';
        triggerInput.placeholder = 'Trigger';
        triggerInput.value = existing ? existing.trigger : '';
        triggerInput.style.maxWidth = '100px';

        var expInput = document.createElement('input');
        expInput.type = 'text';
        expInput.placeholder = 'Expansion';
        expInput.value = existing ? existing.expansion : '';

        var saveBtn = document.createElement('button');
        saveBtn.className = 'btn-icon save';
        saveBtn.title = 'Save';
        saveBtn.appendChild(createSvgIcon(ICON_CHECK));
        saveBtn.addEventListener('click', function () {
            var t = triggerInput.value.trim();
            var e = expInput.value.trim();
            if (!t || !e) return;
            if (existing) {
                snippetsData[idx] = { trigger: t, expansion: e };
            } else {
                snippetsData.push({ trigger: t, expansion: e });
            }
            saveSnippets();
        });

        var cancelBtn = document.createElement('button');
        cancelBtn.className = 'btn-icon';
        cancelBtn.title = 'Cancel';
        cancelBtn.appendChild(createSvgIcon(ICON_CLOSE));
        cancelBtn.addEventListener('click', function () { renderSnippets(); });

        row.appendChild(triggerInput);
        row.appendChild(expInput);
        row.appendChild(saveBtn);
        row.appendChild(cancelBtn);

        if (existing) {
            var items = snippetList.querySelectorAll('.snippet-item, .snippet-edit-row');
            if (items[idx]) {
                snippetList.replaceChild(row, items[idx]);
            }
        } else {
            snippetList.appendChild(row);
        }
        triggerInput.focus();
    }

    async function saveSnippets() {
        try {
            await fetch('/api/settings/snippets', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ snippets: snippetsData }),
            });
        } catch (e) { /* ignore */ }
        renderSnippets();
    }

    async function deleteSnippet(idx) {
        snippetsData.splice(idx, 1);
        await saveSnippets();
    }

    async function loadSnippets() {
        try {
            var resp = await fetch('/api/settings/snippets');
            var data = await resp.json();
            snippetsData = Array.isArray(data.snippets) ? data.snippets : [];
        } catch (e) {
            snippetsData = [];
        }
        renderSnippets();
    }

    addSnippetBtn.addEventListener('click', function () {
        showSnippetEditRow(snippetsData.length);
    });

    // Dictionary
    var dictionaryTerms = [];

    function renderDictionary() {
        dictionaryTags.textContent = '';
        dictionaryTerms.forEach(function (term, idx) {
            var tag = document.createElement('span');
            tag.className = 'dictionary-tag';

            var text = document.createElement('span');
            text.textContent = term;

            var removeBtn = document.createElement('button');
            removeBtn.className = 'dictionary-tag-remove';
            removeBtn.textContent = '\u00d7';
            removeBtn.title = 'Remove';
            removeBtn.addEventListener('click', function () { removeDictionaryTerm(idx); });

            tag.appendChild(text);
            tag.appendChild(removeBtn);
            dictionaryTags.appendChild(tag);
        });
    }

    async function loadDictionary() {
        try {
            var resp = await fetch('/api/settings/dictionary');
            var data = await resp.json();
            dictionaryTerms = Array.isArray(data.terms) ? data.terms : [];
        } catch (e) {
            dictionaryTerms = [];
        }
        renderDictionary();
    }

    async function saveDictionary() {
        try {
            await fetch('/api/settings/dictionary', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ terms: dictionaryTerms }),
            });
        } catch (e) { /* ignore */ }
        renderDictionary();
    }

    function addDictionaryTerm() {
        var term = dictionaryInput.value.trim();
        if (!term) return;
        if (dictionaryTerms.indexOf(term) !== -1) {
            dictionaryInput.value = '';
            return;
        }
        dictionaryTerms.push(term);
        dictionaryInput.value = '';
        saveDictionary();
    }

    async function removeDictionaryTerm(idx) {
        dictionaryTerms.splice(idx, 1);
        await saveDictionary();
    }

    var dictionaryInputRow = document.getElementById('dictionary-input-row');
    dictionaryAddBtn.addEventListener('click', function () {
        if (dictionaryInputRow.classList.contains('hidden')) {
            dictionaryInputRow.classList.remove('hidden');
            dictionaryInput.focus();
        } else {
            addDictionaryTerm();
        }
    });
    dictionaryInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            addDictionaryTerm();
        }
    });

    // Start disabled
    setMicDisabled(true);
    connect();
    startHistoryRefresh();
    loadThemeSettings();
    loadHistory(false);
    loadUsageStats();
    loadHotkey();
    loadInsertionSettings();
    loadVersion();
    loadUpdateSettings();
    startUpdatePolling();
    loadAiFeaturesState();
    loadSmartCleanup();
    loadContextFormatting();
    loadSnippets();
    loadDictionary();
    setMode('dictate');

    // --- RAM Advisory ---
    (async function checkRamAdvisory() {
        try {
            var resp = await fetch('/api/system/ram');
            var data = await resp.json();
            var totalGb = data.total_gb || 0;
            if (totalGb > 0 && totalGb <= 8) {
                var dismissed = localStorage.getItem('ram_banner_dismissed');
                if (dismissed === 'true') return;
                var banner = document.getElementById('ram-banner');
                var amountEl = document.getElementById('ram-amount');
                if (banner && amountEl) {
                    amountEl.textContent = totalGb;
                    banner.classList.remove('hidden');
                }
                var dismissBtn = document.getElementById('ram-banner-dismiss');
                var hideBtn = document.getElementById('ram-banner-hide');
                if (dismissBtn) {
                    dismissBtn.addEventListener('click', function () {
                        banner.classList.add('hidden');
                    });
                }
                if (hideBtn) {
                    hideBtn.addEventListener('click', function () {
                        localStorage.setItem('ram_banner_dismissed', 'true');
                        banner.classList.add('hidden');
                    });
                }
            }
        } catch (e) { /* ignore */ }
    })();

    // --- Snippet Picker Overlay ---
    var snippetOverlay = document.getElementById('snippet-overlay');
    var snippetSearchInput = document.getElementById('snippet-search');
    var snippetResultsContainer = document.getElementById('snippet-results');
    var snippetSelectedIndex = 0;
    var snippetFilteredList = [];

    function showSnippetOverlay() {
        snippetSearchInput.value = '';
        snippetSelectedIndex = 0;
        snippetOverlay.classList.remove('hidden');
        renderSnippetResults('');
        setTimeout(function() { snippetSearchInput.focus(); }, 50);
    }

    function hideSnippetOverlay() {
        snippetOverlay.classList.add('hidden');
    }

    function renderSnippetResults(query) {
        fetch('/api/settings/snippets').then(function(r) { return r.json(); }).then(function(data) {
            var all = data.snippets || [];
            var q = query.toLowerCase();
            snippetFilteredList = q ? all.filter(function(s) {
                return s.trigger.toLowerCase().indexOf(q) !== -1 ||
                       s.expansion.toLowerCase().indexOf(q) !== -1;
            }) : all;

            snippetResultsContainer.innerHTML = '';
            if (snippetFilteredList.length === 0) {
                var empty = document.createElement('div');
                empty.className = 'snippet-results-empty';
                empty.textContent = all.length === 0 ? 'No snippets configured. Add some in Settings.' : 'No matches.';
                snippetResultsContainer.appendChild(empty);
                return;
            }

            snippetSelectedIndex = Math.min(snippetSelectedIndex, snippetFilteredList.length - 1);
            snippetFilteredList.forEach(function(s, i) {
                var item = document.createElement('div');
                item.className = 'snippet-result-item' + (i === snippetSelectedIndex ? ' selected' : '');
                var trig = document.createElement('span');
                trig.className = 'snippet-result-trigger';
                trig.textContent = s.trigger;
                var exp = document.createElement('span');
                exp.className = 'snippet-result-expansion';
                exp.textContent = s.expansion;
                item.appendChild(trig);
                item.appendChild(exp);
                item.addEventListener('click', function() { insertSnippet(s); });
                snippetResultsContainer.appendChild(item);
            });
        });
    }

    function insertSnippet(snippet) {
        hideSnippetOverlay();
        // Copy expansion to clipboard and paste
        navigator.clipboard.writeText(snippet.expansion).then(function() {
            document.execCommand('paste');
        });
    }

    snippetSearchInput.addEventListener('input', function() {
        snippetSelectedIndex = 0;
        renderSnippetResults(snippetSearchInput.value);
    });

    snippetSearchInput.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            e.preventDefault();
            hideSnippetOverlay();
        } else if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (snippetFilteredList.length > 0) {
                snippetSelectedIndex = (snippetSelectedIndex + 1) % snippetFilteredList.length;
                renderSnippetResults(snippetSearchInput.value);
            }
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (snippetFilteredList.length > 0) {
                snippetSelectedIndex = (snippetSelectedIndex - 1 + snippetFilteredList.length) % snippetFilteredList.length;
                renderSnippetResults(snippetSearchInput.value);
            }
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (snippetFilteredList.length > 0 && snippetFilteredList[snippetSelectedIndex]) {
                insertSnippet(snippetFilteredList[snippetSelectedIndex]);
            }
        }
    });

    snippetOverlay.addEventListener('click', function(e) {
        if (e.target === snippetOverlay) hideSnippetOverlay();
    });

    // Expose for pywebview evaluate_js
    window.showSnippetOverlay = showSnippetOverlay;

    // --- Profile (local display name) ---
    var profileNameInput = document.getElementById('profile-display-name');
    var profileSaveBtn = document.getElementById('profile-save-btn');
    var profileFeedback = document.getElementById('profile-feedback');

    async function loadProfile() {
        try {
            var resp = await fetch('/api/profile');
            var data = await resp.json();
            if (profileNameInput) profileNameInput.value = data.display_name || '';
        } catch (e) { /* ignore */ }
    }

    async function saveProfile() {
        if (!profileNameInput) return;
        var name = profileNameInput.value.trim();
        if (profileSaveBtn) profileSaveBtn.disabled = true;
        try {
            var resp = await fetch('/api/profile', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ display_name: name }),
            });
            var data = await resp.json();
            if (data.ok) {
                showProfileFeedback('Saved', true);
            } else {
                showProfileFeedback(data.error || 'Failed to save', false);
            }
        } catch (e) {
            showProfileFeedback('Network error', false);
        } finally {
            if (profileSaveBtn) profileSaveBtn.disabled = false;
        }
    }

    function showProfileFeedback(msg, success) {
        if (!profileFeedback) return;
        profileFeedback.textContent = msg;
        profileFeedback.style.display = 'block';
        profileFeedback.style.color = success ? '#28a745' : '#dc3545';
        setTimeout(function () { profileFeedback.style.display = 'none'; }, 3000);
    }

    if (profileSaveBtn) profileSaveBtn.addEventListener('click', saveProfile);
    if (profileNameInput) profileNameInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') saveProfile();
    });

    // --- Settings Export/Import ---
    var exportBtn = document.getElementById('settings-export-btn');
    var importBtn = document.getElementById('settings-import-btn');
    var importFileInput = document.getElementById('settings-import-file');
    var resetBtn = document.getElementById('settings-reset-btn');
    var transferFeedback = document.getElementById('transfer-feedback');

    function showTransferFeedback(msg, success) {
        if (!transferFeedback) return;
        transferFeedback.textContent = msg;
        transferFeedback.style.display = 'block';
        transferFeedback.style.color = success ? '#28a745' : '#dc3545';
        setTimeout(function () { transferFeedback.style.display = 'none'; }, 4000);
    }

    if (exportBtn) {
        exportBtn.addEventListener('click', async function () {
            try {
                var resp = await fetch('/api/settings/export');
                var data = await resp.json();
                var blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
                var url = URL.createObjectURL(blob);
                var a = document.createElement('a');
                a.href = url;
                a.download = 'dashscribe-settings.json';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                showTransferFeedback('Settings exported', true);
            } catch (e) {
                showTransferFeedback('Export failed', false);
            }
        });
    }

    if (importBtn) {
        importBtn.addEventListener('click', function () {
            if (importFileInput) importFileInput.click();
        });
    }

    if (importFileInput) {
        importFileInput.addEventListener('change', async function () {
            var file = importFileInput.files[0];
            if (!file) return;
            try {
                var text = await file.text();
                var data = JSON.parse(text);
                if (!data.version) {
                    showTransferFeedback('Invalid settings file', false);
                    return;
                }
                if (!confirm('This will overwrite your current settings. Continue?')) return;
                var resp = await fetch('/api/settings/import', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data),
                });
                var result = await resp.json();
                if (result.ok) {
                    showTransferFeedback('Settings imported. Reload to apply.', true);
                    setTimeout(function () { location.reload(); }, 2000);
                } else {
                    showTransferFeedback(result.error || 'Import failed', false);
                }
            } catch (e) {
                showTransferFeedback('Invalid file format', false);
            }
            importFileInput.value = '';
        });
    }

    if (resetBtn) {
        resetBtn.addEventListener('click', async function () {
            if (!confirm('Reset all settings to defaults? This cannot be undone.')) return;
            try {
                var resp = await fetch('/api/settings/reset', { method: 'POST' });
                var result = await resp.json();
                if (result.ok) {
                    showTransferFeedback('Settings reset. Reloading...', true);
                    setTimeout(function () { location.reload(); }, 1500);
                } else {
                    showTransferFeedback(result.error || 'Reset failed', false);
                }
            } catch (e) {
                showTransferFeedback('Network error', false);
            }
        });
    }

    // Load profile on settings open
    loadProfile();
})();

