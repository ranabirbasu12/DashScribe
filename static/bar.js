// static/bar.js
(function () {
    const bar = document.getElementById('bar');
    const tooltip = document.getElementById('tooltip');
    const cancelBtn = document.getElementById('cancel-btn');
    const stopBtn = document.getElementById('stop-btn');
    const canvas = document.getElementById('waveform');
    const ctx = canvas.getContext('2d');
    const barIdle = document.querySelector('.bar-idle');
    const retryBtn = document.getElementById('retry-btn');
    const processingCancelBtn = document.getElementById('processing-cancel-btn');
    const warningEl = document.getElementById('bar-warning');

    let ws = null;
    let currentState = 'idle';
    let amplitudes = [];
    const NUM_BARS = 22;
    let animFrameId = null;
    let warningTimeout = null;
    let reconnectTimer = null;

    // Initialize amplitude array
    for (let i = 0; i < NUM_BARS; i++) amplitudes.push(0);

    function setState(state) {
        currentState = state;
        bar.className = 'bar ' + state;
        if (state !== 'idle') {
            tooltip.classList.add('hidden');
        }
        // Hide warning when returning to idle (timeout/cancel dismissed)
        if (state === 'idle') {
            warningEl.classList.add('hidden');
            if (warningTimeout) {
                clearTimeout(warningTimeout);
                warningTimeout = null;
            }
        }
        if (state === 'recording') {
            startWaveformAnimation();
        } else {
            stopWaveformAnimation();
        }
    }

    function drawWaveform() {
        const w = canvas.width;
        const h = canvas.height;
        ctx.clearRect(0, 0, w, h);

        const barWidth = w / NUM_BARS * 0.6;
        const gap = w / NUM_BARS * 0.4;
        const centerY = h / 2;

        for (let i = 0; i < NUM_BARS; i++) {
            // Scale RMS (typically 0.01-0.15) to visual range 0-1
            const amp = Math.min((amplitudes[i] || 0) * 8, 1);
            const barHeight = Math.max(2, amp * h * 0.9);
            const x = i * (barWidth + gap) + gap / 2;
            const y = centerY - barHeight / 2;

            ctx.fillStyle = 'rgba(255, 255, 255, 0.95)';
            ctx.beginPath();
            ctx.roundRect(x, y, barWidth, barHeight, 2);
            ctx.fill();
        }

        animFrameId = requestAnimationFrame(drawWaveform);
    }

    function startWaveformAnimation() {
        if (animFrameId) return;
        drawWaveform();
    }

    function stopWaveformAnimation() {
        if (animFrameId) {
            cancelAnimationFrame(animFrameId);
            animFrameId = null;
        }
    }

    function pushAmplitude(value) {
        amplitudes.shift();
        amplitudes.push(value);
    }

    function showWarning(message) {
        warningEl.textContent = message;
        warningEl.classList.remove('hidden');
        if (warningTimeout) clearTimeout(warningTimeout);
        warningTimeout = setTimeout(() => {
            warningEl.classList.add('hidden');
            warningTimeout = null;
        }, 5000);
    }

    // --- Device change toast ---
    const toastEl = document.getElementById('bar-toast');
    const toastTextEl = document.getElementById('bar-toast-text');
    let toastTimer = null;

    function showToast(message) {
        if (!toastEl || !toastTextEl) return;
        toastTextEl.textContent = message;
        toastEl.classList.remove('hidden');
        if (toastTimer) {
            clearTimeout(toastTimer);
        }
        toastTimer = setTimeout(() => {
            hideToast();
        }, 4000);
    }

    function hideToast() {
        if (!toastEl) return;
        toastEl.classList.add('hidden');
        if (toastTimer) {
            clearTimeout(toastTimer);
            toastTimer = null;
        }
    }

    function connect() {
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            return;
        }
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(`${protocol}//${location.host}/ws/bar`);

        ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);
            if (msg.type === 'state') {
                setState(msg.state);
            } else if (msg.type === 'amplitude') {
                pushAmplitude(msg.value);
            } else if (msg.type === 'warning') {
                showWarning(msg.message);
            } else if (msg.type === 'hotkey') {
                tooltip.textContent = 'Hold ' + msg.display + ' to dictate';
            } else if (msg.type === 'device_changed') {
                showToast(`Input switched to ${msg.device}`);
            } else if (msg.type === 'device_lost') {
                showToast('No microphone found — reconnect to continue');
            } else if (msg.type === 'device_restored') {
                showToast(`${msg.device} connected`);
            }
        };

        ws.onclose = () => {
            ws = null;
            if (reconnectTimer) return;
            reconnectTimer = setTimeout(() => {
                reconnectTimer = null;
                connect();
            }, 1000);
        };
    }

    function cleanup() {
        if (reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
        if (warningTimeout) {
            clearTimeout(warningTimeout);
            warningTimeout = null;
        }
        if (toastTimer) {
            clearTimeout(toastTimer);
            toastTimer = null;
        }
        stopWaveformAnimation();
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            ws.onclose = null;
            ws.close();
        }
        ws = null;
    }

    // Click idle bar to start recording
    barIdle.addEventListener('click', () => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action: 'start' }));
        }
    });

    // Stop button
    stopBtn.addEventListener('click', () => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action: 'stop' }));
        }
    });

    // Cancel button
    cancelBtn.addEventListener('click', () => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action: 'cancel' }));
        }
    });

    // Retry button (in error state)
    retryBtn.addEventListener('click', () => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action: 'retry' }));
        }
    });

    // Cancel button (in processing state)
    processingCancelBtn.addEventListener('click', () => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action: 'cancel' }));
        }
    });

    // Tooltip on hover (idle state only)
    barIdle.addEventListener('mouseenter', () => {
        if (currentState === 'idle') tooltip.classList.remove('hidden');
    });
    barIdle.addEventListener('mouseleave', () => {
        tooltip.classList.add('hidden');
    });

    window.addEventListener('beforeunload', cleanup);
    connect();
})();
