// AI Consultant Platform - Frontend Application
// Handles WebRTC video streaming, WebSocket communication, and UI interactions

class AIConsultantApp {
    constructor() {
        this.sessionId = null;
        this.consultantMode = null;
        this.websocket = null;
        this.localStream = null;
        this.screenStream = null;
        this.frameInterval = null;
        this.sessionStartTime = null;
        this.timerInterval = null;
        this.frameCount = 0;
        this.insightCount = 0;
        this.availableModes = [];

        this.init();
    }

    async init() {
        await this.loadConsultantModes();
        this.setupEventListeners();
    }

    async loadConsultantModes() {
        try {
            const response = await fetch('/api/consultant-modes');
            const data = await response.json();
            this.availableModes = data.modes;
            this.renderConsultantModes();
        } catch (error) {
            this.showStatus('Failed to load consultant modes', 'error');
        }
    }

    renderConsultantModes() {
        const container = document.getElementById('consultantModes');
        container.innerHTML = this.availableModes.map(mode => `
            <div class="mode-card" data-mode="${mode.id}">
                <h3>${mode.name}</h3>
                <p>${mode.description}</p>
                <div class="mode-pricing">
                    <span class="price">${mode.pricing}</span>
                    <span class="comparable-rate">vs ${mode.comparable_rate}</span>
                </div>
            </div>
        `).join('');

        // Add click handlers
        document.querySelectorAll('.mode-card').forEach(card => {
            card.addEventListener('click', () => {
                document.querySelectorAll('.mode-card').forEach(c => c.classList.remove('selected'));
                card.classList.add('selected');
                this.consultantMode = card.dataset.mode;
            });
        });
    }

    setupEventListeners() {
        // Start session button
        document.getElementById('startSessionBtn').addEventListener('click', () => {
            this.startSession();
        });

        // End session button
        document.getElementById('endSessionBtn').addEventListener('click', () => {
            this.endSession();
        });

        // Video controls
        document.getElementById('toggleVideoBtn').addEventListener('click', () => {
            this.toggleVideo();
        });

        document.getElementById('toggleAudioBtn').addEventListener('click', () => {
            this.toggleAudio();
        });

        document.getElementById('shareScreenBtn').addEventListener('click', () => {
            this.shareScreen();
        });

        document.getElementById('endCallBtn').addEventListener('click', () => {
            this.endSession();
        });

        // Tab navigation
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                this.switchTab(btn.dataset.tab);
            });
        });

        // Quick ask
        document.getElementById('askBtn').addEventListener('click', () => {
            this.askQuestion();
        });

        document.getElementById('quickQuestion').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                this.askQuestion();
            }
        });

        // Export report
        document.getElementById('exportReportBtn').addEventListener('click', () => {
            this.exportReport();
        });
    }

    async startSession() {
        if (!this.consultantMode) {
            this.showStatus('Please select a consultant mode', 'error');
            return;
        }

        try {
            this.showLoading(true);

            // Create session
            const customInstructions = document.getElementById('customInstructions').value;
            const response = await fetch('/api/sessions/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: 'demo-user',
                    consultant_mode: this.consultantMode,
                    custom_instructions: customInstructions || null
                })
            });

            const data = await response.json();
            this.sessionId = data.session_id;

            // Connect WebSocket
            await this.connectWebSocket();

            // Request camera access
            await this.startCamera();

            // Switch to video session view
            document.getElementById('modeSelection').style.display = 'none';
            document.getElementById('videoSession').style.display = 'block';
            document.getElementById('endSessionBtn').style.display = 'block';

            // Update UI
            const modeName = this.availableModes.find(m => m.id === this.consultantMode)?.name || this.consultantMode;
            document.getElementById('currentMode').textContent = modeName;

            // Start timer
            this.sessionStartTime = Date.now();
            this.startTimer();

            // Start frame capture
            this.startFrameCapture();

            this.showStatus('Session started successfully!', 'success');
            this.showLoading(false);

        } catch (error) {
            console.error('Error starting session:', error);
            this.showStatus(`Failed to start session: ${error.message}`, 'error');
            this.showLoading(false);
        }
    }

    async connectWebSocket() {
        return new Promise((resolve, reject) => {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws/${this.sessionId}`;

            this.websocket = new WebSocket(wsUrl);

            this.websocket.onopen = () => {
                console.log('WebSocket connected');
                resolve();
            };

            this.websocket.onmessage = (event) => {
                this.handleWebSocketMessage(JSON.parse(event.data));
            };

            this.websocket.onerror = (error) => {
                console.error('WebSocket error:', error);
                reject(error);
            };

            this.websocket.onclose = () => {
                console.log('WebSocket disconnected');
            };
        });
    }

    handleWebSocketMessage(message) {
        switch (message.type) {
            case 'analysis':
                this.handleAnalysis(message.data);
                break;
            case 'instant-answer':
                this.handleInstantAnswer(message.answer);
                break;
            case 'chat':
                // Handle chat messages if needed
                break;
        }
    }

    async startCamera() {
        try {
            this.localStream = await navigator.mediaDevices.getUserMedia({
                video: { width: 1280, height: 720 },
                audio: true
            });

            const videoElement = document.getElementById('localVideo');
            videoElement.srcObject = this.localStream;

            document.getElementById('toggleVideoBtn').classList.add('active');
            document.getElementById('toggleAudioBtn').classList.add('active');

        } catch (error) {
            console.error('Error accessing camera:', error);
            this.showStatus('Failed to access camera/microphone', 'error');
            throw error;
        }
    }

    startFrameCapture() {
        // Capture and analyze frames at regular intervals
        this.frameInterval = setInterval(() => {
            this.captureAndAnalyzeFrame();
        }, 2000); // Capture every 2 seconds
    }

    async captureAndAnalyzeFrame() {
        if (!this.localStream) return;

        try {
            const videoElement = document.getElementById('localVideo');
            const canvas = document.createElement('canvas');
            canvas.width = videoElement.videoWidth;
            canvas.height = videoElement.videoHeight;

            const ctx = canvas.getContext('2d');
            ctx.drawImage(videoElement, 0, 0, canvas.width, canvas.height);

            const frameData = canvas.toDataURL('image/jpeg', 0.8);

            // Send frame via WebSocket
            this.websocket.send(JSON.stringify({
                type: 'frame',
                frame_data: frameData,
                screen_share_data: this.screenStream ? 'active' : null
            }));

            this.frameCount++;
            document.getElementById('frameCount').textContent = this.frameCount;

        } catch (error) {
            console.error('Error capturing frame:', error);
        }
    }

    handleAnalysis(analysis) {
        if (analysis.error) {
            this.showStatus('Analysis error: ' + analysis.error, 'error');
            return;
        }

        console.log('Analysis received:', analysis);
        this.showLoading(false);

        // Update insights
        if (analysis.insights && analysis.insights.length > 0) {
            this.addInsights(analysis.insights);
        }

        // Update recommendations
        if (analysis.recommendations && analysis.recommendations.length > 0) {
            this.addRecommendations(analysis.recommendations);
        }

        // Update risks
        if (analysis.risk_factors && analysis.risk_factors.length > 0) {
            this.addRisks(analysis.risk_factors);
        }

        // Update opportunities
        if (analysis.opportunities && analysis.opportunities.length > 0) {
            this.addOpportunities(analysis.opportunities);
        }

        // Update action items
        if (analysis.action_items && analysis.action_items.length > 0) {
            this.addActionItems(analysis.action_items);
        }

        this.insightCount++;
        document.getElementById('insightCount').textContent = this.insightCount;

        this.showStatus('New insights generated!', 'success');
    }

    addInsights(insights) {
        const container = document.getElementById('insightsList');
        if (container.querySelector('.empty-state')) {
            container.innerHTML = '';
        }

        insights.forEach(insight => {
            const item = document.createElement('div');
            item.className = 'insight-item';
            item.innerHTML = `
                <p>${insight}</p>
                <small style="color: var(--text-secondary); font-size: 12px;">
                    ${new Date().toLocaleTimeString()}
                </small>
            `;
            container.insertBefore(item, container.firstChild);
        });
    }

    addRecommendations(recommendations) {
        const container = document.getElementById('recommendationsList');
        if (container.querySelector('.empty-state')) {
            container.innerHTML = '';
        }

        recommendations.forEach(rec => {
            const item = document.createElement('div');
            item.className = 'recommendation-item';
            item.innerHTML = `
                <p>${rec}</p>
                <small style="color: var(--text-secondary); font-size: 12px;">
                    ${new Date().toLocaleTimeString()}
                </small>
            `;
            container.insertBefore(item, container.firstChild);
        });
    }

    addRisks(risks) {
        const container = document.getElementById('risksList');
        if (container.querySelector('.empty-state')) {
            container.innerHTML = '';
        }

        risks.forEach(risk => {
            const item = document.createElement('div');
            item.className = 'risk-item';
            item.innerHTML = `
                <p>⚠️ ${risk}</p>
                <small style="color: var(--text-secondary); font-size: 12px;">
                    ${new Date().toLocaleTimeString()}
                </small>
            `;
            container.insertBefore(item, container.firstChild);
        });
    }

    addOpportunities(opportunities) {
        const container = document.getElementById('opportunitiesList');
        if (container.querySelector('.empty-state')) {
            container.innerHTML = '';
        }

        opportunities.forEach(opp => {
            const item = document.createElement('div');
            item.className = 'opportunity-item';
            item.innerHTML = `
                <p>✨ ${opp}</p>
                <small style="color: var(--text-secondary); font-size: 12px;">
                    ${new Date().toLocaleTimeString()}
                </small>
            `;
            container.insertBefore(item, container.firstChild);
        });
    }

    addActionItems(actionItems) {
        const container = document.getElementById('actionItemsList');
        if (container.querySelector('.empty-state')) {
            container.innerHTML = '';
        }

        actionItems.forEach(item => {
            const actionEl = document.createElement('div');
            actionEl.className = `action-item ${item.priority}-priority`;
            actionEl.innerHTML = `
                <span>${item.description || item}</span>
                <span class="badge" style="background: var(--${item.priority === 'high' ? 'danger' : 'warning'}-color); padding: 4px 8px; border-radius: 4px; font-size: 12px;">
                    ${item.priority || 'medium'}
                </span>
            `;
            container.insertBefore(actionEl, container.firstChild);
        });
    }

    toggleVideo() {
        if (this.localStream) {
            const videoTrack = this.localStream.getVideoTracks()[0];
            videoTrack.enabled = !videoTrack.enabled;
            document.getElementById('toggleVideoBtn').classList.toggle('active');
        }
    }

    toggleAudio() {
        if (this.localStream) {
            const audioTrack = this.localStream.getAudioTracks()[0];
            audioTrack.enabled = !audioTrack.enabled;
            document.getElementById('toggleAudioBtn').classList.toggle('active');
        }
    }

    async shareScreen() {
        try {
            if (this.screenStream) {
                // Stop screen sharing
                this.screenStream.getTracks().forEach(track => track.stop());
                this.screenStream = null;
                this.showStatus('Screen sharing stopped', 'info');
            } else {
                // Start screen sharing
                this.screenStream = await navigator.mediaDevices.getDisplayMedia({
                    video: true
                });
                this.showStatus('Screen sharing started', 'success');

                // Handle screen share stop
                this.screenStream.getVideoTracks()[0].onended = () => {
                    this.screenStream = null;
                    this.showStatus('Screen sharing stopped', 'info');
                };
            }
        } catch (error) {
            console.error('Error sharing screen:', error);
            this.showStatus('Failed to share screen', 'error');
        }
    }

    async askQuestion() {
        const questionInput = document.getElementById('quickQuestion');
        const question = questionInput.value.trim();

        if (!question) return;

        try {
            this.showLoading(true);

            // Capture current frame
            const videoElement = document.getElementById('localVideo');
            const canvas = document.createElement('canvas');
            canvas.width = videoElement.videoWidth;
            canvas.height = videoElement.videoHeight;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(videoElement, 0, 0, canvas.width, canvas.height);
            const frameData = canvas.toDataURL('image/jpeg', 0.8);

            // Send question via WebSocket
            this.websocket.send(JSON.stringify({
                type: 'instant-question',
                question: question,
                frame_data: frameData,
                consultant_mode: this.consultantMode
            }));

            questionInput.value = '';

        } catch (error) {
            console.error('Error asking question:', error);
            this.showStatus('Failed to process question', 'error');
            this.showLoading(false);
        }
    }

    handleInstantAnswer(answer) {
        const answerDiv = document.getElementById('quickAnswer');
        answerDiv.innerHTML = `<strong>AI Consultant:</strong><br>${answer}`;
        answerDiv.style.display = 'block';
        this.showLoading(false);
    }

    async exportReport() {
        try {
            this.showLoading(true);

            const response = await fetch(`/api/sessions/${this.sessionId}/report`, {
                method: 'POST'
            });

            const data = await response.json();

            // Create downloadable report
            const blob = new Blob([data.report], { type: 'text/markdown' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `ai-consultant-report-${this.sessionId}.md`;
            a.click();

            this.showStatus('Report exported successfully!', 'success');
            this.showLoading(false);

        } catch (error) {
            console.error('Error exporting report:', error);
            this.showStatus('Failed to export report', 'error');
            this.showLoading(false);
        }
    }

    startTimer() {
        this.timerInterval = setInterval(() => {
            const elapsed = Date.now() - this.sessionStartTime;
            const hours = Math.floor(elapsed / 3600000);
            const minutes = Math.floor((elapsed % 3600000) / 60000);
            const seconds = Math.floor((elapsed % 60000) / 1000);

            document.getElementById('sessionTimer').textContent =
                `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
        }, 1000);
    }

    async endSession() {
        if (confirm('Are you sure you want to end this session?')) {
            try {
                // Stop all streams
                if (this.localStream) {
                    this.localStream.getTracks().forEach(track => track.stop());
                }
                if (this.screenStream) {
                    this.screenStream.getTracks().forEach(track => track.stop());
                }

                // Stop intervals
                clearInterval(this.frameInterval);
                clearInterval(this.timerInterval);

                // Close WebSocket
                if (this.websocket) {
                    this.websocket.close();
                }

                // End session on server
                await fetch(`/api/sessions/${this.sessionId}/end`, {
                    method: 'POST'
                });

                // Reset UI
                document.getElementById('videoSession').style.display = 'none';
                document.getElementById('modeSelection').style.display = 'block';
                document.getElementById('endSessionBtn').style.display = 'none';

                this.showStatus('Session ended', 'info');

            } catch (error) {
                console.error('Error ending session:', error);
            }
        }
    }

    switchTab(tabName) {
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.classList.remove('active');
        });
        document.querySelectorAll('.tab-content').forEach(content => {
            content.classList.remove('active');
        });

        document.querySelector(`[data-tab="${tabName}"]`).classList.add('active');
        document.getElementById(`${tabName}Tab`).classList.add('active');
    }

    showStatus(message, type = 'info') {
        const container = document.getElementById('statusMessages');
        const messageEl = document.createElement('div');
        messageEl.className = `status-message ${type}`;
        messageEl.textContent = message;

        container.appendChild(messageEl);

        setTimeout(() => {
            messageEl.remove();
        }, 5000);
    }

    showLoading(show) {
        document.getElementById('loadingIndicator').style.display = show ? 'block' : 'none';
    }
}

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    new AIConsultantApp();
});
