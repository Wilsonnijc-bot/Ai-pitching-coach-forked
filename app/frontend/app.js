// Main application logic and routing

import { VideoRecorder, formatTime } from './recorder.js';
import { DeckUploader } from './deckUpload.js';
import { createJob, getJob, startRound1Feedback, startRound2Feedback } from './api.js';

const MAX_RECORD_SECONDS = 5 * 60;
const MIN_RECORD_SECONDS = 2;
const TRANSCRIPTION_POLL_INTERVAL_MS = 1500;
const SUMMARY_POLL_INTERVAL_MS = 1500;
const TRANSCRIPTION_TIMEOUT_MS = 3 * 60 * 1000;
const SUMMARY_TIMEOUT_MS = 3 * 60 * 1000;
const STAGES = new Set(['idle', 'recording', 'uploading', 'transcribing', 'feedbacking', 'done', 'error']);

class App {
    constructor() {
        this.currentRoute = 'home';
        this.videoRecorder = null;
        this.deckUploader = null;
        this.transcriptionData = null;
        this.currentJobId = null;
        this.currentJobData = null;
        this.isRecording = false;
        this.isStopping = false;
        this.isBusy = false;
        this.stage = 'idle';
        this.round1RequestedForJobId = null;
        this.round2RequestedForJobId = null;

        this.init();
    }

    init() {
        this.setupRouter();
        this.setupSidebar();
        this.handleInitialRoute();
    }

    setupRouter() {
        window.addEventListener('hashchange', () => {
            this.handleRoute();
        });

        document.querySelectorAll('.nav-item').forEach(item => {
            item.addEventListener('click', (e) => {
                e.preventDefault();
                const route = item.dataset.route;
                window.location.hash = route;
            });
        });
    }

    setupSidebar() {
        const mobileToggle = document.getElementById('mobile-menu-toggle');
        const sidebar = document.getElementById('sidebar');
        const overlay = document.getElementById('overlay');

        mobileToggle.addEventListener('click', () => {
            sidebar.classList.toggle('active');
            overlay.classList.toggle('active');
        });

        overlay.addEventListener('click', () => {
            sidebar.classList.remove('active');
            overlay.classList.remove('active');
        });

        document.querySelectorAll('.nav-item').forEach(item => {
            item.addEventListener('click', () => {
                if (window.innerWidth <= 768) {
                    sidebar.classList.remove('active');
                    overlay.classList.remove('active');
                }
            });
        });
    }

    handleInitialRoute() {
        const hash = window.location.hash.slice(1) || 'home';
        window.location.hash = hash;
    }

    handleRoute() {
        const route = window.location.hash.slice(1) || 'home';
        this.currentRoute = route;

        document.querySelectorAll('.nav-item').forEach(item => {
            item.classList.remove('active');
            if (item.dataset.route === route) {
                item.classList.add('active');
            }
        });

        switch (route) {
            case 'home':
                this.renderHome();
                break;
            case 'studio':
                this.renderStudio();
                break;
            case 'history':
                this.renderHistory();
                break;
            case 'settings':
                this.renderSettings();
                break;
            default:
                this.renderHome();
        }
    }

    renderHome() {
        const mainContent = document.getElementById('main-content');
        mainContent.innerHTML = `
            <div class="page-container">
                <div class="hero-section">
                    <h1 class="hero-title">AI Pitching Coach</h1>
                    <p class="hero-subtitle">Let your idea shine.</p>
                    <p class="hero-description">
                        Perfect your pitch with AI-powered feedback. Upload your deck,
                        record your presentation, and get instant transcription and analysis.
                    </p>
                    <a href="#studio" class="btn btn-primary">
                        Start polishing your pitch
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M5 12h14M12 5l7 7-7 7"></path>
                        </svg>
                    </a>
                </div>
            </div>
        `;
    }

    renderStudio() {
        const mainContent = document.getElementById('main-content');
        mainContent.innerHTML = `
            <div class="page-container">
                <div class="studio-header">
                    <h1 class="studio-title">Studio</h1>
                </div>

                <div class="studio-grid" id="input-blocks">
                    <div class="card" id="deck-upload-card">
                        <h2 class="card-title">Upload your pitch deck</h2>

                        <div class="upload-area">
                            <img src="https://mgx-backend-cdn.metadl.com/generate/images/960660/2026-02-11/e9275271-5cc3-4538-9599-50f4e1bc9b2f.png"
                                 alt="Upload" class="upload-illustration">
                            <p class="upload-text">Drag and drop your deck here, or click to browse</p>
                            <p class="upload-hint">Supports .pdf, .pptx (max 25MB)</p>
                            <input type="file" class="file-input" accept=".pdf,.pptx">
                        </div>

                        <div class="file-info">
                            <p class="file-name"></p>
                            <p class="file-size"></p>
                            <div class="file-actions">
                                <button class="btn btn-secondary btn-remove">Remove</button>
                                <button class="btn btn-primary btn-upload">Upload</button>
                            </div>
                        </div>

                        <div class="status-message"></div>
                    </div>

                    <div class="card" id="recording-card">
                        <h2 class="card-title">Record your pitch</h2>

                        <div class="recording-area">
                            <video id="camera-preview" autoplay muted playsinline></video>

                            <button class="record-button" id="record-btn">
                                <div class="record-icon"></div>
                                <span id="record-text">Start recording</span>
                            </button>

                            <div class="timer" id="timer">00:00</div>
                        </div>

                        <p class="job-meta" id="job-meta">No active job.</p>
                        <div class="status-message" id="recording-status"></div>
                        <div class="status-actions" id="status-actions"></div>
                    </div>
                </div>

                <div class="card results-panel" id="results-panel">
                    <div class="results-header">
                        <h2 class="results-title">Transcription Results</h2>
                        <div class="results-actions">
                            <button class="btn btn-secondary" id="copy-transcript-btn">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                                </svg>
                                Copy transcript
                            </button>
                            <button class="btn btn-secondary btn-small" id="new-practice-btn">Start new practice</button>
                        </div>
                    </div>

                    <div class="tabs">
                        <button class="tab active" data-tab="transcript">Transcript</button>
                        <button class="tab" data-tab="segments">Segments</button>
                        <button class="tab" data-tab="words">Words</button>
                    </div>

                    <div class="tab-content active" id="transcript-tab">
                        <div class="transcript-text" id="transcript-text"></div>
                    </div>

                    <div class="tab-content" id="segments-tab">
                        <table class="data-table">
                            <thead>
                                <tr>
                                    <th>Start</th>
                                    <th>End</th>
                                    <th>Text</th>
                                </tr>
                            </thead>
                            <tbody id="segments-tbody"></tbody>
                        </table>
                    </div>

                    <div class="tab-content" id="words-tab">
                        <div class="words-container">
                            <table class="data-table">
                                <thead>
                                    <tr>
                                        <th>Start</th>
                                        <th>End</th>
                                        <th>Word</th>
                                    </tr>
                                </thead>
                                <tbody id="words-tbody"></tbody>
                            </table>
                        </div>
                    </div>

                    <div class="summary-section">
                        <div class="summary-head">
                            <h3 class="summary-title">Professional Feedback</h3>
                        </div>
                        <div class="status-message" id="summary-status"></div>
                        <div class="status-actions" id="summary-actions"></div>
                        <div class="summary-card" id="summary-card">
                            <p class="summary-empty">Professional feedback will appear automatically once transcript is ready.</p>
                        </div>
                    </div>
                </div>
            </div>

            <div class="toast-area" id="toast-area"></div>
        `;

        this.deckUploader = new DeckUploader('deck-upload-card');
        this.setupRecording();
        this.setupTabs();
        this.setupStudioActions();
        this.updateJobMeta(this.currentJobData);
        if (this.transcriptionData) {
            this.displayTranscription(this.transcriptionData);
            this.renderSummary(this.getFeedbackFromJob(this.currentJobData));
        } else {
            this.renderSummary(null);
        }
        this.applyStudioLayout();
    }

    setupStudioActions() {
        const newPracticeBtn = document.getElementById('new-practice-btn');
        if (newPracticeBtn) {
            newPracticeBtn.addEventListener('click', () => {
                this.resetStudioState();
            });
        }
    }

    setupRecording() {
        const recordBtn = document.getElementById('record-btn');

        recordBtn.addEventListener('click', async () => {
            if (this.isBusy || ['uploading', 'transcribing', 'feedbacking'].includes(this.stage)) {
                return;
            }

            if (!this.isRecording) {
                await this.startRecording();
            } else {
                await this.stopRecordingAndUpload({ fromMaxDuration: false });
            }
        });
    }

    async startRecording() {
        const timer = document.getElementById('timer');

        try {
            this.clearStatusActions();
            this.setStage('recording');
            this.setRecordingStatus('Requesting camera & microphone permission...', 'info', true);

            if (!this.videoRecorder) {
                this.videoRecorder = new VideoRecorder();
                await this.videoRecorder.initialize();
            }

            // Attach live camera stream to the preview element
            const preview = document.getElementById('camera-preview');
            if (preview && this.videoRecorder.getStream()) {
                preview.srcObject = this.videoRecorder.getStream();
            }

            this.isRecording = true;
            this.isStopping = false;
            this.setRecordButtonState('recording', false);
            timer.textContent = '00:00';

            this.videoRecorder.startRecording(
                (seconds) => {
                    timer.textContent = formatTime(Math.min(seconds, MAX_RECORD_SECONDS));
                },
                async () => {
                    if (!this.isRecording || this.isStopping) {
                        return;
                    }
                    timer.textContent = '05:00';
                    this.showToast('Reached 5:00 limit. Recording stopped.', 'info');
                    await this.stopRecordingAndUpload({ fromMaxDuration: true });
                }
            );

            this.setRecordingStatus('Recording... Click stop at any time (max 5:00).', 'info', false);
        } catch (error) {
            console.error('Recording error:', error);
            this.isRecording = false;
            this.setStage('error');
            this.setRecordButtonState('idle', false);
            this.setRecordingStatus(
                'Camera/microphone access denied. Please allow camera & microphone access in your browser settings and try again.',
                'error',
                false
            );
        }
    }

    async stopRecordingAndUpload({ fromMaxDuration }) {
        const timer = document.getElementById('timer');

        if (!this.isRecording || this.isStopping) {
            return;
        }

        this.isStopping = true;
        this.setRecordButtonState('busy', true);
        this.setRecordingStatus('Stopping recording...', 'info', true);

        try {
            const videoBlob = await this.videoRecorder.stopRecording();
            const elapsedSeconds = this.videoRecorder.getElapsedSeconds();

            // Detach camera preview after stopping
            const preview = document.getElementById('camera-preview');
            if (preview) { preview.srcObject = null; }

            this.isRecording = false;
            this.isStopping = false;
            this.setStage('idle');
            this.setRecordButtonState('idle', false);
            timer.textContent = '00:00';

            if (elapsedSeconds < MIN_RECORD_SECONDS) {
                this.setRecordingStatus('Recording too short. Please record at least 2 seconds.', 'error', false);
                return;
            }

            if (!videoBlob || videoBlob.size === 0) {
                this.setRecordingStatus('Recording failed. Captured video is empty.', 'error', false);
                return;
            }

            if (fromMaxDuration) {
                this.setRecordingStatus('Reached 5:00 limit. Uploading recording...', 'info', true);
            }

            await this.handleTranscription(videoBlob);
        } catch (error) {
            console.error('Stop recording error:', error);
            this.isRecording = false;
            this.isStopping = false;
            this.setStage('error');
            this.setRecordButtonState('idle', false);
            this.setRecordingStatus(`Error stopping recording: ${error.message}`, 'error', false);
        }
    }

    async handleTranscription(videoBlob) {
        const selectedDeck = this.deckUploader ? this.deckUploader.currentFile : null;

        try {
            this.isBusy = true;
            this.clearStatusActions();
            this.clearSummaryActions();
            this.setStage('uploading');
            this.setRecordButtonState('busy', true);
            this.renderSummary(null);
            this.setSummaryStatus('', 'info', false);
            this.round1RequestedForJobId = null;
            this.round2RequestedForJobId = null;

            this.setRecordingStatus(
                selectedDeck ? 'Uploading video + deck...' : 'Uploading video...',
                'info',
                true
            );

            const created = await createJob(videoBlob, selectedDeck);
            if (!created.job_id) {
                throw new Error('Backend did not return a job_id.');
            }

            this.currentJobId = created.job_id;
            this.currentJobData = null;
            this.transcriptionData = null;
            this.updateJobMeta({
                job_id: created.job_id,
                status: created.status || 'queued',
                progress: 0,
            });
            this.setStage('transcribing');

            const finishedJob = await this.pollJob({
                jobId: created.job_id,
                timeoutMs: TRANSCRIPTION_TIMEOUT_MS,
                intervalMs: TRANSCRIPTION_POLL_INTERVAL_MS,
                phaseName: 'Transcription',
                isComplete: (job) => job.status === 'done' && !!this.getTranscriptFromJob(job),
                onTick: (job) => {
                    this.currentJobData = job;
                    this.updateJobMeta(job);
                    this.setRecordingStatus(
                        `Transcribing... ${this.progressLabel(job.progress)}`,
                        'info',
                        true
                    );
                },
            });

            await this.onTranscriptionReady(finishedJob);
        } catch (error) {
            console.error('Transcription flow error:', error);
            this.setStage('error');
            this.setRecordingStatus(`Transcription failed: ${error.message}`, 'error', false);

            if (this.currentJobId) {
                this.showStatusActions([
                    {
                        label: 'Retry polling',
                        kind: 'primary',
                        onClick: () => this.retryTranscriptionPolling(),
                    },
                    {
                        label: 'Start new recording',
                        kind: 'secondary',
                        onClick: () => this.resetStudioState(),
                    },
                ]);
            }
        } finally {
            this.isBusy = false;
            this.setRecordButtonState('idle', false);
        }
    }

    async retryTranscriptionPolling() {
        if (!this.currentJobId) {
            return;
        }

        try {
            this.isBusy = true;
            this.clearStatusActions();
            this.setStage('transcribing');
            this.setRecordButtonState('busy', true);
            this.setRecordingStatus('Retrying transcript polling...', 'info', true);

            const finishedJob = await this.pollJob({
                jobId: this.currentJobId,
                timeoutMs: TRANSCRIPTION_TIMEOUT_MS,
                intervalMs: TRANSCRIPTION_POLL_INTERVAL_MS,
                phaseName: 'Transcription',
                isComplete: (job) => job.status === 'done' && !!this.getTranscriptFromJob(job),
                onTick: (job) => {
                    this.currentJobData = job;
                    this.updateJobMeta(job);
                    this.setRecordingStatus(
                        `Transcribing... ${this.progressLabel(job.progress)}`,
                        'info',
                        true
                    );
                },
            });

            await this.onTranscriptionReady(finishedJob);
        } catch (error) {
            this.setStage('error');
            this.setRecordingStatus(`Polling failed: ${error.message}`, 'error', false);
            this.showStatusActions([
                {
                    label: 'Retry polling',
                    kind: 'primary',
                    onClick: () => this.retryTranscriptionPolling(),
                },
                {
                    label: 'Start new recording',
                    kind: 'secondary',
                    onClick: () => this.resetStudioState(),
                },
            ]);
        } finally {
            this.isBusy = false;
            this.setRecordButtonState('idle', false);
        }
    }

    async onTranscriptionReady(job) {
        this.currentJobData = job;
        this.transcriptionData = this.getTranscriptFromJob(job);
        if (!this.transcriptionData) {
            throw new Error('Transcript is missing in the job response.');
        }

        this.displayTranscription(this.transcriptionData);
        this.setRecordingStatus('Transcription complete.', 'success', false);

        const hasRound1 = this.hasRoundFeedback(job, 1);
        const hasRound2 = this.hasRoundFeedback(job, 2);
        if (hasRound1 && hasRound2) {
            this.renderSummary(this.getFeedbackFromJob(job));
            this.setSummaryStatus('Round 1 and Round 2 feedback ready.', 'success', false);
            this.setStage('done');
            return;
        }

        try {
            await this.requestFeedbackForCurrentJob({ isRetry: false });
        } catch (error) {
            this.handleFeedbackFailure(error);
        }
    }

    async requestFeedbackForCurrentJob({ isRetry }) {
        if (!this.currentJobId || !this.transcriptionData) {
            return;
        }

        this.setStage('feedbacking');
        this.clearSummaryActions();
        this.setSummaryStatus(
            isRetry ? 'Retrying professional feedback generation...' : 'Generating Round 1 and Round 2 feedback...',
            'info',
            true
        );

        let latestJob = this.currentJobData;
        const needsRound1 = !this.hasRoundFeedback(latestJob, 1);

        if (needsRound1 && (isRetry || this.round1RequestedForJobId !== this.currentJobId)) {
            await startRound1Feedback(this.currentJobId);
            this.round1RequestedForJobId = this.currentJobId;
        }

        if (needsRound1) {
            latestJob = await this.pollJob({
                jobId: this.currentJobId,
                timeoutMs: SUMMARY_TIMEOUT_MS,
                intervalMs: SUMMARY_POLL_INTERVAL_MS,
                phaseName: 'Round 1 feedback',
                isComplete: (currentJob) => this.hasRoundFeedback(currentJob, 1),
                isFailed: (currentJob) => {
                    if (currentJob.feedback_round_1_status === 'failed') {
                        return currentJob.feedback_round_1_error || 'Round 1 feedback failed.';
                    }
                    return null;
                },
                onTick: (currentJob) => {
                    this.currentJobData = currentJob;
                    this.updateJobMeta(currentJob);
                    this.setSummaryStatus(
                        `Generating Round 1 feedback... ${this.progressLabel(currentJob.progress)}`,
                        'info',
                        true
                    );
                },
            });
        }

        const needsRound2 = !this.hasRoundFeedback(latestJob, 2);
        if (needsRound2 && (isRetry || this.round2RequestedForJobId !== this.currentJobId)) {
            await startRound2Feedback(this.currentJobId);
            this.round2RequestedForJobId = this.currentJobId;
        }

        if (needsRound2) {
            latestJob = await this.pollJob({
                jobId: this.currentJobId,
                timeoutMs: SUMMARY_TIMEOUT_MS,
                intervalMs: SUMMARY_POLL_INTERVAL_MS,
                phaseName: 'Round 2 feedback',
                isComplete: (currentJob) => this.hasRoundFeedback(currentJob, 2),
                isFailed: (currentJob) => {
                    if (currentJob.feedback_round_2_status === 'failed') {
                        return currentJob.feedback_round_2_error || 'Round 2 feedback failed.';
                    }
                    return null;
                },
                onTick: (currentJob) => {
                    this.currentJobData = currentJob;
                    this.updateJobMeta(currentJob);
                    this.setSummaryStatus(
                        `Generating Round 2 feedback... ${this.progressLabel(currentJob.progress)}`,
                        'info',
                        true
                    );
                },
            });
        }

        this.currentJobData = latestJob;
        const feedback = this.getFeedbackFromJob(latestJob);
        if (!feedback || !feedback.round1 || !feedback.round2) {
            throw new Error('Round feedback payload is incomplete.');
        }
        this.renderSummary(feedback);
        this.setSummaryStatus('Round 1 and Round 2 feedback generated successfully.', 'success', false);
        this.setStage('done');
        this.showToast('Round 1 and Round 2 feedback generated successfully.', 'info');
    }

    async retryFeedbackGeneration() {
        if (!this.currentJobId || !this.transcriptionData || this.isBusy) {
            return;
        }

        try {
            this.isBusy = true;
            await this.requestFeedbackForCurrentJob({ isRetry: true });
        } catch (error) {
            this.handleFeedbackFailure(error);
        } finally {
            this.isBusy = false;
        }
    }

    handleFeedbackFailure(error) {
        const detail = error instanceof Error ? error.message : String(error || 'Unknown error');
        this.setStage('error');
        const partialFeedback = this.getFeedbackFromJob(this.currentJobData);
        if (partialFeedback && (partialFeedback.round1 || partialFeedback.round2 || partialFeedback.legacy)) {
            this.renderSummary(partialFeedback);
        }
        this.setSummaryStatus(`Feedback generation failed. Retry. ${detail}`, 'error', false);
        this.showSummaryActions([
            {
                label: 'Retry feedback',
                kind: 'primary',
                onClick: () => this.retryFeedbackGeneration(),
            },
            {
                label: 'Start new practice',
                kind: 'secondary',
                onClick: () => this.resetStudioState(),
            },
        ]);
    }

    async pollJob({ jobId, timeoutMs, intervalMs, phaseName, isComplete, onTick, isFailed = null }) {
        const startedAt = Date.now();

        while (Date.now() - startedAt < timeoutMs) {
            let job;
            try {
                job = await getJob(jobId);
            } catch (error) {
                throw new Error(`Network error while polling ${phaseName.toLowerCase()}: ${error.message}`);
            }

            if (onTick) {
                onTick(job);
            }

            if (job.status === 'failed') {
                const message = job.summary_error || job.error || `${phaseName} failed.`;
                throw new Error(message);
            }

            if (isFailed) {
                const customFailure = isFailed(job);
                if (customFailure) {
                    throw new Error(String(customFailure));
                }
            }

            if (isComplete(job)) {
                return job;
            }

            await this.sleep(intervalMs);
        }

        throw new Error(`${phaseName} polling timed out.`);
    }

    displayTranscription(data) {
        const resultsPanel = document.getElementById('results-panel');
        const transcriptText = document.getElementById('transcript-text');
        const segmentsTbody = document.getElementById('segments-tbody');
        const wordsTbody = document.getElementById('words-tbody');

        if (!resultsPanel || !transcriptText || !segmentsTbody || !wordsTbody) {
            return;
        }

        this.applyStudioLayout();

        transcriptText.textContent = data.full_text || 'No transcript available';

        segmentsTbody.innerHTML = '';
        if (Array.isArray(data.segments) && data.segments.length > 0) {
            data.segments.forEach(segment => {
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td>${this.formatTimestamp(segment.start)}</td>
                    <td>${this.formatTimestamp(segment.end)}</td>
                    <td>${this.escapeHtml(segment.text || '')}</td>
                `;
                segmentsTbody.appendChild(row);
            });
        } else {
            segmentsTbody.innerHTML = '<tr><td colspan="3">No segments available</td></tr>';
        }

        wordsTbody.innerHTML = '';
        if (Array.isArray(data.words) && data.words.length > 0) {
            data.words.forEach(word => {
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td>${this.formatTimestamp(word.start)}</td>
                    <td>${this.formatTimestamp(word.end)}</td>
                    <td>${this.escapeHtml(word.word || '')}</td>
                `;
                wordsTbody.appendChild(row);
            });
        } else {
            wordsTbody.innerHTML = '<tr><td colspan="3">No word-level data available</td></tr>';
        }

        const copyBtn = document.getElementById('copy-transcript-btn');
        if (copyBtn) {
            copyBtn.onclick = () => {
                navigator.clipboard.writeText(data.full_text || '').then(() => {
                    const original = copyBtn.innerHTML;
                    copyBtn.innerHTML = '<span>Copied!</span>';
                    setTimeout(() => {
                        copyBtn.innerHTML = original;
                    }, 1500);
                });
            };
        }

        resultsPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    renderSummary(feedbackPayload) {
        const summaryCard = document.getElementById('summary-card');
        if (!summaryCard) {
            return;
        }

        if (!feedbackPayload || typeof feedbackPayload !== 'object') {
            summaryCard.innerHTML = '<p class="summary-empty">Professional feedback will appear automatically once transcript is ready.</p>';
            return;
        }

        if (feedbackPayload.round1 || feedbackPayload.round2) {
            const round1Html = feedbackPayload.round1
                ? this.renderRoundFeedbackSection(feedbackPayload.round1, 'Round 1')
                : '<p class="summary-muted">Round 1 feedback is not available yet.</p>';
            const round2Html = feedbackPayload.round2
                ? this.renderRoundFeedbackSection(feedbackPayload.round2, 'Round 2')
                : '<p class="summary-muted">Round 2 feedback is not available yet.</p>';

            summaryCard.innerHTML = `
                <div class="feedback-rounds">
                    <section class="feedback-round">
                        ${round1Html}
                    </section>
                    <section class="feedback-round">
                        ${round2Html}
                    </section>
                </div>
                <details class="raw-json">
                    <summary>View raw JSON</summary>
                    <pre>${this.escapeHtml(JSON.stringify(feedbackPayload, null, 2))}</pre>
                </details>
            `;
            return;
        }

        const summary = feedbackPayload.legacy;
        if (!summary || typeof summary !== 'object') {
            summaryCard.innerHTML = '<p class="summary-empty">Professional feedback will appear automatically once transcript is ready.</p>';
            return;
        }

        const clarityRaw = Number(summary.clarity_score);
        const clarityScore = Number.isFinite(clarityRaw) ? Math.min(10, Math.max(1, Math.round(clarityRaw))) : 1;
        const confidence = String(summary.confidence || '').toLowerCase();
        const confidenceClass = ['low', 'medium', 'high'].includes(confidence) ? confidence : 'low';
        const confidenceLabel = confidenceClass.charAt(0).toUpperCase() + confidenceClass.slice(1);

        summaryCard.innerHTML = `
            <h4 class="summary-main-title">${this.escapeHtml(summary.title || 'Untitled Summary')}</h4>
            <p class="summary-one-line">${this.escapeHtml(summary.one_sentence_summary || '')}</p>

            <div class="summary-grid">
                <div class="summary-block">
                    <h5>Key Points</h5>
                    ${this.renderStringList(summary.key_points, 'None')}
                </div>

                <div class="summary-block">
                    <h5>Audience</h5>
                    <p>${this.escapeHtml(summary.audience || 'N/A')}</p>
                </div>

                <div class="summary-block">
                    <h5>Ask / Goal</h5>
                    <p>${this.escapeHtml(summary.ask_or_goal || 'N/A')}</p>
                </div>

                <div class="summary-block">
                    <h5>Clarity Score</h5>
                    <div class="clarity-row">
                        <span class="clarity-badge">${clarityScore}/10</span>
                        <div class="clarity-bar">
                            <span style="width:${clarityScore * 10}%"></span>
                        </div>
                    </div>
                </div>

                <div class="summary-block">
                    <h5>Confidence</h5>
                    <span class="confidence-pill ${confidenceClass}">${this.escapeHtml(confidenceLabel)}</span>
                </div>

                <div class="summary-block">
                    <h5>Red Flags</h5>
                    ${this.renderStringList(summary.red_flags, 'None')}
                </div>

                <div class="summary-block full-width">
                    <h5>Next Steps</h5>
                    ${this.renderStringList(summary.next_steps, 'None')}
                </div>
            </div>

            <details class="raw-json">
                <summary>View raw JSON</summary>
                <pre>${this.escapeHtml(JSON.stringify(summary, null, 2))}</pre>
            </details>
        `;
    }

    renderRoundFeedbackSection(roundPayload, fallbackTitle) {
        const title = this.escapeHtml(roundPayload.title || fallbackTitle);
        const sections = Array.isArray(roundPayload.sections) ? roundPayload.sections : [];
        const sectionsHtml = sections
            .map(section => this.renderRoundCriterionBlock(section))
            .join('');

        const topActions = this.renderStringList(roundPayload.top_3_actions_for_next_pitch, 'No actions provided');
        const thirtySecond = this.renderStringList(roundPayload.tightened_30_second_structure, 'No structure provided');

        return `
            <h4 class="summary-main-title">${title}</h4>
            <div class="summary-grid">
                ${sectionsHtml || '<div class="summary-block full-width"><p class="summary-muted">No sections available</p></div>'}
                <div class="summary-block full-width">
                    <h5>Tightened 30-Second Structure</h5>
                    ${thirtySecond}
                </div>
                <div class="summary-block full-width">
                    <h5>Top 3 Actions For Next Pitch</h5>
                    ${topActions}
                </div>
            </div>
        `;
    }

    renderRoundCriterionBlock(section) {
        if (!section || typeof section !== 'object') {
            return '';
        }

        const criterion = this.escapeHtml(section.criterion || 'Criterion');
        const verdict = this.escapeHtml(String(section.verdict || 'mixed').toUpperCase());
        let detailsHtml = '';

        Object.entries(section).forEach(([key, value]) => {
            if (key === 'criterion' || key === 'verdict') {
                return;
            }

            if (Array.isArray(value)) {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h5>${this.escapeHtml(this.humanizeKey(key))}</h5>
                        ${this.renderStringList(value, 'None')}
                    </div>
                `;
                return;
            }

            if (value && typeof value === 'object') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h5>${this.escapeHtml(this.humanizeKey(key))}</h5>
                        <pre class="metric-json">${this.escapeHtml(JSON.stringify(value, null, 2))}</pre>
                    </div>
                `;
            }
        });

        return `
            <div class="summary-block full-width">
                <h5>${criterion} <span class="confidence-pill medium">${verdict}</span></h5>
                ${detailsHtml}
            </div>
        `;
    }

    humanizeKey(value) {
        return String(value || '')
            .replaceAll('_', ' ')
            .replace(/\b\w/g, (match) => match.toUpperCase());
    }

    renderStringList(items, emptyText) {
        if (!Array.isArray(items) || items.length === 0) {
            return `<p class="summary-muted">${this.escapeHtml(emptyText)}</p>`;
        }

        return `
            <ul class="summary-list">
                ${items.map(item => `<li>${this.escapeHtml(String(item || ''))}</li>`).join('')}
            </ul>
        `;
    }

    setupTabs() {
        const tabs = document.querySelectorAll('.tab');
        const tabContents = document.querySelectorAll('.tab-content');

        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                const targetTab = tab.dataset.tab;

                tabs.forEach(t => t.classList.remove('active'));
                tabContents.forEach(tc => tc.classList.remove('active'));

                tab.classList.add('active');
                document.getElementById(`${targetTab}-tab`).classList.add('active');
            });
        });
    }

    updateJobMeta(job) {
        const jobMeta = document.getElementById('job-meta');
        if (!jobMeta) {
            return;
        }

        if (!job) {
            jobMeta.textContent = 'No active job.';
            return;
        }

        const jobId = job.job_id || this.currentJobId || 'N/A';
        const status = job.status || 'unknown';
        const progress = typeof job.progress === 'number' ? `${job.progress}%` : 'N/A';
        jobMeta.textContent = `Job: ${jobId} | Status: ${status} | Progress: ${progress}`;
    }

    setStage(nextStage) {
        if (!STAGES.has(nextStage)) {
            return;
        }
        this.stage = nextStage;
        this.applyStudioLayout();
    }

    applyStudioLayout() {
        const inputBlocks = document.getElementById('input-blocks');
        const resultsPanel = document.getElementById('results-panel');
        const newPracticeBtn = document.getElementById('new-practice-btn');
        const hasTranscript = !!this.transcriptionData;

        if (inputBlocks) {
            inputBlocks.classList.toggle('hidden', hasTranscript);
        }

        if (resultsPanel) {
            resultsPanel.classList.toggle('active', hasTranscript);
            resultsPanel.classList.toggle('without-inputs', hasTranscript);
        }

        if (newPracticeBtn) {
            newPracticeBtn.style.display = hasTranscript ? 'inline-flex' : 'none';
        }
    }

    setRecordButtonState(mode, disabled) {
        const recordBtn = document.getElementById('record-btn');
        const recordText = document.getElementById('record-text');
        if (!recordBtn || !recordText) {
            return;
        }

        recordBtn.disabled = disabled;

        if (mode === 'recording') {
            recordBtn.classList.add('recording');
            recordText.textContent = 'Stop recording';
            return;
        }

        if (mode === 'busy') {
            recordBtn.classList.remove('recording');
            recordText.textContent = 'Working...';
            return;
        }

        recordBtn.classList.remove('recording');
        recordText.textContent = 'Start recording';
    }

    setRecordingStatus(message, type, loading) {
        const status = document.getElementById('recording-status');
        this.setStatusElement(status, message, type, loading);
    }

    setSummaryStatus(message, type, loading) {
        const status = document.getElementById('summary-status');
        this.setStatusElement(status, message, type, loading);
    }

    setStatusElement(element, message, type, loading) {
        if (!element) {
            return;
        }

        const text = (message || '').trim();
        if (!text) {
            element.textContent = '';
            element.className = 'status-message';
            return;
        }

        const safeType = ['success', 'error', 'info'].includes(type) ? type : 'info';
        element.textContent = text;
        element.className = `status-message active ${safeType}${loading ? ' loading' : ''}`;
    }

    showStatusActions(actions) {
        const container = document.getElementById('status-actions');
        if (!container) {
            return;
        }

        container.innerHTML = '';
        actions.forEach(action => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `btn ${action.kind === 'primary' ? 'btn-primary' : 'btn-secondary'} btn-small`;
            button.textContent = action.label;
            button.addEventListener('click', action.onClick);
            container.appendChild(button);
        });
    }

    showSummaryActions(actions) {
        const container = document.getElementById('summary-actions');
        if (!container) {
            return;
        }

        container.innerHTML = '';
        actions.forEach(action => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `btn ${action.kind === 'primary' ? 'btn-primary' : 'btn-secondary'} btn-small`;
            button.textContent = action.label;
            button.addEventListener('click', action.onClick);
            container.appendChild(button);
        });
    }

    clearStatusActions() {
        const container = document.getElementById('status-actions');
        if (container) {
            container.innerHTML = '';
        }
    }

    clearSummaryActions() {
        const container = document.getElementById('summary-actions');
        if (container) {
            container.innerHTML = '';
        }
    }

    resetStudioState() {
        this.currentJobId = null;
        this.currentJobData = null;
        this.transcriptionData = null;
        this.round1RequestedForJobId = null;
        this.round2RequestedForJobId = null;
        this.setStage('idle');
        this.updateJobMeta(null);
        this.setRecordingStatus('', 'info', false);
        this.setSummaryStatus('', 'info', false);
        this.renderSummary(null);
        this.clearStatusActions();
        this.clearSummaryActions();

        const resultsPanel = document.getElementById('results-panel');
        if (resultsPanel) {
            resultsPanel.classList.remove('active');
            resultsPanel.classList.remove('without-inputs');
        }

        const timer = document.getElementById('timer');
        if (timer) {
            timer.textContent = '00:00';
        }
        this.setRecordButtonState('idle', false);
        this.applyStudioLayout();
    }

    showToast(message, type = 'info') {
        const container = document.getElementById('toast-area');
        if (!container) {
            return;
        }

        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.textContent = message;
        container.appendChild(toast);

        setTimeout(() => {
            toast.classList.add('fade-out');
            setTimeout(() => {
                toast.remove();
            }, 250);
        }, 2500);
    }

    getTranscriptFromJob(job) {
        return job && (job.transcript || job.result) ? (job.transcript || job.result) : null;
    }

    hasRoundFeedback(job, roundNumber) {
        if (!job || typeof job !== 'object') {
            return false;
        }
        const key = roundNumber === 1 ? 'feedback_round_1' : 'feedback_round_2';
        const payload = job[key];
        return !!(payload && typeof payload === 'object');
    }

    getFeedbackFromJob(job) {
        if (!job || typeof job !== 'object') {
            return null;
        }

        const round1 = this.hasRoundFeedback(job, 1) ? job.feedback_round_1 : null;
        const round2 = this.hasRoundFeedback(job, 2) ? job.feedback_round_2 : null;
        if (round1 || round2) {
            return { round1, round2, legacy: null };
        }

        const legacy = job.feedback || job.summary;
        if (!legacy || typeof legacy !== 'object') {
            return { round1: null, round2: null, legacy: null };
        }
        return { round1: null, round2: null, legacy };
    }

    progressLabel(progress) {
        return typeof progress === 'number' ? `${progress}%` : '';
    }

    escapeHtml(value) {
        return String(value || '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
    }

    sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    formatTimestamp(seconds) {
        const numeric = Number(seconds) || 0;
        const mins = Math.floor(numeric / 60);
        const secs = (numeric % 60).toFixed(2);
        return `${String(mins).padStart(2, '0')}:${String(secs).padStart(5, '0')}`;
    }

    renderHistory() {
        const mainContent = document.getElementById('main-content');
        mainContent.innerHTML = `
            <div class="page-container">
                <div class="placeholder-page">
                    <svg class="placeholder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M3 3v18h18"></path>
                        <path d="M18 17V9l-5 5-5-5v8"></path>
                    </svg>
                    <h1 class="placeholder-title">History</h1>
                    <p class="placeholder-text">
                        Your recording history will appear here. This feature is coming soon!
                    </p>
                </div>
            </div>
        `;
    }

    renderSettings() {
        const mainContent = document.getElementById('main-content');
        mainContent.innerHTML = `
            <div class="page-container">
                <div class="placeholder-page">
                    <svg class="placeholder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="12" cy="12" r="3"></circle>
                        <path d="M12 1v6m0 6v6m9-9h-6m-6 0H3"></path>
                    </svg>
                    <h1 class="placeholder-title">Settings</h1>
                    <p class="placeholder-text">
                        Configure your preferences and account settings. This feature is coming soon!
                    </p>
                </div>
            </div>
        `;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new App();
});
