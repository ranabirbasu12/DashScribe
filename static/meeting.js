// meeting.js — Meeting live transcription UI
(function () {
    'use strict';

    // --- State ---
    var ws = null;
    var currentMeetingId = null;
    var currentView = 'list'; // 'list' | 'setup' | 'recording' | 'review'
    var elapsedTimer = null;
    var elapsedSeconds = 0;
    var reconnectTimer = null;
    var isPaused = false;
    var selectedMode = 'listen';
    var editMode = false;
    var startInFlight = false;

    // --- DOM refs ---
    var listView = document.getElementById('meeting-list');
    var recordingView = document.getElementById('meeting-recording');
    var setupView = document.getElementById('meeting-setup');
    var reviewView = document.getElementById('meeting-review');
    var startBtn = document.getElementById('meeting-start-btn');
    var pauseBtn = document.getElementById('meeting-pause-btn');
    var stopBtn = document.getElementById('meeting-stop-btn');
    var discardBtn = document.getElementById('meeting-discard-btn');
    var titleInput = document.getElementById('meeting-title');
    var elapsedEl = document.getElementById('meeting-elapsed');
    var transcriptEl = document.getElementById('meeting-transcript');
    var meetingList = document.getElementById('meeting-meetings');
    var emptyState = document.getElementById('meeting-empty');
    var searchInput = document.getElementById('meeting-search');
    var recordingDot = document.querySelector('.meeting-recording-dot');
    var warningBanner = document.getElementById('meeting-warning-banner');

    // Setup
    var appPicker = document.getElementById('meeting-app-picker'); // hidden input
    var modeToggleBtns = document.querySelectorAll('.mode-toggle-btn');
    var modeDesc = document.getElementById('mode-desc');
    var setupTitle = document.getElementById('meeting-setup-title');
    var setupCancel = document.getElementById('meeting-setup-cancel');
    var setupGo = document.getElementById('meeting-setup-go');
    var setupError = document.getElementById('meeting-setup-error');
    var appHint = document.getElementById('meeting-app-hint');

    // Custom dropdown
    var dropdown = document.getElementById('app-dropdown');
    var dropdownTrigger = document.getElementById('app-dropdown-trigger');
    var dropdownLabel = document.getElementById('app-dropdown-label');
    var dropdownMenu = document.getElementById('app-dropdown-menu');
    var dropdownList = document.getElementById('app-dropdown-list');
    var refreshBtn = document.getElementById('app-refresh-btn');
    var levelPollTimer = null;
    var monitorActive = false;
    var appsList = []; // cached apps list
    var autoRefreshTimer = null;

    // Review
    var reviewTitle = document.getElementById('mt-review-title');
    var reviewMeta = document.getElementById('mt-review-meta');
    var reviewTranscript = document.getElementById('mt-review-transcript');
    var reviewLabels = document.getElementById('mt-review-labels');
    var reviewEditBtn = document.getElementById('mt-review-edit-btn');
    var reviewPlayer = document.getElementById('mt-review-player');

    // --- Utility ---
    function clearChildren(el) {
        while (el.firstChild) el.removeChild(el.firstChild);
    }

    function formatTime(s) {
        var m = Math.floor(s / 60);
        var sec = Math.floor(s % 60);
        return String(m).padStart(2, '0') + ':' + String(sec).padStart(2, '0');
    }

    function formatDate(iso) {
        if (!iso) return '';
        var d = new Date(iso);
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
            + ' ' + d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    }

    function createSvgIcon(pathD, size) {
        var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('width', size || '16');
        svg.setAttribute('height', size || '16');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('fill', 'none');
        svg.setAttribute('stroke', 'currentColor');
        svg.setAttribute('stroke-width', '2');
        var paths = Array.isArray(pathD) ? pathD : [pathD];
        paths.forEach(function (d) {
            var path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            path.setAttribute('d', d);
            svg.appendChild(path);
        });
        return svg;
    }

    // --- Error display ---
    function showSetupError(message) {
        if (!setupError) return;
        setupError.textContent = message;
        setupError.style.display = '';
    }

    function hideSetupError() {
        if (!setupError) return;
        setupError.textContent = '';
        setupError.style.display = 'none';
    }

    function showRecordingWarning(message) {
        if (!warningBanner) return;
        warningBanner.textContent = message;
        warningBanner.style.display = '';
        setTimeout(function () {
            warningBanner.style.display = 'none';
        }, 5000);
    }

    function setSetupLoading(loading) {
        if (!setupGo) return;
        startInFlight = loading;
        setupGo.disabled = loading;
        setupGo.textContent = loading ? 'Starting...' : 'Start Recording';
    }

    // --- View switching ---
    function showView(name) {
        currentView = name;
        // Must use explicit display values to override `.meeting-view { display: none }` CSS rule
        // Recording and review use flex for scroll layout
        listView.style.display = name === 'list' ? 'block' : 'none';
        setupView.style.display = name === 'setup' ? 'block' : 'none';
        recordingView.style.display = name === 'recording' ? 'flex' : 'none';
        reviewView.style.display = name === 'review' ? 'flex' : 'none';
        // Auto-refresh app list while on setup view
        if (name === 'setup') {
            startAutoRefresh();
        } else {
            stopAutoRefresh();
        }
    }

    // --- WebSocket ---
    function connectWS() {
        if (ws && ws.readyState <= 1) return;
        var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(proto + '//' + location.host + '/ws/meeting');
        ws.onmessage = function (e) {
            var msg = JSON.parse(e.data);
            handleMessage(msg);
        };
        ws.onclose = function () {
            ws = null;
            if (currentView === 'recording') {
                reconnectTimer = setTimeout(connectWS, 2000);
            }
            // If we were waiting for start to complete, abort back to setup
            if (startInFlight) {
                setSetupLoading(false);
                showSetupError('Connection lost. Please try again.');
            }
        };
        ws.onerror = function () {
            // Will trigger onclose
        };
    }

    function sendAction(action, data) {
        if (!ws || ws.readyState !== 1) return;
        ws.send(JSON.stringify(Object.assign({ action: action }, data || {})));
    }

    function handleMessage(msg) {
        if (msg.type === 'segment') {
            appendSegment(msg);
        } else if (msg.type === 'status') {
            handleStatus(msg);
        } else if (msg.type === 'error') {
            handleError(msg);
        }
    }

    function handleError(msg) {
        var message = msg.message || 'An error occurred';
        console.error('Meeting error:', message);

        // If we were waiting for start, show error on setup screen and go back
        if (startInFlight) {
            setSetupLoading(false);
            showSetupError(message);
            showView('setup');
            return;
        }

        // If we're recording, show inline warning banner
        if (currentView === 'recording') {
            showRecordingWarning(message);
            return;
        }
    }

    function handleStatus(msg) {
        if (msg.state === 'recording') {
            setSetupLoading(false);
            hideSetupError();
            if (msg.meeting_id) currentMeetingId = msg.meeting_id;
            showView('recording');
            if (warningBanner) warningBanner.style.display = 'none';
            recordingDot.style.display = '';
            isPaused = false;
            pauseBtn.textContent = 'Pause';
            startElapsedTimer();
        } else if (msg.state === 'paused') {
            isPaused = true;
            pauseBtn.textContent = 'Resume';
            stopElapsedTimer();
        } else if (msg.state === 'stopped') {
            recordingDot.style.display = 'none';
            stopElapsedTimer();
            showView('list');
            loadMeetings();
            if (msg.meeting_id) {
                openReview(msg.meeting_id);
            }
        } else if (msg.state === 'discarded') {
            recordingDot.style.display = 'none';
            stopElapsedTimer();
            showView('list');
            loadMeetings();
        }
    }

    // --- Segments ---
    function appendSegment(seg) {
        var div = document.createElement('div');
        div.className = 'segment ' + (seg.speaker === 'you' ? 'segment-you' : 'segment-others');
        div.setAttribute('data-index', seg.index);

        var label = document.createElement('span');
        label.className = 'speaker-label ' + (seg.speaker === 'you' ? 'speaker-label-you' : 'speaker-label-others');
        label.textContent = seg.speaker === 'you' ? 'You' : 'Others';
        div.appendChild(label);

        var text = document.createElement('span');
        text.className = 'segment-text';
        text.textContent = seg.text;
        div.appendChild(text);

        transcriptEl.appendChild(div);
        div.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }

    // --- Elapsed timer ---
    function startElapsedTimer() {
        elapsedSeconds = 0;
        elapsedEl.textContent = '00:00';
        elapsedTimer = setInterval(function () {
            elapsedSeconds++;
            elapsedEl.textContent = formatTime(elapsedSeconds);
        }, 1000);
    }

    function stopElapsedTimer() {
        if (elapsedTimer) {
            clearInterval(elapsedTimer);
            elapsedTimer = null;
        }
    }

    // --- Waveform helpers ---
    function createWaveform() {
        var container = document.createElement('div');
        container.className = 'app-waveform';
        for (var i = 0; i < 5; i++) {
            var bar = document.createElement('div');
            bar.className = 'app-waveform-bar';
            bar.style.height = '3px';
            container.appendChild(bar);
        }
        return container;
    }

    function updateWaveform(waveformEl, level) {
        if (!waveformEl) return;
        var bars = waveformEl.querySelectorAll('.app-waveform-bar');
        var isActive = level > 0.02;
        waveformEl.classList.toggle('active', isActive);
        // Generate varied bar heights from the level
        var heights = [0.5, 0.8, 1.0, 0.7, 0.4];
        for (var i = 0; i < bars.length; i++) {
            var h = isActive ? Math.max(3, level * heights[i] * 18) : 3;
            bars[i].style.height = h + 'px';
        }
    }

    // --- Custom dropdown ---
    function toggleDropdown(open) {
        var isOpen = open !== undefined ? open : !dropdown.classList.contains('open');
        dropdown.classList.toggle('open', isOpen);
        if (isOpen) {
            var rect = dropdownTrigger.getBoundingClientRect();
            dropdownMenu.style.top = rect.bottom + 'px';
            dropdownMenu.style.left = rect.left + 'px';
            dropdownMenu.style.width = rect.width + 'px';
            dropdownMenu.style.display = 'block';
            startLevelMonitor();
        } else {
            dropdownMenu.style.display = 'none';
            stopLevelMonitor();
        }
    }

    dropdownTrigger.addEventListener('click', function (e) {
        e.stopPropagation();
        toggleDropdown();
    });

    document.addEventListener('click', function (e) {
        if (!dropdown.contains(e.target)) {
            toggleDropdown(false);
        }
    });

    function selectApp(bundleId, displayName) {
        appPicker.value = bundleId;
        dropdownLabel.textContent = displayName || 'Select app';
        // Update trigger waveform
        var triggerWave = dropdownTrigger.querySelector('.app-waveform');
        if (!triggerWave) {
            triggerWave = createWaveform();
            dropdownTrigger.insertBefore(triggerWave, dropdownLabel);
        }
        // Mark selected in list
        var items = dropdownList.querySelectorAll('.app-dropdown-item');
        items.forEach(function (item) {
            item.classList.toggle('selected', item.getAttribute('data-bundle-id') === bundleId);
        });
        toggleDropdown(false);
        hideSetupError();
        setupGo.disabled = !bundleId;
    }

    // --- App picker ---
    function loadApps(silent) {
        if (!silent) {
            dropdownLabel.textContent = 'Loading apps...';
            setupGo.disabled = true;
            clearChildren(dropdownList);
        }

        fetch('/api/meeting/apps')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var newApps = data.apps || [];
                // Skip re-render if app list hasn't changed
                var oldIds = appsList.map(function (a) { return a.bundle_id; }).join(',');
                var newIds = newApps.map(function (a) { return a.bundle_id; }).join(',');
                if (silent && oldIds === newIds) return;
                var prevSelected = appPicker.value;
                appsList = newApps;
                renderAppList(appsList, prevSelected);
            })
            .catch(function () {
                if (silent) return;
                appsList = [];
                dropdownLabel.textContent = 'Error loading apps';
                if (appHint) appHint.style.display = '';
                setupGo.disabled = true;
            });
    }

    function renderAppList(apps, preserveSelection) {
        clearChildren(dropdownList);
        if (apps.length === 0) {
            dropdownLabel.textContent = 'No meeting apps detected';
            if (appHint) appHint.style.display = '';
            setupGo.disabled = true;
            return;
        }
        if (appHint) appHint.style.display = 'none';

        var hasKnown = false;
        var hasOther = false;

        apps.forEach(function (app) {
            var isKnown = !!app.display_name;
            if (isKnown && !hasKnown) hasKnown = true;
            if (!isKnown && !hasOther) hasOther = true;
        });

        apps.forEach(function (app, idx) {
            var isKnown = !!app.display_name;
            // Add separator between known and other apps
            if (hasKnown && hasOther && !isKnown) {
                var prevApp = idx > 0 ? apps[idx - 1] : null;
                if (prevApp && prevApp.display_name) {
                    var sep = document.createElement('div');
                    sep.className = 'app-dropdown-separator';
                    dropdownList.appendChild(sep);
                }
            }

            var item = document.createElement('button');
            item.className = 'app-dropdown-item' + (isKnown ? ' known' : '');
            item.type = 'button';
            item.setAttribute('data-bundle-id', app.bundle_id);

            var waveform = createWaveform();
            item.appendChild(waveform);

            var nameEl = document.createElement('span');
            nameEl.className = 'app-dropdown-item-name';
            nameEl.textContent = app.display_name || app.name;
            item.appendChild(nameEl);

            item.addEventListener('click', function (e) {
                e.stopPropagation();
                selectApp(app.bundle_id, app.display_name || app.name);
            });

            dropdownList.appendChild(item);
        });

        // Restore previous selection or auto-select first known app
        var toSelect = null;
        if (preserveSelection) {
            toSelect = apps.find(function (a) { return a.bundle_id === preserveSelection; });
        }
        if (!toSelect) {
            toSelect = apps.find(function (a) { return !!a.display_name; }) || apps[0];
        }
        selectApp(toSelect.bundle_id, toSelect.display_name || toSelect.name);
    }

    // --- Refresh button + auto-refresh ---
    refreshBtn.addEventListener('click', function () {
        refreshBtn.classList.add('spinning');
        setTimeout(function () { refreshBtn.classList.remove('spinning'); }, 600);
        loadApps(true);
    });

    function startAutoRefresh() {
        stopAutoRefresh();
        autoRefreshTimer = setInterval(function () { loadApps(true); }, 5000);
    }

    function stopAutoRefresh() {
        if (autoRefreshTimer) {
            clearInterval(autoRefreshTimer);
            autoRefreshTimer = null;
        }
    }

    // --- Audio level monitoring ---
    function startLevelMonitor() {
        if (monitorActive) return;
        if (appsList.length === 0) return;

        var bundleIds = appsList.map(function (a) { return a.bundle_id; });
        fetch('/api/meeting/audio-monitor/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bundle_ids: bundleIds }),
        }).then(function (r) { return r.json(); }).then(function (data) {
            if (data.ok) {
                monitorActive = true;
                pollLevels();
            }
        }).catch(function () {});
    }

    function stopLevelMonitor() {
        if (levelPollTimer) {
            clearTimeout(levelPollTimer);
            levelPollTimer = null;
        }
        if (monitorActive) {
            monitorActive = false;
            fetch('/api/meeting/audio-monitor/stop', { method: 'POST' }).catch(function () {});
        }
        // Reset all waveforms to idle
        var items = dropdownList.querySelectorAll('.app-dropdown-item');
        items.forEach(function (item) {
            var wf = item.querySelector('.app-waveform');
            if (wf) updateWaveform(wf, 0);
        });
    }

    function pollLevels() {
        if (!monitorActive) return;
        fetch('/api/meeting/audio-levels')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var levels = data.levels || {};
                var items = dropdownList.querySelectorAll('.app-dropdown-item');
                items.forEach(function (item) {
                    var bid = item.getAttribute('data-bundle-id');
                    var wf = item.querySelector('.app-waveform');
                    var info = levels[bid];
                    updateWaveform(wf, info ? info.level : 0);
                });
                // Also update trigger waveform for selected app
                var selectedBid = appPicker.value;
                if (selectedBid && levels[selectedBid]) {
                    var triggerWf = dropdownTrigger.querySelector('.app-waveform');
                    updateWaveform(triggerWf, levels[selectedBid].level);
                }
            })
            .catch(function () {})
            .then(function () {
                if (monitorActive) {
                    levelPollTimer = setTimeout(pollLevels, 200);
                }
            });
    }

    // --- Setup ---
    startBtn.addEventListener('click', function () {
        hideSetupError();
        loadApps();
        setupTitle.value = '';
        selectedMode = 'listen';
        modeToggleBtns.forEach(function (btn) {
            btn.classList.toggle('active', btn.getAttribute('data-mode') === 'listen');
        });
        modeDesc.textContent = 'Captures meeting audio only. All segments labeled "Others".';
        // Remove trigger waveform from previous session
        var oldWave = dropdownTrigger.querySelector('.app-waveform');
        if (oldWave) oldWave.remove();
        showView('setup');
    });

    modeToggleBtns.forEach(function (btn) {
        btn.addEventListener('click', function () {
            selectedMode = btn.getAttribute('data-mode');
            modeToggleBtns.forEach(function (b) {
                b.classList.toggle('active', b === btn);
            });
            modeDesc.textContent = selectedMode === 'listen'
                ? 'Captures meeting audio only. All segments labeled "Others".'
                : 'Captures meeting audio + your microphone. Segments labeled "You" and "Others".';
        });
    });

    setupCancel.addEventListener('click', function () {
        hideSetupError();
        setSetupLoading(false);
        stopLevelMonitor();
        toggleDropdown(false);
        showView('list');
    });

    setupGo.addEventListener('click', function () {
        if (startInFlight) return;

        var appId = appPicker.value;
        if (!appId) {
            showSetupError('Please select a meeting app to capture.');
            return;
        }

        hideSetupError();

        var title = setupTitle.value.trim();
        if (!title) {
            var now = new Date();
            title = 'Meeting \u2014 ' + now.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
                + ', ' + now.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
        }
        titleInput.value = title;
        clearChildren(transcriptEl);

        setSetupLoading(true);
        stopLevelMonitor();
        toggleDropdown(false);
        connectWS();

        // Wait for connection then send start, with timeout
        var attempts = 0;
        var maxAttempts = 50; // 5 seconds
        var waitForWS = setInterval(function () {
            attempts++;
            if (ws && ws.readyState === 1) {
                clearInterval(waitForWS);
                sendAction('start', {
                    title: title,
                    app_bundle_id: appId,
                    mode: selectedMode,
                });
            } else if (attempts >= maxAttempts) {
                clearInterval(waitForWS);
                setSetupLoading(false);
                showSetupError('Could not connect to the server. Please try again.');
            }
        }, 100);
    });

    // --- Recording controls ---
    pauseBtn.addEventListener('click', function () {
        if (isPaused) {
            sendAction('resume');
        } else {
            sendAction('pause');
        }
    });

    stopBtn.addEventListener('click', function () {
        sendAction('stop');
    });

    discardBtn.addEventListener('click', function () {
        if (confirm('Discard this meeting recording?')) {
            sendAction('discard');
        }
    });

    // --- Meeting list ---
    function loadMeetings() {
        var q = searchInput ? searchInput.value.trim() : '';
        var url = '/api/meeting/meetings' + (q ? '?q=' + encodeURIComponent(q) : '');
        fetch(url)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                renderMeetingList(data.meetings || []);
            })
            .catch(function () {
                renderMeetingList([]);
            });
    }

    function renderMeetingList(meetings) {
        clearChildren(meetingList);
        emptyState.style.display = meetings.length === 0 ? '' : 'none';

        meetings.forEach(function (m) {
            var card = document.createElement('div');
            card.className = 'lecture-card';
            card.onclick = function () { openReview(m.id); };

            var title = document.createElement('div');
            title.className = 'lecture-card-title';
            title.textContent = m.title || 'Untitled';

            var modeBadge = document.createElement('span');
            modeBadge.className = 'mode-badge';
            modeBadge.textContent = m.mode === 'full' ? 'Full' : 'Listen';
            title.appendChild(modeBadge);

            var meta = document.createElement('div');
            meta.className = 'lecture-card-meta';
            var dur = m.duration_seconds ? formatTime(m.duration_seconds) : '--:--';
            meta.textContent = formatDate(m.created_at) + ' \u00b7 ' + dur;
            if (m.app_name) {
                meta.textContent += ' \u00b7 ' + m.app_name;
            }

            var actions = document.createElement('div');
            actions.className = 'lecture-card-actions';

            var delBtn = document.createElement('button');
            delBtn.className = 'btn-icon';
            delBtn.title = 'Delete';
            delBtn.appendChild(createSvgIcon(['M3 6h18', 'M8 6V4h8v2', 'M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6']));
            delBtn.onclick = function (e) {
                e.stopPropagation();
                if (confirm('Delete this meeting?')) {
                    fetch('/api/meeting/meetings/' + m.id, { method: 'DELETE' })
                        .then(function () { loadMeetings(); });
                }
            };
            actions.appendChild(delBtn);

            card.appendChild(title);
            card.appendChild(meta);
            card.appendChild(actions);
            meetingList.appendChild(card);
        });
    }

    if (searchInput) {
        var searchDebounce;
        searchInput.addEventListener('input', function () {
            clearTimeout(searchDebounce);
            searchDebounce = setTimeout(loadMeetings, 300);
        });
    }

    // --- Review ---
    function openReview(meetingId) {
        currentMeetingId = meetingId;
        editMode = false;
        fetch('/api/meeting/meetings/' + meetingId)
            .then(function (r) {
                if (!r.ok) throw new Error('Meeting not found');
                return r.json();
            })
            .then(function (data) {
                var meeting = data.meeting;
                var segments = data.segments || [];

                reviewTitle.value = meeting.title || 'Untitled';
                var dur = meeting.duration_seconds ? formatTime(meeting.duration_seconds) : '--:--';
                reviewMeta.textContent = formatDate(meeting.created_at) + ' \u00b7 ' + dur;

                clearChildren(reviewTranscript);
                segments.forEach(function (seg) {
                    var div = document.createElement('div');
                    div.className = 'segment ' + (seg.speaker === 'you' ? 'segment-you' : 'segment-others');
                    div.setAttribute('data-index', seg.segment_index);

                    var label = document.createElement('span');
                    label.className = 'speaker-label ' + (seg.speaker === 'you' ? 'speaker-label-you' : 'speaker-label-others');
                    label.textContent = seg.speaker === 'you' ? 'You' : 'Others';
                    div.appendChild(label);

                    var text = document.createElement('span');
                    text.className = 'segment-text';
                    text.textContent = seg.text;
                    div.appendChild(text);

                    // Click to seek audio
                    if (meeting.system_audio_path) {
                        div.style.cursor = 'pointer';
                        div.onclick = function () {
                            if (editMode) return;
                            var audio = reviewPlayer;
                            if (!audio.src || audio.src.indexOf('/api/meeting/meetings/' + meetingId + '/audio') < 0) {
                                audio.src = '/api/meeting/meetings/' + meetingId + '/audio';
                            }
                            audio.currentTime = seg.start_ms / 1000;
                            audio.play();
                        };
                    }

                    reviewTranscript.appendChild(div);
                });

                // Audio player
                var playerBar = document.getElementById('mt-player-bar');
                if (meeting.system_audio_path) {
                    playerBar.style.display = '';
                    reviewPlayer.src = '/api/meeting/meetings/' + meetingId + '/audio';
                } else {
                    playerBar.style.display = 'none';
                }

                showView('review');
            })
            .catch(function () {
                showView('list');
                loadMeetings();
            });
    }

    // Review back button
    var reviewBackBtn = document.getElementById('mt-review-back-btn');
    if (reviewBackBtn) {
        reviewBackBtn.addEventListener('click', function () {
            showView('list');
            loadMeetings();
        });
    }

    // Review edit mode
    if (reviewEditBtn) {
        reviewEditBtn.addEventListener('click', function () {
            editMode = !editMode;
            reviewEditBtn.classList.toggle('active', editMode);
            var segments = reviewTranscript.querySelectorAll('.segment');
            segments.forEach(function (seg) {
                var textEl = seg.querySelector('.segment-text');
                if (!textEl) return;
                if (editMode) {
                    textEl.contentEditable = 'true';
                    textEl.classList.add('editable');
                } else {
                    textEl.contentEditable = 'false';
                    textEl.classList.remove('editable');
                    // Save
                    var idx = parseInt(seg.getAttribute('data-index'), 10);
                    var newText = textEl.textContent.trim();
                    if (newText && currentMeetingId) {
                        fetch('/api/meeting/meetings/' + currentMeetingId + '/segments/' + idx, {
                            method: 'PATCH',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ text: newText }),
                        });
                    }
                }
            });
        });
    }

    // Review menu
    var reviewMenuBtn = document.getElementById('mt-review-menu-btn');
    var reviewMenu = document.getElementById('mt-review-menu');
    if (reviewMenuBtn && reviewMenu) {
        reviewMenuBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            reviewMenu.style.display = reviewMenu.style.display === 'none' ? '' : 'none';
        });
        document.addEventListener('click', function () {
            reviewMenu.style.display = 'none';
        });
    }

    var exportBtn = document.getElementById('mt-menu-export');
    if (exportBtn) {
        exportBtn.addEventListener('click', function () {
            if (currentMeetingId) {
                fetch('/api/meeting/meetings/' + currentMeetingId + '/export', { method: 'POST' });
            }
            reviewMenu.style.display = 'none';
        });
    }

    var deleteBtn = document.getElementById('mt-menu-delete');
    if (deleteBtn) {
        deleteBtn.addEventListener('click', function () {
            if (currentMeetingId && confirm('Delete this meeting?')) {
                fetch('/api/meeting/meetings/' + currentMeetingId, { method: 'DELETE' })
                    .then(function () {
                        showView('list');
                        loadMeetings();
                    });
            }
            reviewMenu.style.display = 'none';
        });
    }

    // Audio player controls
    var playBtn = document.getElementById('mt-player-play-btn');
    var playIcon = document.getElementById('mt-player-play-icon');
    var pausePlayerIcon = document.getElementById('mt-player-pause-icon');
    var scrub = document.getElementById('mt-player-scrub');
    var currentTimeEl = document.getElementById('mt-player-current');
    var durationEl = document.getElementById('mt-player-duration');

    if (playBtn && reviewPlayer) {
        playBtn.addEventListener('click', function () {
            if (reviewPlayer.paused) {
                reviewPlayer.play();
            } else {
                reviewPlayer.pause();
            }
        });

        reviewPlayer.addEventListener('play', function () {
            playIcon.style.display = 'none';
            pausePlayerIcon.style.display = '';
        });
        reviewPlayer.addEventListener('pause', function () {
            playIcon.style.display = '';
            pausePlayerIcon.style.display = 'none';
        });
        reviewPlayer.addEventListener('loadedmetadata', function () {
            durationEl.textContent = formatTime(reviewPlayer.duration);
        });
        reviewPlayer.addEventListener('timeupdate', function () {
            currentTimeEl.textContent = formatTime(reviewPlayer.currentTime);
            if (reviewPlayer.duration) {
                scrub.value = Math.round((reviewPlayer.currentTime / reviewPlayer.duration) * 1000);
            }
        });
        scrub.addEventListener('input', function () {
            if (reviewPlayer.duration) {
                reviewPlayer.currentTime = (scrub.value / 1000) * reviewPlayer.duration;
            }
        });
    }

    // --- Global menu (label manager) ---
    var globalMenuBtn = document.getElementById('mt-global-menu-btn');
    var globalMenu = document.getElementById('mt-global-menu');
    if (globalMenuBtn && globalMenu) {
        globalMenuBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            globalMenu.style.display = globalMenu.style.display === 'none' ? '' : 'none';
        });
        document.addEventListener('click', function () {
            globalMenu.style.display = 'none';
        });
    }

    // --- Init ---
    function init() {
        if (!startBtn) return; // Meeting elements not present
        showView('list');
        loadMeetings();

        // Reload meetings when sidebar "Meeting" is clicked
        document.addEventListener('click', function (e) {
            var navItem = e.target.closest('.sidebar-item');
            if (navItem && navItem.dataset.mode === 'meeting') {
                if (currentView === 'list') {
                    loadMeetings();
                }
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
