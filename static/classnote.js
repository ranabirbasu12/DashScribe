// classnote.js — ClassNote live lecture transcription UI
(function () {
    'use strict';

    // --- State ---
    var ws = null;
    var currentLectureId = null;
    var currentView = 'list'; // 'list' | 'recording' | 'review'
    var elapsedTimer = null;
    var elapsedSeconds = 0;
    var autoScrollEnabled = true;
    var reconnectTimer = null;
    var countdownTimer = null;
    var isPaused = false;
    var pendingStartTitle = null;

    // --- DOM refs ---
    var listView = document.getElementById('classnote-list');
    var recordingView = document.getElementById('classnote-recording');
    var reviewView = document.getElementById('classnote-review');
    var startBtn = document.getElementById('classnote-start-btn');
    var pauseBtn = document.getElementById('classnote-pause-btn');
    var stopBtn = document.getElementById('classnote-stop-btn');
    var discardBtn = document.getElementById('classnote-discard-btn');
    var titleInput = document.getElementById('classnote-title');
    var elapsedEl = document.getElementById('classnote-elapsed');
    var transcriptEl = document.getElementById('classnote-transcript');
    var jumpBtn = document.getElementById('classnote-jump-btn');
    var warningBanner = document.getElementById('classnote-warning-banner');
    var lectureList = document.getElementById('classnote-lectures');
    var emptyState = document.getElementById('classnote-empty');
    var searchInput = document.getElementById('classnote-search');
    var countdownOverlay = document.getElementById('classnote-countdown');
    var countdownNumber = document.getElementById('countdown-number');
    var countdownCancel = document.getElementById('countdown-cancel');
    var recordingDot = document.querySelector('.classnote-recording-dot');
    // Review
    var reviewTitle = document.getElementById('review-title');
    var reviewMeta = document.getElementById('review-meta');
    var reviewTranscript = document.getElementById('review-transcript');
    var reviewLabels = document.getElementById('review-labels');
    // reviewDeleteBtn removed — now in three-dot menu
    var reviewEditBtn = document.getElementById('review-edit-btn');
    var reviewPlayer = document.getElementById('review-player');
    var editMode = false;

    // --- Utility ---
    function clearChildren(el) {
        while (el.firstChild) el.removeChild(el.firstChild);
    }

    function formatElapsed(seconds) {
        var h = Math.floor(seconds / 3600);
        var m = Math.floor((seconds % 3600) / 60);
        var s = seconds % 60;
        var mm = m < 10 ? '0' + m : '' + m;
        var ss = s < 10 ? '0' + s : '' + s;
        return h > 0 ? h + ':' + mm + ':' + ss : mm + ':' + ss;
    }

    function formatDate(isoStr) {
        if (!isoStr) return '';
        var d = new Date(isoStr);
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) +
            ' at ' + d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    }

    function formatDurationMs(ms) {
        if (!ms || ms <= 0) return '0s';
        var totalSec = Math.round(ms / 1000);
        var m = Math.floor(totalSec / 60);
        var s = totalSec % 60;
        if (m > 0) return m + 'm ' + s + 's';
        return s + 's';
    }

    // --- View Switching ---
    function showView(view) {
        currentView = view;
        listView.style.display = view === 'list' ? '' : 'none';
        recordingView.style.display = view === 'recording' ? '' : 'none';
        reviewView.style.display = view === 'review' ? '' : 'none';
        countdownOverlay.style.display = 'none';
    }

    // --- WebSocket ---
    function connectWS() {
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            return;
        }
        var protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(protocol + '//' + location.host + '/ws/classnote');

        ws.onopen = function () {
            if (reconnectTimer) {
                clearTimeout(reconnectTimer);
                reconnectTimer = null;
            }
            // Send queued start action (recording begins during countdown)
            if (pendingStartTitle !== null) {
                sendAction('start', { title: pendingStartTitle });
                pendingStartTitle = null;
            }
        };

        ws.onmessage = function (event) {
            var msg = JSON.parse(event.data);
            handleMessage(msg);
        };

        ws.onclose = function () {
            ws = null;
            if (currentView === 'recording') {
                scheduleReconnect();
            }
        };

        ws.onerror = function () {
            // Will trigger onclose
        };
    }

    function scheduleReconnect() {
        if (reconnectTimer) return;
        reconnectTimer = setTimeout(function () {
            reconnectTimer = null;
            connectWS();
        }, 2000);
    }

    function sendAction(action, data) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        var msg = Object.assign({ action: action }, data || {});
        ws.send(JSON.stringify(msg));
    }

    function handleMessage(msg) {
        switch (msg.type) {
            case 'segment':
                addSegment(msg);
                break;
            case 'correction':
                applyCorrection(msg);
                break;
            case 'status':
                handleStatus(msg);
                break;
            case 'error':
                handleError(msg);
                break;
        }
    }

    // --- Recording UI ---
    function startCountdown() {
        connectWS();
        showView('recording');
        countdownOverlay.style.display = '';
        clearChildren(transcriptEl);
        elapsedSeconds = 0;
        elapsedEl.textContent = '00:00';
        isPaused = false;
        pauseBtn.textContent = 'Pause';
        autoScrollEnabled = true;
        jumpBtn.style.display = 'none';
        warningBanner.style.display = 'none';

        // Start recording immediately so the VAD warms up during countdown.
        // Queue the start action for when the WebSocket connects.
        pendingStartTitle = titleInput.value.trim();

        var count = 3;
        countdownNumber.textContent = count;

        countdownTimer = setInterval(function () {
            count--;
            if (count <= 0) {
                clearInterval(countdownTimer);
                countdownTimer = null;
                countdownOverlay.style.display = 'none';
            } else {
                countdownNumber.textContent = count;
            }
        }, 1000);
    }

    function cancelCountdown() {
        if (countdownTimer) {
            clearInterval(countdownTimer);
            countdownTimer = null;
        }
        // Recording was started during countdown — discard it
        sendAction('discard');
        showView('list');
    }

    function addSegment(data) {
        var el = document.createElement('div');
        el.className = 'segment ' + (data.ghost ? 'ghost' : 'solid');
        el.dataset.index = data.index;
        el.dataset.startMs = data.start_ms;
        el.dataset.endMs = data.end_ms;
        el.textContent = data.text;
        transcriptEl.appendChild(el);
        updateAutoScroll();
    }

    function applyCorrection(data) {
        // Find segments in range and replace with single solid block
        var segments = transcriptEl.querySelectorAll('.segment');
        var toReplace = [];
        for (var i = 0; i < segments.length; i++) {
            var idx = parseInt(segments[i].dataset.index, 10);
            if (idx >= data.start_index && idx <= data.end_index) {
                toReplace.push(segments[i]);
            }
        }
        if (toReplace.length === 0) return;

        var corrected = document.createElement('div');
        corrected.className = 'segment solid';
        corrected.dataset.index = data.start_index;
        corrected.dataset.startMs = data.start_ms;
        corrected.dataset.endMs = data.end_ms;
        corrected.dataset.correctionGroup = data.correction_group_id;
        corrected.textContent = data.text;

        toReplace[0].parentNode.insertBefore(corrected, toReplace[0]);
        for (var j = 0; j < toReplace.length; j++) {
            toReplace[j].remove();
        }
    }

    function handleStatus(msg) {
        if (msg.state === 'recording') {
            if (msg.lecture_id) currentLectureId = msg.lecture_id;
            showRecordingDot(true);
            startElapsedTimer();
        } else if (msg.state === 'paused') {
            isPaused = true;
            pauseBtn.textContent = 'Resume';
            stopElapsedTimer();
        } else if (msg.state === 'resumed') {
            isPaused = false;
            pauseBtn.textContent = 'Pause';
            startElapsedTimer();
        } else if (msg.state === 'stopped') {
            showRecordingDot(false);
            stopElapsedTimer();
            showView('list');
            loadLectures();
        } else if (msg.state === 'discarded') {
            showRecordingDot(false);
            stopElapsedTimer();
            showView('list');
        }
    }

    function handleError(msg) {
        if (currentView === 'recording') {
            warningBanner.textContent = msg.message || 'An error occurred';
            warningBanner.style.display = '';
            setTimeout(function () {
                warningBanner.style.display = 'none';
            }, 5000);
        }
    }

    function showRecordingDot(show) {
        if (recordingDot) {
            recordingDot.style.display = show ? '' : 'none';
        }
    }

    // --- Auto-scroll ---
    function updateAutoScroll() {
        if (!autoScrollEnabled) return;
        transcriptEl.scrollTop = transcriptEl.scrollHeight;
    }

    function setupScrollObserver() {
        transcriptEl.addEventListener('scroll', function () {
            var atBottom = transcriptEl.scrollHeight - transcriptEl.scrollTop - transcriptEl.clientHeight < 50;
            autoScrollEnabled = atBottom;
            jumpBtn.style.display = atBottom ? 'none' : '';
        });
    }

    // --- Elapsed Timer ---
    function startElapsedTimer() {
        if (elapsedTimer) return;
        elapsedTimer = setInterval(function () {
            elapsedSeconds++;
            elapsedEl.textContent = formatElapsed(elapsedSeconds);
        }, 1000);
    }

    function stopElapsedTimer() {
        if (elapsedTimer) {
            clearInterval(elapsedTimer);
            elapsedTimer = null;
        }
    }

    // --- Lecture List ---
    var searchDebounce = null;
    var activeLabelFilter = null; // label name or null
    var sortBy = 'date'; // 'date' | 'title' | 'duration'
    var allLectures = []; // cached for client-side filter/sort
    var labelFilterEl = document.getElementById('classnote-label-filter');

    function loadLectures(query) {
        var url = '/api/classnote/lectures?limit=100';
        if (query) url += '&q=' + encodeURIComponent(query);

        fetch(url)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                allLectures = data.lectures || [];
                renderLabelFilters(allLectures);
                applyFilterAndSort();
            })
            .catch(function () {
                allLectures = [];
                renderLectureList([]);
            });
    }

    function applyFilterAndSort() {
        var filtered = allLectures;
        // Label filter
        if (activeLabelFilter) {
            filtered = filtered.filter(function (lec) {
                return (lec.labels || []).some(function (l) { return l.name === activeLabelFilter; });
            });
        }
        // Sort
        filtered = filtered.slice().sort(function (a, b) {
            if (sortBy === 'title') return (a.title || '').localeCompare(b.title || '');
            if (sortBy === 'duration') return (b.duration_seconds || 0) - (a.duration_seconds || 0);
            return new Date(b.created_at) - new Date(a.created_at); // date desc
        });
        renderLectureList(filtered);
    }

    function renderLabelFilters(lectures) {
        if (!labelFilterEl) return;
        clearChildren(labelFilterEl);
        // Collect unique labels
        var labelMap = {};
        lectures.forEach(function (lec) {
            (lec.labels || []).forEach(function (l) { labelMap[l.name] = l; });
        });
        var names = Object.keys(labelMap);
        if (names.length === 0) { labelFilterEl.style.display = 'none'; return; }
        labelFilterEl.style.display = '';

        // "All" chip
        var allChip = document.createElement('span');
        allChip.className = 'label-chip' + (!activeLabelFilter ? ' active' : '');
        allChip.textContent = 'All';
        allChip.addEventListener('click', function () {
            activeLabelFilter = null;
            applyFilterAndSort();
            renderLabelFilters(allLectures);
        });
        labelFilterEl.appendChild(allChip);

        names.sort().forEach(function (name) {
            var label = labelMap[name];
            var chip = document.createElement('span');
            chip.className = 'label-chip' + (activeLabelFilter === name ? ' active' : '');
            if (label.color && activeLabelFilter !== name) {
                chip.style.background = 'color-mix(in srgb, ' + label.color + ' 20%, transparent)';
                chip.style.color = label.color;
            }
            chip.textContent = name;
            chip.addEventListener('click', function () {
                activeLabelFilter = activeLabelFilter === name ? null : name;
                applyFilterAndSort();
                renderLabelFilters(allLectures);
            });
            labelFilterEl.appendChild(chip);
        });
    }

    function renderLectureList(lectures) {
        clearChildren(lectureList);
        if (lectures.length === 0) {
            emptyState.style.display = '';
            return;
        }
        emptyState.style.display = 'none';

        lectures.forEach(function (lec) {
            var card = document.createElement('div');
            card.className = 'lecture-card';
            card.addEventListener('click', function () {
                openLecture(lec.id);
            });

            var titleDiv = document.createElement('div');
            titleDiv.className = 'title';
            titleDiv.textContent = lec.title || 'Untitled';

            var metaDiv = document.createElement('div');
            metaDiv.className = 'meta';
            var durationSec = lec.duration_seconds || 0;
            var duration = durationSec > 0 ? formatDurationMs(durationSec * 1000) : '';
            metaDiv.textContent = formatDate(lec.created_at) + (duration ? ' \u00b7 ' + duration : '');

            // Badges
            if (lec.status === 'recovered') {
                var badge = document.createElement('span');
                badge.className = 'badge badge-recovered';
                badge.textContent = 'recovered';
                metaDiv.appendChild(document.createTextNode(' '));
                metaDiv.appendChild(badge);
            }
            if (!lec.audio_path) {
                var expBadge = document.createElement('span');
                expBadge.className = 'badge badge-expired';
                expBadge.textContent = 'audio expired';
                metaDiv.appendChild(document.createTextNode(' '));
                metaDiv.appendChild(expBadge);
            }

            // Action buttons (download + delete)
            var actionsDiv = document.createElement('div');
            actionsDiv.className = 'lecture-card-actions';
            var dlBtn = document.createElement('button');
            dlBtn.className = 'btn-icon-sm';
            dlBtn.title = 'Download transcript';
            dlBtn.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
            dlBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                downloadTranscript(lec.id, lec.title || 'Untitled');
            });
            var delBtn = document.createElement('button');
            delBtn.className = 'btn-icon-sm danger';
            delBtn.title = 'Delete lecture';
            delBtn.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
            delBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                deleteLecture(lec.id);
            });
            actionsDiv.appendChild(dlBtn);
            actionsDiv.appendChild(delBtn);

            // Label chips on card
            var cardLabels = lec.labels || [];
            if (cardLabels.length > 0) {
                var chipsDiv = document.createElement('div');
                chipsDiv.className = 'label-chips';
                chipsDiv.style.marginTop = '4px';
                cardLabels.forEach(function (label) {
                    var chip = document.createElement('span');
                    chip.className = 'label-chip';
                    if (label.color) {
                        chip.style.background = 'color-mix(in srgb, ' + label.color + ' 20%, transparent)';
                        chip.style.color = label.color;
                    }
                    chip.textContent = label.name;
                    chipsDiv.appendChild(chip);
                });
                card.appendChild(titleDiv);
                card.appendChild(actionsDiv);
                card.appendChild(metaDiv);
                card.appendChild(chipsDiv);
            } else {
                card.appendChild(titleDiv);
                card.appendChild(actionsDiv);
                card.appendChild(metaDiv);
            }
            lectureList.appendChild(card);
        });
    }

    // --- Review ---
    function openLecture(id) {
        fetch('/api/classnote/lectures/' + id)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.error) return;
                currentLectureId = id;
                var lec = data.lecture;
                var segments = data.segments || [];
                var labels = data.labels || [];

                reviewTitle.value = lec.title || 'Untitled';
                var durSec = lec.duration_seconds || 0;
                reviewMeta.textContent = formatDate(lec.created_at) +
                    (durSec > 0 ? ' \u00b7 ' + formatDurationMs(durSec * 1000) : '');

                // Render segments with click-to-seek
                clearChildren(reviewTranscript);
                editMode = false;
                if (reviewEditBtn) reviewEditBtn.classList.remove('active');
                segments.forEach(function (seg) {
                    var el = document.createElement('div');
                    el.className = 'segment solid';
                    el.dataset.startMs = seg.start_ms;
                    el.dataset.endMs = seg.end_ms;
                    el.dataset.segmentIndex = seg.segment_index;
                    var segText = seg.corrected_text || seg.text;
                    el.textContent = segText;
                    el.dataset.originalText = segText;
                    el.addEventListener('click', function () {
                        seekToSegment(parseFloat(el.dataset.startMs));
                    });
                    el.addEventListener('blur', function () {
                        if (!editMode) return;
                        saveSegmentEdit(el);
                    });
                    reviewTranscript.appendChild(el);
                });

                // Render labels
                renderReviewLabels(labels);

                // Wire up audio player
                reviewPlayer.src = '/api/classnote/lectures/' + id + '/audio';
                reviewPlayer.currentTime = 0;
                if (playerBar) playerBar.style.display = '';
                if (playerScrub) playerScrub.value = 0;
                if (playerCurrent) playerCurrent.textContent = '0:00';
                if (playerDuration) playerDuration.textContent = '0:00';
                updatePlayIcons();

                showView('review');
            })
            .catch(function () {
                // Silently fail
            });
    }

    // --- Edit mode ---
    function toggleEditMode() {
        editMode = !editMode;
        if (reviewEditBtn) reviewEditBtn.classList.toggle('active', editMode);
        var segments = reviewTranscript.querySelectorAll('.segment');
        for (var i = 0; i < segments.length; i++) {
            segments[i].contentEditable = editMode ? 'true' : 'false';
            if (editMode) {
                segments[i].classList.add('editable');
            } else {
                segments[i].classList.remove('editable');
                // Save any pending edit on exit
                saveSegmentEdit(segments[i]);
            }
        }
    }

    function saveSegmentEdit(el) {
        if (!currentLectureId) return;
        var newText = el.textContent.trim();
        var idx = el.dataset.segmentIndex;
        if (!newText || idx === undefined) return;
        // Save original to compare
        if (el.dataset.originalText === undefined) return;
        if (newText === el.dataset.originalText) return;
        el.dataset.originalText = newText;
        fetch('/api/classnote/lectures/' + currentLectureId + '/segments/' + idx, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: newText }),
        });
    }

    if (reviewEditBtn) {
        reviewEditBtn.addEventListener('click', toggleEditMode);
    }

    // --- Audio playback ---
    // Custom player elements
    var playerBar = document.getElementById('review-player-bar');
    var playerPlayBtn = document.getElementById('player-play-btn');
    var playerPlayIcon = document.getElementById('player-play-icon');
    var playerPauseIcon = document.getElementById('player-pause-icon');
    var playerScrub = document.getElementById('player-scrub');
    var playerCurrent = document.getElementById('player-current');
    var playerDuration = document.getElementById('player-duration');

    function formatPlayerTime(sec) {
        if (!sec || isNaN(sec)) return '0:00';
        var m = Math.floor(sec / 60);
        var s = Math.floor(sec % 60);
        return m + ':' + (s < 10 ? '0' : '') + s;
    }

    function updatePlayIcons() {
        if (!reviewPlayer) return;
        var playing = !reviewPlayer.paused;
        playerPlayIcon.style.display = playing ? 'none' : '';
        playerPauseIcon.style.display = playing ? '' : 'none';
    }

    function seekToSegment(startMs) {
        if (!reviewPlayer || !reviewPlayer.src) return;
        reviewPlayer.currentTime = startMs / 1000;
        reviewPlayer.play();
        updatePlayIcons();
    }

    function highlightPlayingSegment() {
        if (!reviewPlayer) return;
        var currentMs = reviewPlayer.currentTime * 1000;
        if (playerScrub && !playerScrub._dragging) {
            playerScrub.value = reviewPlayer.duration ? Math.round((reviewPlayer.currentTime / reviewPlayer.duration) * 1000) : 0;
        }
        if (playerCurrent) playerCurrent.textContent = formatPlayerTime(reviewPlayer.currentTime);
        if (reviewPlayer.paused) return;
        var segments = reviewTranscript.querySelectorAll('.segment');
        for (var i = 0; i < segments.length; i++) {
            var start = parseFloat(segments[i].dataset.startMs);
            var end = parseFloat(segments[i].dataset.endMs);
            if (currentMs >= start && currentMs < end) {
                segments[i].classList.add('playing');
            } else {
                segments[i].classList.remove('playing');
            }
        }
    }

    function renderReviewLabels(labels) {
        clearChildren(reviewLabels);
        labels.forEach(function (label) {
            var chip = document.createElement('span');
            chip.className = 'label-chip';
            if (label.color) {
                chip.style.background = 'color-mix(in srgb, ' + label.color + ' 20%, transparent)';
                chip.style.color = label.color;
            }
            chip.textContent = label.name;
            reviewLabels.appendChild(chip);
        });
    }

    function refreshReviewLabels() {
        fetch('/api/classnote/lectures/' + currentLectureId)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.labels) renderReviewLabels(data.labels);
            });
    }

    // --- Three-dot menu ---
    var reviewMenuBtn = document.getElementById('review-menu-btn');
    var reviewMenuDropdown = document.getElementById('review-menu-dropdown');
    var menuAddLabels = document.getElementById('menu-add-labels');
    var menuDeleteLecture = document.getElementById('menu-delete-lecture');
    var labelModal = document.getElementById('label-modal');
    var labelModalClose = document.getElementById('label-modal-close');
    var labelModalCurrent = document.getElementById('label-modal-current');
    var labelModalAvailable = document.getElementById('label-modal-available');
    var labelModalInput = document.getElementById('label-modal-input');

    function toggleReviewMenu() {
        var shown = reviewMenuDropdown.style.display !== 'none';
        reviewMenuDropdown.style.display = shown ? 'none' : '';
        if (!shown) {
            setTimeout(function () {
                document.addEventListener('click', function closeMenu(e) {
                    if (!reviewMenuDropdown.contains(e.target) && e.target !== reviewMenuBtn) {
                        reviewMenuDropdown.style.display = 'none';
                        document.removeEventListener('click', closeMenu);
                    }
                });
            }, 10);
        }
    }

    function openLabelModal() {
        reviewMenuDropdown.style.display = 'none';
        labelModal.style.display = '';
        refreshLabelModal();
    }

    function closeLabelModal() {
        labelModal.style.display = 'none';
        refreshReviewLabels();
    }

    function refreshLabelModal() {
        // Fetch current lecture labels and all labels in parallel
        Promise.all([
            fetch('/api/classnote/lectures/' + currentLectureId).then(function (r) { return r.json(); }),
            fetch('/api/classnote/labels').then(function (r) { return r.json(); })
        ]).then(function (results) {
            var currentLabels = results[0].labels || [];
            var allLabels = results[1].labels || [];
            var currentIds = {};
            currentLabels.forEach(function (l) { currentIds[l.id] = true; });

            // Current labels (with remove button)
            clearChildren(labelModalCurrent);
            if (currentLabels.length === 0) {
                var empty = document.createElement('div');
                empty.style.cssText = 'font-size:13px;color:var(--text-secondary);padding:4px 0;';
                empty.textContent = 'No labels assigned';
                labelModalCurrent.appendChild(empty);
            }
            currentLabels.forEach(function (label) {
                var item = document.createElement('div');
                item.className = 'label-modal-item';
                var dot = document.createElement('span');
                dot.className = 'label-dot';
                dot.style.background = label.color;
                item.appendChild(dot);
                item.appendChild(document.createTextNode(label.name));
                var removeBtn = document.createElement('button');
                removeBtn.className = 'label-action remove';
                removeBtn.textContent = 'Remove';
                removeBtn.addEventListener('click', function () {
                    fetch('/api/classnote/lectures/' + currentLectureId + '/labels/' + label.id, { method: 'DELETE' })
                        .then(function () { refreshLabelModal(); });
                });
                item.appendChild(removeBtn);
                labelModalCurrent.appendChild(item);
            });

            // Available labels (with add button)
            clearChildren(labelModalAvailable);
            var available = allLabels.filter(function (l) { return !currentIds[l.id]; });
            if (available.length === 0) {
                var emptyAvail = document.createElement('div');
                emptyAvail.style.cssText = 'font-size:13px;color:var(--text-secondary);padding:4px 0;';
                emptyAvail.textContent = 'No more labels available';
                labelModalAvailable.appendChild(emptyAvail);
            }
            available.forEach(function (label) {
                var item = document.createElement('div');
                item.className = 'label-modal-item';
                var dot = document.createElement('span');
                dot.className = 'label-dot';
                dot.style.background = label.color;
                item.appendChild(dot);
                item.appendChild(document.createTextNode(label.name));
                var addBtn = document.createElement('button');
                addBtn.className = 'label-action add';
                addBtn.textContent = 'Add';
                addBtn.addEventListener('click', function () {
                    fetch('/api/classnote/lectures/' + currentLectureId + '/labels/' + label.id, { method: 'POST' })
                        .then(function () { refreshLabelModal(); });
                });
                item.appendChild(addBtn);
                labelModalAvailable.appendChild(item);
            });
        });
    }

    function downloadTranscript(id, title) {
        fetch('/api/classnote/lectures/' + id + '/export', { method: 'POST' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.error) alert(data.error);
            });
    }

    function deleteLecture(id) {
        if (!confirm('Delete this lecture? This cannot be undone.')) return;
        fetch('/api/classnote/lectures/' + id, { method: 'DELETE' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.ok) {
                    showView('list');
                    loadLectures();
                }
            });
    }

    // --- Global Label Manager ---
    var cnGlobalMenuBtn = document.getElementById('cn-global-menu-btn');
    var cnGlobalMenu = document.getElementById('cn-global-menu');
    var cnManageLabelsBtn = document.getElementById('cn-manage-labels-btn');
    var cnLabelManager = document.getElementById('cn-label-manager');
    var cnLabelManagerClose = document.getElementById('cn-label-manager-close');
    var cnLabelManagerList = document.getElementById('cn-label-manager-list');
    var cnLabelManagerInput = document.getElementById('cn-label-manager-input');
    var cnLabelManagerSubmit = document.getElementById('cn-label-manager-submit');

    function toggleGlobalMenu() {
        var shown = cnGlobalMenu.style.display !== 'none';
        cnGlobalMenu.style.display = shown ? 'none' : '';
        if (!shown) {
            setTimeout(function () {
                document.addEventListener('click', function closeGM(e) {
                    if (!cnGlobalMenu.contains(e.target) && e.target !== cnGlobalMenuBtn) {
                        cnGlobalMenu.style.display = 'none';
                        document.removeEventListener('click', closeGM);
                    }
                });
            }, 10);
        }
    }

    function openLabelManager() {
        cnGlobalMenu.style.display = 'none';
        cnLabelManager.style.display = '';
        refreshLabelManager();
    }

    function closeLabelManager() {
        cnLabelManager.style.display = 'none';
        loadLectures(searchInput ? searchInput.value.trim() : '');
    }

    function refreshLabelManager() {
        fetch('/api/classnote/labels')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var labels = data.labels || [];
                clearChildren(cnLabelManagerList);
                if (labels.length === 0) {
                    var empty = document.createElement('div');
                    empty.style.cssText = 'font-size:13px;color:var(--text-secondary);padding:8px 0;';
                    empty.textContent = 'No labels created yet';
                    cnLabelManagerList.appendChild(empty);
                    return;
                }
                labels.forEach(function (label) {
                    var item = document.createElement('div');
                    item.className = 'label-modal-item';
                    var dot = document.createElement('span');
                    dot.className = 'label-dot';
                    dot.style.background = label.color;
                    item.appendChild(dot);
                    item.appendChild(document.createTextNode(label.name));
                    var delBtn = document.createElement('button');
                    delBtn.className = 'label-action remove';
                    delBtn.textContent = 'Delete';
                    delBtn.addEventListener('click', function () {
                        if (!confirm('Delete "' + label.name + '" permanently? It will be removed from all lectures.')) return;
                        fetch('/api/classnote/labels/' + label.id, { method: 'DELETE' })
                            .then(function () { refreshLabelManager(); });
                    });
                    item.appendChild(delBtn);
                    cnLabelManagerList.appendChild(item);
                });
            });
    }

    function createGlobalLabel() {
        if (!cnLabelManagerInput || !cnLabelManagerInput.value.trim()) return;
        var name = cnLabelManagerInput.value.trim();
        var colors = ['#ef4444', '#22c55e', '#3b82f6', '#f97316', '#8b5cf6', '#ec4899', '#14b8a6'];
        var color = colors[Math.floor(Math.random() * colors.length)];
        fetch('/api/classnote/labels', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name, color: color })
        })
            .then(function () {
                cnLabelManagerInput.value = '';
                refreshLabelManager();
            });
    }

    // --- Init ---
    function init() {
        if (!startBtn) return; // ClassNote elements not present

        // View setup
        showView('list');
        loadLectures();

        // Start button -> countdown
        startBtn.addEventListener('click', function () {
            titleInput.value = '';
            startCountdown();
        });

        // Countdown cancel
        countdownCancel.addEventListener('click', cancelCountdown);

        // Pause/Resume
        pauseBtn.addEventListener('click', function () {
            if (isPaused) {
                sendAction('resume');
            } else {
                sendAction('pause');
            }
        });

        // Stop
        stopBtn.addEventListener('click', function () {
            sendAction('stop');
        });

        // Discard
        discardBtn.addEventListener('click', function () {
            if (!confirm('Discard this recording? Audio and transcript will be permanently deleted.')) return;
            sendAction('discard');
        });

        // Jump to latest
        jumpBtn.addEventListener('click', function () {
            autoScrollEnabled = true;
            transcriptEl.scrollTop = transcriptEl.scrollHeight;
            jumpBtn.style.display = 'none';
        });

        // Search
        searchInput.addEventListener('input', function () {
            if (searchDebounce) clearTimeout(searchDebounce);
            searchDebounce = setTimeout(function () {
                loadLectures(searchInput.value.trim());
            }, 300);
        });

        // Global label manager
        if (cnGlobalMenuBtn) cnGlobalMenuBtn.addEventListener('click', toggleGlobalMenu);
        if (cnManageLabelsBtn) cnManageLabelsBtn.addEventListener('click', openLabelManager);
        if (cnLabelManagerClose) cnLabelManagerClose.addEventListener('click', closeLabelManager);
        if (cnLabelManager) {
            cnLabelManager.addEventListener('click', function (e) {
                if (e.target === cnLabelManager) closeLabelManager();
            });
        }
        if (cnLabelManagerInput) {
            cnLabelManagerInput.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') createGlobalLabel();
            });
        }
        if (cnLabelManagerSubmit) cnLabelManagerSubmit.addEventListener('click', createGlobalLabel);

        // Sort
        var sortSelect = document.getElementById('classnote-sort');
        if (sortSelect) {
            sortSelect.addEventListener('change', function () {
                sortBy = sortSelect.value;
                applyFilterAndSort();
            });
        }

        // Review: back button
        var reviewBackBtn = document.getElementById('review-back-btn');
        if (reviewBackBtn) {
            reviewBackBtn.addEventListener('click', function () {
                if (reviewPlayer) reviewPlayer.pause();
                if (playerBar) playerBar.style.display = 'none';
                showView('list');
                loadLectures();
            });
        }

        // Review: three-dot menu
        if (reviewMenuBtn) {
            reviewMenuBtn.addEventListener('click', toggleReviewMenu);
        }
        if (menuAddLabels) {
            menuAddLabels.addEventListener('click', openLabelModal);
        }
        var menuRetranscribe = document.getElementById('menu-retranscribe');
        if (menuRetranscribe) {
            menuRetranscribe.addEventListener('click', function () {
                reviewMenuDropdown.style.display = 'none';
                if (!currentLectureId) return;
                if (!confirm('Re-transcribe this lecture? This will replace the existing transcript.')) return;
                menuRetranscribe.textContent = 'Re-transcribing...';
                menuRetranscribe.disabled = true;
                fetch('/api/classnote/lectures/' + currentLectureId + '/retranscribe', { method: 'POST' })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        menuRetranscribe.textContent = 'Re-transcribe';
                        menuRetranscribe.disabled = false;
                        if (data.ok) {
                            openLecture(currentLectureId); // reload review
                        } else {
                            alert(data.error || 'Re-transcription failed');
                        }
                    })
                    .catch(function () {
                        menuRetranscribe.textContent = 'Re-transcribe';
                        menuRetranscribe.disabled = false;
                        alert('Re-transcription failed');
                    });
            });
        }
        if (menuDeleteLecture) {
            menuDeleteLecture.addEventListener('click', function () {
                reviewMenuDropdown.style.display = 'none';
                if (currentLectureId) deleteLecture(currentLectureId);
            });
        }
        if (labelModalClose) {
            labelModalClose.addEventListener('click', closeLabelModal);
        }
        if (labelModal) {
            labelModal.addEventListener('click', function (e) {
                if (e.target === labelModal) closeLabelModal(); // click outside modal
            });
        }
        function createAndAssignLabel() {
            if (!labelModalInput || !labelModalInput.value.trim()) return;
            var name = labelModalInput.value.trim();
            var colors = ['#ef4444', '#22c55e', '#3b82f6', '#f97316', '#8b5cf6', '#ec4899', '#14b8a6'];
            var color = colors[Math.floor(Math.random() * colors.length)];
            fetch('/api/classnote/labels', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name, color: color })
            })
                .then(function (r) { return r.json(); })
                .then(function (newLabel) {
                    return fetch('/api/classnote/lectures/' + currentLectureId + '/labels/' + newLabel.id, { method: 'POST' });
                })
                .then(function () {
                    labelModalInput.value = '';
                    refreshLabelModal();
                });
        }

        if (labelModalInput) {
            labelModalInput.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') createAndAssignLabel();
            });
        }
        var labelModalSubmit = document.getElementById('label-modal-submit');
        if (labelModalSubmit) {
            labelModalSubmit.addEventListener('click', createAndAssignLabel);
        }

        // Scroll observer for auto-scroll
        setupScrollObserver();

        // Custom audio player controls
        if (reviewPlayer) {
            reviewPlayer.addEventListener('timeupdate', highlightPlayingSegment);
            reviewPlayer.addEventListener('loadedmetadata', function () {
                if (playerDuration) playerDuration.textContent = formatPlayerTime(reviewPlayer.duration);
            });
            reviewPlayer.addEventListener('ended', function () {
                updatePlayIcons();
                var segs = reviewTranscript.querySelectorAll('.segment.playing');
                for (var i = 0; i < segs.length; i++) segs[i].classList.remove('playing');
            });
            reviewPlayer.addEventListener('pause', function () {
                updatePlayIcons();
                var segs = reviewTranscript.querySelectorAll('.segment.playing');
                for (var i = 0; i < segs.length; i++) segs[i].classList.remove('playing');
            });
            reviewPlayer.addEventListener('play', updatePlayIcons);
        }
        if (playerPlayBtn) {
            playerPlayBtn.addEventListener('click', function () {
                if (!reviewPlayer) return;
                if (reviewPlayer.paused) { reviewPlayer.play(); } else { reviewPlayer.pause(); }
                updatePlayIcons();
            });
        }
        if (playerScrub) {
            playerScrub.addEventListener('mousedown', function () { playerScrub._dragging = true; });
            playerScrub.addEventListener('input', function () {
                if (reviewPlayer && reviewPlayer.duration) {
                    reviewPlayer.currentTime = (playerScrub.value / 1000) * reviewPlayer.duration;
                }
            });
            playerScrub.addEventListener('mouseup', function () { playerScrub._dragging = false; });
            playerScrub.addEventListener('change', function () { playerScrub._dragging = false; });
        }

        // Listen for tab switches — when ClassNote tab activates, refresh list
        document.addEventListener('click', function (e) {
            var navItem = e.target.closest('.sidebar-item');
            if (navItem && navItem.dataset.mode === 'classnote') {
                if (currentView === 'list') {
                    loadLectures();
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
