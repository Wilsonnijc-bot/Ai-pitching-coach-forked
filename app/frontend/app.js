// Main application logic and routing

import { VideoRecorder, formatTime } from './recorder.js';
import { DeckUploader } from './deckUpload.js';
import { createJob, getJob, startRound1Feedback, startRound2Feedback, startRound3Feedback, startRound4Feedback, startRound5Feedback, prepareJob, uploadVideoStreaming, uploadVideoChunked, startProcessing, getUploadUrl, uploadVideoDirectToGcs, processFromGcs, uploadCalibrationPhoto } from './api.js';

const MAX_RECORD_SECONDS = 5 * 60;
const MIN_RECORD_SECONDS = 2;
const TRANSCRIPTION_POLL_INTERVAL_MS = 1500;
const SUMMARY_POLL_INTERVAL_MS = 1500;
// End-to-end processing can include STT + local tone/body-language analysis.
// Keep timeout comfortably above typical long recordings.
const TRANSCRIPTION_TIMEOUT_MS = 12 * 60 * 1000;
const SUMMARY_TIMEOUT_MS = 12 * 60 * 1000;
const STAGES = new Set(['idle', 'recording', 'uploading', 'transcribing', 'feedbacking', 'done', 'error']);
const NO_DECK_OVERALL_ASSESSMENT = 'There is no slide uploaded';

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
        this.round3RequestedForJobId = null;
        this.round4RequestedForJobId = null;
        this.round5RequestedForJobId = null;
        this.transcriptionProgressPct = 0;
        this.feedbackProgressPct = 0;
        this.feedbackPulseTick = 0;
        this.feedbackActiveRound = null;
        this.noDeckNoticeAcknowledged = false;
        this.noDeckModalPromise = null;
        this.noDeckModalResolver = null;

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
        this.handleRoute();
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
                <div class="hero-section hero-panel">
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

                <div class="studio-stack" id="input-blocks">
                    <!-- Step 1: Slim deck upload bar -->
                    <div class="deck-upload-bar" id="deck-upload-card">
                        <div class="upload-area">
                            <span class="upload-bar-label">Upload your pitch deck (optional)</span>
                            <span class="upload-bar-cta">Drag &amp; drop here or click to browse</span>
                            <span class="upload-bar-hint">Optional: .pdf / .pptx, max 25 MB</span>
                            <input type="file" class="file-input" accept=".pdf,.pptx">
                        </div>

                        <div class="file-info">
                            <span class="file-info-inline">
                                <span class="deck-check">&#10003;</span>
                                <span class="file-name"></span>
                                <span class="file-size"></span>
                            </span>
                            <div class="file-actions">
                                <button class="btn btn-secondary btn-small btn-remove">Remove</button>
                                <button class="btn btn-primary btn-small btn-upload">Ready</button>
                            </div>
                        </div>

                        <div class="status-message"></div>
                    </div>

                    <!-- Step 2: Primary recording area -->
                    <div class="card recording-primary" id="recording-card">
                        <h2 class="card-title">Record your pitch</h2>

                        <div class="recording-area">
                            <div class="camera-container" id="camera-container">
                                <video id="camera-preview" autoplay muted playsinline></video>
                                <div class="distance-guide" id="distance-guide">
                                    <div class="guide-silhouette">
                                        <svg viewBox="0 0 200 260" fill="none" xmlns="http://www.w3.org/2000/svg">
                                            <!-- Head -->
                                            <ellipse cx="100" cy="60" rx="40" ry="50" stroke="rgba(255,255,255,0.6)" stroke-width="2" stroke-dasharray="6 4" fill="none"/>
                                            <!-- Shoulders -->
                                            <path d="M30 170 Q35 130 60 120 Q80 115 100 115 Q120 115 140 120 Q165 130 170 170" stroke="rgba(255,255,255,0.6)" stroke-width="2" stroke-dasharray="6 4" fill="none"/>
                                        </svg>
                                    </div>
                                </div>
                                <div class="distance-banner" id="distance-banner">
                                    <span class="distance-icon">📏</span>
                                    <span>Stay <strong>0.5 – 1 m</strong> from camera (about arm's length)</span>
                                </div>
                            </div>

                            <button class="record-button" id="record-btn">
                                <div class="record-icon"></div>
                                <span id="record-text">Start recording</span>
                            </button>

                            <div class="timer" id="timer">00:00</div>
                        </div>

                        <div class="no-deck-modal-backdrop" id="no-deck-modal" role="dialog" aria-modal="true" aria-labelledby="no-deck-modal-title" aria-describedby="no-deck-modal-text">
                            <div class="no-deck-modal-card">
                                <h3 class="no-deck-modal-title" id="no-deck-modal-title">Deck upload is optional</h3>
                                <p class="no-deck-modal-text" id="no-deck-modal-text">
                                    You can continue without a deck. If none is uploaded, slide feedback will say: "There is no slide uploaded".
                                </p>
                                <div class="no-deck-modal-actions">
                                    <button type="button" class="btn btn-primary no-deck-modal-ok" id="no-deck-modal-ok">OK</button>
                                </div>
                            </div>
                        </div>

                        <p class="job-meta" id="job-meta">No active job.</p>
                        <div class="status-message" id="recording-status"></div>
                        <div class="status-actions" id="status-actions"></div>
                    </div>
                </div>  <!-- /studio-stack -->

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

        this.deckUploader = new DeckUploader('deck-upload-card', {
            onFileChange: (hasDeck) => this._onDeckChanged(hasDeck),
        });
        this._wireNoDeckModalActions();
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
        this._syncDeckRecordingState();
    }

    setupStudioActions() {
        const newPracticeBtn = document.getElementById('new-practice-btn');
        if (newPracticeBtn) {
            newPracticeBtn.addEventListener('click', () => {
                this.resetStudioState();
            });
        }
    }

    /** Called by DeckUploader whenever a file is attached or removed. */
    _onDeckChanged(hasDeck) {
        if (hasDeck) {
            this._dismissNoDeckModal(false);
        }
        this._syncDeckRecordingState();
    }

    /** Keep record controls ready regardless of deck presence (deck is optional). */
    _syncDeckRecordingState() {
        const hasDeck = this.deckUploader && this.deckUploader.hasDeck();
        const recordBtn = document.getElementById('record-btn');
        const recordText = document.getElementById('record-text');
        if (!recordBtn || !recordText) return;

        // Don't interfere while busy / already recording
        if (this.isRecording || this.isBusy || ['uploading', 'transcribing', 'feedbacking'].includes(this.stage)) {
            return;
        }

        recordBtn.disabled = false;
        recordBtn.classList.remove('no-deck');
        recordText.textContent = 'Start recording';

        if (hasDeck) {
            this.setRecordingStatus('Deck attached — you\'re ready to record!', 'success', false);
        } else {
            this.setRecordingStatus('Deck is optional. You can start recording now.', 'info', false);
        }
    }

    _wireNoDeckModalActions() {
        const okBtn = document.getElementById('no-deck-modal-ok');
        if (!okBtn) {
            return;
        }
        okBtn.addEventListener('click', () => {
            this.noDeckNoticeAcknowledged = true;
            this._dismissNoDeckModal(true);
        });
    }

    _openNoDeckModalAndWait() {
        if (this.noDeckModalPromise) {
            return this.noDeckModalPromise;
        }

        const modal = document.getElementById('no-deck-modal');
        if (!modal) {
            return Promise.resolve(false);
        }

        modal.classList.add('active');
        this.noDeckModalPromise = new Promise((resolve) => {
            this.noDeckModalResolver = resolve;
        });
        return this.noDeckModalPromise;
    }

    _dismissNoDeckModal(confirmed) {
        const modal = document.getElementById('no-deck-modal');
        if (modal) {
            modal.classList.remove('active');
        }

        if (!this.noDeckModalResolver) {
            this.noDeckModalPromise = null;
            return;
        }
        const resolve = this.noDeckModalResolver;
        this.noDeckModalResolver = null;
        this.noDeckModalPromise = null;
        resolve(Boolean(confirmed));
    }

    setupRecording() {
        const recordBtn = document.getElementById('record-btn');

        recordBtn.addEventListener('click', async () => {
            if (this.isBusy || ['uploading', 'transcribing', 'feedbacking'].includes(this.stage)) {
                return;
            }
            if (this.noDeckModalPromise) {
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
        const hasDeck = !!(this.deckUploader && this.deckUploader.hasDeck());
        if (!hasDeck && !this.noDeckNoticeAcknowledged) {
            const confirmed = await this._openNoDeckModalAndWait();
            if (!confirmed) {
                return;
            }
        }
        this._dismissNoDeckModal(false);

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

            // Show the distance guide overlay and banner
            const distanceGuide = document.getElementById('distance-guide');
            const distanceBanner = document.getElementById('distance-banner');
            if (distanceGuide) distanceGuide.classList.add('visible');
            if (distanceBanner) distanceBanner.classList.add('visible');

            // ── Calibration step: capture a selfie for body-language baselines ──
            this.setRecordingStatus(
                'Position yourself: stand at arm\'s length (0.5 – 1 m), face the camera, align with the silhouette guide, and hold still...',
                'info',
                true,
            );
            // Small delay so the camera auto-exposure stabilises and the user
            // has a moment to get into position.
            await new Promise(r => setTimeout(r, 1500));

            try {
                this.setRecordingStatus('Capturing calibration snapshot...', 'info', true);
                const calibrationBlob = await this.videoRecorder.captureCalibrationFrame();
                // Prepare the job shell first so we have a job_id for calibration
                const prepared = await prepareJob();
                this._calibrationJobId = prepared.job_id;
                const calResult = await uploadCalibrationPhoto(prepared.job_id, calibrationBlob);

                // Show distance feedback from calibration
                if (calResult && calResult.calibration) {
                    const cal = calResult.calibration;
                    if (cal.distance_ok === false) {
                        // Distance is not ideal — warn the user and give them time to adjust
                        this._showDistanceFeedback(cal.distance_status, cal.distance_feedback);
                        this.setRecordingStatus(
                            cal.distance_feedback + ' Adjust and hold still — re-calibrating in 3s...',
                            'warning',
                            true,
                        );
                        await new Promise(r => setTimeout(r, 3000));
                        // Re-capture after user adjusts
                        try {
                            const recalBlob = await this.videoRecorder.captureCalibrationFrame();
                            const recalResult = await uploadCalibrationPhoto(prepared.job_id, recalBlob);
                            if (recalResult && recalResult.calibration) {
                                const recal = recalResult.calibration;
                                this._showDistanceFeedback(recal.distance_status, recal.distance_feedback);
                                if (recal.distance_ok) {
                                    this.setRecordingStatus('Distance adjusted — starting recording...', 'success', false);
                                } else {
                                    this.setRecordingStatus(
                                        recal.distance_feedback + ' Proceeding anyway — try to maintain arm\'s length.',
                                        'warning',
                                        false,
                                    );
                                }
                            }
                        } catch (recalErr) {
                            console.warn('Re-calibration failed (non-fatal):', recalErr.message);
                        }
                    } else {
                        this._showDistanceFeedback('ok', cal.distance_feedback);
                        this.setRecordingStatus('Calibration done — starting recording...', 'success', false);
                    }
                } else {
                    this.setRecordingStatus('Calibration done — starting recording...', 'success', false);
                }
            } catch (calErr) {
                console.warn('Calibration snapshot failed (non-fatal):', calErr.message);
                // Calibration is optional — proceed without it
                this.setRecordingStatus('Starting recording (calibration skipped)...', 'info', false);
                this._calibrationJobId = null;
            }

            // Hide the silhouette guide but keep the distance banner as a reminder
            if (distanceGuide) distanceGuide.classList.remove('visible');
            const banner = document.getElementById('distance-banner');
            if (banner) {
                banner.classList.add('recording-reminder');
                banner.innerHTML = '<span class="distance-icon">📏</span> <span>Maintain <strong>0.5 – 1 m</strong> distance — stay consistent for accurate analysis</span>';
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

            // Clean up distance guide / banner
            this._hideDistanceOverlays();

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
            this.round3RequestedForJobId = null;
            this.round4RequestedForJobId = null;
            this.round5RequestedForJobId = null;
            this.feedbackActiveRound = null;

            let jobId;
            try {
                // ── Reuse calibration job or prepare a new one ──
                this.setRecordingStatus('Preparing upload...', 'info', true);
                if (this._calibrationJobId) {
                    jobId = this._calibrationJobId;
                    this._calibrationJobId = null;
                } else {
                    const prepared = await prepareJob();
                    jobId = prepared.job_id;
                }

                let gcsUploadSucceeded = false;
                try {
                    this.setRecordingStatus('Requesting upload URL...', 'info', true);
                    const { upload_url, content_type } = await getUploadUrl(jobId);

                    this.setRecordingStatus('Uploading video... 0%', 'info', true);
                    await uploadVideoDirectToGcs(upload_url, videoBlob, content_type, {
                        onProgress: (pct) => {
                            this.setRecordingStatus(`Uploading video... ${Math.min(pct, 100)}%`, 'info', true);
                        },
                    });
                    gcsUploadSucceeded = true;

                    this.setRecordingStatus('Starting processing...', 'info', true);
                    await processFromGcs(jobId, selectedDeck);
                } catch (gcsErr) {
                    console.warn('Direct GCS upload failed, falling back to streaming upload:', gcsErr.message);
                }

                // ── Fallback 1: Chunked upload (timeout-resilient) ──
                if (!gcsUploadSucceeded) {
                    this.setRecordingStatus('Uploading video... 0%', 'info', true);
                    await uploadVideoChunked(jobId, videoBlob, {
                        onProgress: ({ pct }) => {
                            this.setRecordingStatus(`Uploading video... ${Math.min(pct, 100)}%`, 'info', true);
                        },
                    });

                    this.setRecordingStatus('Starting processing...', 'info', true);
                    await startProcessing(jobId, selectedDeck);
                }
            } catch (uploadErr) {
                throw uploadErr;
            }

            this.currentJobId = jobId;
            this.currentJobData = null;
            this.transcriptionData = null;
            this.transcriptionProgressPct = 0;
            this.updateJobMeta({
                job_id: jobId,
                status: 'queued',
                progress: 0,
            });
            this.setStage('transcribing');
            this._setTranscriptionProgressStatus({ status: 'queued', progress: 0 });

            const finishedJob = await this.pollJob({
                jobId: jobId,
                timeoutMs: TRANSCRIPTION_TIMEOUT_MS,
                intervalMs: TRANSCRIPTION_POLL_INTERVAL_MS,
                phaseName: 'Transcription',
                isComplete: (job) => job.status === 'done' && !!this.getTranscriptFromJob(job),
                onTick: (job) => {
                    this.currentJobData = job;
                    this.updateJobMeta(job);
                    this._setTranscriptionProgressStatus(job);
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
            this._setTranscriptionProgressStatus({
                status: this.currentJobData?.status || 'transcribing',
                progress: this.currentJobData?.progress ?? this.transcriptionProgressPct,
            });

            const finishedJob = await this.pollJob({
                jobId: this.currentJobId,
                timeoutMs: TRANSCRIPTION_TIMEOUT_MS,
                intervalMs: TRANSCRIPTION_POLL_INTERVAL_MS,
                phaseName: 'Transcription',
                isComplete: (job) => job.status === 'done' && !!this.getTranscriptFromJob(job),
                onTick: (job) => {
                    this.currentJobData = job;
                    this.updateJobMeta(job);
                    this._setTranscriptionProgressStatus(job);
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
        const hasRound3 = this.hasRoundFeedback(job, 3);
        const hasRound4 = this.hasRoundFeedback(job, 4);
        const hasRound5 = this.hasRoundFeedback(job, 5);
        if (hasRound1 && hasRound2 && hasRound3 && hasRound4 && hasRound5) {
            this.renderSummary(this.getFeedbackFromJob(job));
            this.setSummaryStatus('All feedback rounds ready.', 'success', false);
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

        let latestJob = this.currentJobData;
        this.feedbackProgressPct = this._completedFeedbackRounds(latestJob) * 20;
        this.feedbackPulseTick = 0;
        this.feedbackActiveRound = null;
        const firstPendingRound = Math.min(5, this._completedFeedbackRounds(latestJob) + 1);
        this._setFeedbackProgressStatus({
            round: firstPendingRound,
            progress: this.feedbackProgressPct,
        });

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
                    this._setFeedbackProgressStatus({ round: 1, job: currentJob });
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
                    this._setFeedbackProgressStatus({ round: 2, job: currentJob });
                },
            });
        }

        const needsRound3 = !this.hasRoundFeedback(latestJob, 3);
        if (needsRound3 && (isRetry || this.round3RequestedForJobId !== this.currentJobId)) {
            await startRound3Feedback(this.currentJobId);
            this.round3RequestedForJobId = this.currentJobId;
        }

        if (needsRound3) {
            latestJob = await this.pollJob({
                jobId: this.currentJobId,
                timeoutMs: SUMMARY_TIMEOUT_MS,
                intervalMs: SUMMARY_POLL_INTERVAL_MS,
                phaseName: 'Round 3 feedback',
                isComplete: (currentJob) => this.hasRoundFeedback(currentJob, 3),
                isFailed: (currentJob) => {
                    if (currentJob.feedback_round_3_status === 'failed') {
                        return currentJob.feedback_round_3_error || 'Round 3 feedback failed.';
                    }
                    return null;
                },
                onTick: (currentJob) => {
                    this.currentJobData = currentJob;
                    this.updateJobMeta(currentJob);
                    this._setFeedbackProgressStatus({ round: 3, job: currentJob });
                },
            });
        }

        const needsRound4 = !this.hasRoundFeedback(latestJob, 4);
        if (needsRound4 && (isRetry || this.round4RequestedForJobId !== this.currentJobId)) {
            await startRound4Feedback(this.currentJobId);
            this.round4RequestedForJobId = this.currentJobId;
        }

        if (needsRound4) {
            latestJob = await this.pollJob({
                jobId: this.currentJobId,
                timeoutMs: SUMMARY_TIMEOUT_MS,
                intervalMs: SUMMARY_POLL_INTERVAL_MS,
                phaseName: 'Round 4 feedback',
                isComplete: (currentJob) => this.hasRoundFeedback(currentJob, 4),
                isFailed: (currentJob) => {
                    if (currentJob.feedback_round_4_status === 'failed') {
                        return currentJob.feedback_round_4_error || 'Round 4 feedback failed.';
                    }
                    return null;
                },
                onTick: (currentJob) => {
                    this.currentJobData = currentJob;
                    this.updateJobMeta(currentJob);
                    this._setFeedbackProgressStatus({ round: 4, job: currentJob });
                },
            });
        }

        const needsRound5 = !this.hasRoundFeedback(latestJob, 5);
        if (needsRound5 && (isRetry || this.round5RequestedForJobId !== this.currentJobId)) {
            await startRound5Feedback(this.currentJobId);
            this.round5RequestedForJobId = this.currentJobId;
        }

        if (needsRound5) {
            latestJob = await this.pollJob({
                jobId: this.currentJobId,
                timeoutMs: SUMMARY_TIMEOUT_MS,
                intervalMs: SUMMARY_POLL_INTERVAL_MS,
                phaseName: 'Round 5 feedback',
                isComplete: (currentJob) => this.hasRoundFeedback(currentJob, 5),
                isFailed: (currentJob) => {
                    if (currentJob.feedback_round_5_status === 'failed') {
                        return currentJob.feedback_round_5_error || 'Round 5 feedback failed.';
                    }
                    return null;
                },
                onTick: (currentJob) => {
                    this.currentJobData = currentJob;
                    this.updateJobMeta(currentJob);
                    this._setFeedbackProgressStatus({ round: 5, job: currentJob });
                },
            });
        }

        this.currentJobData = latestJob;
        const feedback = this.getFeedbackFromJob(latestJob);
        if (!feedback || !feedback.round1 || !feedback.round2 || !feedback.round3 || !feedback.round4 || !feedback.round5) {
            throw new Error('Round feedback payload is incomplete.');
        }
        this.renderSummary(feedback);
        this.setSummaryStatus('All feedback rounds generated successfully.', 'success', false);
        this.setStage('done');
        this.showToast('All feedback rounds generated successfully.', 'info');
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
        if (partialFeedback && (partialFeedback.round1 || partialFeedback.round2 || partialFeedback.round3 || partialFeedback.round4 || partialFeedback.round5 || partialFeedback.legacy)) {
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
        let lastStatus = 'unknown';
        let lastProgress = null;

        while (Date.now() - startedAt < timeoutMs) {
            let job;
            try {
                job = await getJob(jobId);
            } catch (error) {
                throw new Error(`Network error while polling ${phaseName.toLowerCase()}: ${error.message}`);
            }

            lastStatus = String(job.status || 'unknown');
            lastProgress = Number.isFinite(job.progress) ? job.progress : null;

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

        const progressLabel = lastProgress == null ? 'unknown' : `${lastProgress}%`;
        throw new Error(`${phaseName} polling timed out (last status: ${lastStatus}, progress: ${progressLabel}).`);
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

        if (feedbackPayload.round1 || feedbackPayload.round2 || feedbackPayload.round3 || feedbackPayload.round4 || feedbackPayload.round5) {
            const allSections = [];
            // Round 5 (Overview + Pitch Deck Evaluation) rendered FIRST
            if (feedbackPayload.round5 && Array.isArray(feedbackPayload.round5.sections)) {
                allSections.push(...feedbackPayload.round5.sections);
            }
            // Round 4 (Body Language & Presence) rendered SECOND
            if (feedbackPayload.round4 && Array.isArray(feedbackPayload.round4.sections)) {
                allSections.push(...feedbackPayload.round4.sections);
            }
            // Round 3 (Vocal Tone & Energy) rendered THIRD
            if (feedbackPayload.round3 && Array.isArray(feedbackPayload.round3.sections)) {
                allSections.push(...feedbackPayload.round3.sections);
            }
            if (feedbackPayload.round1 && Array.isArray(feedbackPayload.round1.sections)) {
                allSections.push(...feedbackPayload.round1.sections);
            }
            if (feedbackPayload.round2 && Array.isArray(feedbackPayload.round2.sections)) {
                allSections.push(...feedbackPayload.round2.sections);
            }

            let statusHtml = '';
            if (!feedbackPayload.round5) {
                statusHtml += '<p class="summary-muted">Round 5 (Overview + Deck Evaluation) feedback is not available yet.</p>';
            }
            if (!feedbackPayload.round4) {
                statusHtml += '<p class="summary-muted">Round 4 (Body Language) feedback is not available yet.</p>';
            }
            if (!feedbackPayload.round3) {
                statusHtml += '<p class="summary-muted">Round 3 (Vocal Tone) feedback is not available yet.</p>';
            }
            if (!feedbackPayload.round1) {
                statusHtml += '<p class="summary-muted">Round 1 feedback is not available yet.</p>';
            }
            if (!feedbackPayload.round2) {
                statusHtml += '<p class="summary-muted">Round 2 feedback is not available yet.</p>';
            }

            const bodyActions = feedbackPayload.round4?.top_3_body_language_actions || [];
            const improvementCards = [];
            if (bodyActions.length > 0) {
                improvementCards.push(`
                    <div class="feedback-action-card action-card-neutral">
                        <h4 class="subsection-label semantic-label-neutral">recommended body language improvements</h4>
                        ${this.renderStringList(bodyActions, 'No body language actions provided')}
                    </div>
                `);
            }

            const vocalActions = feedbackPayload.round3?.top_3_vocal_actions || [];
            if (vocalActions.length > 0) {
                improvementCards.push(`
                    <div class="feedback-action-card action-card-neutral">
                        <h4 class="subsection-label semantic-label-neutral">recommended Vocal improvements</h4>
                        ${this.renderStringList(vocalActions, 'No vocal actions provided')}
                    </div>
                `);
            }

            const sectionCardsList = [];
            const improvementCardsHtml = improvementCards.join('');
            let insertedAfterToneSection = false;
            allSections.forEach(section => {
                sectionCardsList.push(this.renderSectionCard(section));
                if (!insertedAfterToneSection && section && section.criterion === 'Tone-Product Alignment') {
                    if (improvementCardsHtml) {
                        sectionCardsList.push(improvementCardsHtml);
                    }
                    insertedAfterToneSection = true;
                }
            });
            if (!insertedAfterToneSection && improvementCardsHtml) {
                sectionCardsList.push(improvementCardsHtml);
            }

            const sectionCards = sectionCardsList.join('');
            const actionBlocks = [];

            const actions1 = feedbackPayload.round1?.top_3_actions_for_next_pitch || [];
            const actions2 = feedbackPayload.round2?.top_3_actions_for_next_pitch || [];
            const allActions = [...actions1, ...actions2];
            if (allActions.length > 0) {
                actionBlocks.push(`
                    <div class="feedback-action-card action-card-top-actions">
                        <h4 class="top-actions-title">Top Actions For Next Pitch</h4>
                        <ul class="top-actions-list">
                            ${allActions.map(a => `<li class="top-actions-item">${this.escapeHtml(String(a || ''))}</li>`).join('')}
                        </ul>
                    </div>
                `);
            }

            summaryCard.innerHTML = `
                <div class="feedback-sections">
                    ${sectionCards || '<p class="summary-muted">No sections available</p>'}
                    ${statusHtml}
                    ${actionBlocks.join('')}
                </div>
            `;
            this.initCollapsibleSections();
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

        `;
    }

    initCollapsibleSections() {
        const summaryCard = document.getElementById('summary-card');
        if (!summaryCard) {
            return;
        }

        const sectionCards = summaryCard.querySelectorAll('.section-card');
        sectionCards.forEach(card => {
            const titleEl = card.querySelector('.section-card-title');
            const body = card.querySelector('.section-card-body');
            if (!titleEl || !body) {
                return;
            }

            const title = String(titleEl.textContent || '').trim().toLowerCase();
            if (title === 'overview') {
                return;
            }

            if (body.querySelector('.section-see-more-btn')) {
                return;
            }

            const detailBlocks = Array.from(body.children)
                .filter(node => node instanceof HTMLElement && node.classList.contains('criterion-detail'));
            if (detailBlocks.length <= 1) {
                return;
            }

            const [previewBlock, ...remainingBlocks] = detailBlocks;
            const collapsibleContent = document.createElement('div');
            collapsibleContent.className = 'section-collapsible-content';
            collapsibleContent.style.display = 'none';

            remainingBlocks.forEach(block => {
                collapsibleContent.appendChild(block);
            });

            const toggleBtn = document.createElement('button');
            toggleBtn.type = 'button';
            toggleBtn.className = 'section-see-more-btn';
            toggleBtn.setAttribute('aria-expanded', 'false');
            toggleBtn.innerHTML = 'See more <span class="see-more-arrow">&raquo;</span>';

            toggleBtn.addEventListener('click', () => {
                const isExpanded = toggleBtn.classList.toggle('expanded');
                toggleBtn.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');

                if (isExpanded) {
                    collapsibleContent.style.display = 'block';
                    collapsibleContent.classList.add('is-open');
                    toggleBtn.innerHTML = 'See less <span class="see-more-arrow">&laquo;</span>';
                } else {
                    collapsibleContent.classList.remove('is-open');
                    collapsibleContent.style.display = 'none';
                    toggleBtn.innerHTML = 'See more <span class="see-more-arrow">&raquo;</span>';
                }
            });

            previewBlock.insertAdjacentElement('afterend', toggleBtn);
            toggleBtn.insertAdjacentElement('afterend', collapsibleContent);
        });
    }

    renderSectionCard(section) {
        if (!section || typeof section !== 'object') {
            return '';
        }

        const criterion = this.escapeHtml(section.criterion || 'Criterion');
        const verdictRaw = String(section.verdict || 'mixed').toLowerCase();
        const verdictLabel = verdictRaw.toUpperCase();
        const verdictClass = ['weak', 'strong'].includes(verdictRaw) ? verdictRaw : 'mixed';

        // Custom rendering for Overview section (Round 5)
        if (section.criterion === 'Overview') {
            return this.renderOverviewSection(section, criterion, verdictLabel, verdictClass);
        }

        // Custom rendering for Pitch Deck Evaluation section (Round 5)
        if (section.criterion === 'Pitch Deck Evaluation') {
            return this.renderDeckEvaluationSection(section, criterion, verdictLabel, verdictClass);
        }

        // Custom rendering for Posture & Stillness section
        if (section.criterion === 'Posture & Stillness') {
            return this.renderPostureSection(section, criterion, verdictLabel, verdictClass);
        }

        // Custom rendering for Eye Contact section
        if (section.criterion === 'Eye Contact') {
            return this.renderEyeContactSection(section, criterion, verdictLabel, verdictClass);
        }

        // Custom rendering for Calm Confidence section
        if (section.criterion === 'Calm Confidence') {
            return this.renderCalmConfidenceSection(section, criterion, verdictLabel, verdictClass);
        }

        // Custom rendering for Energy & Presence section
        if (section.criterion === 'Energy & Presence') {
            return this.renderEnergyPresenceSection(section, criterion, verdictLabel, verdictClass);
        }

        // Custom rendering for Pacing & Emphasis section
        if (section.criterion === 'Pacing & Emphasis') {
            return this.renderPacingEmphasisSection(section, criterion, verdictLabel, verdictClass);
        }

        // Custom rendering for Tone-Product Alignment section
        if (section.criterion === 'Tone-Product Alignment') {
            return this.renderToneProductSection(section, criterion, verdictLabel, verdictClass);
        }

        // Custom rendering for Clarity & Conviction section (Round 2)
        if (section.criterion === 'Clarity & Conviction') {
            return this.renderClarityConvictionSection(section, criterion, verdictLabel, verdictClass);
        }

        // Custom rendering for strategic content sections (Round 1 & 2)
        if (this._isStrategicSection(section.criterion)) {
            return this.renderStrategicSection(section, criterion, verdictLabel, verdictClass);
        }

        let detailsHtml = '';

        Object.entries(section).forEach(([key, value]) => {
            if (key === 'criterion' || key === 'verdict') {
                return;
            }

            if (Array.isArray(value)) {
                const hasObjects = value.length > 0 && typeof value[0] === 'object' && value[0] !== null;
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${hasObjects ? this.renderObjectList(value) : this.renderStringList(value, 'None')}
                    </div>
                `;
                return;
            }

            if (typeof value === 'string' || typeof value === 'number') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        <p>${this.escapeHtml(String(value))}</p>
                    </div>
                `;
                return;
            }

            if (value && typeof value === 'object') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${this.renderMetricSummary(value)}
                    </div>
                `;
            }
        });

        return `
            <div class="section-card">
                <div class="section-card-header">
                    <h3 class="section-card-title">${criterion}</h3>
                    <span class="verdict-badge ${verdictClass}">${verdictLabel}</span>
                </div>
                <div class="section-card-body">
                    ${detailsHtml || '<p class="summary-muted">No details available</p>'}
                </div>
            </div>
        `;
    }

    renderOverviewSection(section, criterion, verdictLabel, verdictClass) {
        let detailsHtml = '';

        if (section.overall_evaluation) {
            detailsHtml += `
                <div class="criterion-detail semantic-neutral-box">
                    <h4 class="subsection-label semantic-label-neutral">Overall Evaluation</h4>
                    <p>${this.escapeHtml(String(section.overall_evaluation))}</p>
                </div>
            `;
        }

        if (Array.isArray(section.key_strengths)) {
            detailsHtml += `
                <div class="criterion-detail strategic-evidence-box">
                    <h4 class="subsection-label strategic-label-evidence">Key Strengths</h4>
                    ${this.renderStringList(section.key_strengths, 'No strengths provided')}
                </div>
            `;
        }

        if (Array.isArray(section.areas_of_improvement)) {
            detailsHtml += `
                <div class="criterion-detail strategic-missing-box">
                    <h4 class="subsection-label strategic-label-missing">Areas Of Improvement</h4>
                    ${this.renderStringList(section.areas_of_improvement, 'No improvement areas provided')}
                </div>
            `;
        }

        const handledKeys = new Set([
            'criterion',
            'verdict',
            'overall_evaluation',
            'key_strengths',
            'areas_of_improvement',
        ]);
        Object.entries(section).forEach(([key, value]) => {
            if (handledKeys.has(key)) return;
            if (Array.isArray(value)) {
                const hasObjects = value.length > 0 && typeof value[0] === 'object' && value[0] !== null;
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${hasObjects ? this.renderObjectList(value) : this.renderStringList(value, 'None')}
                    </div>
                `;
            } else if (typeof value === 'string' || typeof value === 'number') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        <p>${this.escapeHtml(String(value))}</p>
                    </div>
                `;
            } else if (value && typeof value === 'object') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${this.renderMetricSummary(value)}
                    </div>
                `;
            }
        });

        return `
            <div class="section-card">
                <div class="section-card-header">
                    <h3 class="section-card-title">${criterion}</h3>
                    <span class="verdict-badge ${verdictClass}">${verdictLabel}</span>
                </div>
                <div class="section-card-body">
                    ${detailsHtml || '<p class="summary-muted">No details available</p>'}
                </div>
            </div>
        `;
    }

    renderDeckEvaluationSection(section, criterion, verdictLabel, verdictClass) {
        let detailsHtml = '';
        const assessment = String(section.overall_assessment || '').trim();
        const noDeckOnlyMessage = assessment === NO_DECK_OVERALL_ASSESSMENT;

        if (assessment) {
            detailsHtml += `
                <div class="criterion-detail semantic-neutral-box">
                    <h4 class="subsection-label semantic-label-neutral">Overall Assessment</h4>
                    <p>${this.escapeHtml(assessment)}</p>
                </div>
            `;
        }

        if (noDeckOnlyMessage) {
            return `
                <div class="section-card">
                    <div class="section-card-header">
                        <h3 class="section-card-title">${criterion}</h3>
                        <span class="verdict-badge ${verdictClass}">${verdictLabel}</span>
                    </div>
                    <div class="section-card-body">
                        ${detailsHtml || '<p class="summary-muted">No details available</p>'}
                    </div>
                </div>
            `;
        }

        const lackingContent = section.lacking_content;
        if (Array.isArray(lackingContent)) {
            const cards = lackingContent.length === 0
                ? '<p class="summary-muted">No lacking content identified.</p>'
                : lackingContent.map(item => {
                    if (!item || typeof item !== 'object') {
                        return `<div class="obj-item"><div class="obj-row"><span class="obj-val">${this.escapeHtml(String(item || ''))}</span></div></div>`;
                    }
                    return `
                        <div class="obj-item moment-card-corrective">
                            <div class="obj-row"><span class="obj-key">What:</span> <span class="obj-val">${this.escapeHtml(String(item.what || ''))}</span></div>
                            <div class="obj-row"><span class="obj-key">Why:</span> <span class="obj-val">${this.escapeHtml(String(item.why || ''))}</span></div>
                        </div>
                    `;
                }).join('');

            detailsHtml += `
                <div class="criterion-detail strategic-missing-box">
                    <h4 class="subsection-label strategic-label-missing">Lacking Content</h4>
                    ${cards}
                </div>
            `;
        }

        const structuralFlowIssues = section.structural_flow_issues;
        if (Array.isArray(structuralFlowIssues)) {
            const cards = structuralFlowIssues.length === 0
                ? '<p class="summary-muted">No structural flow issues identified.</p>'
                : structuralFlowIssues.map(item => {
                    if (!item || typeof item !== 'object') {
                        return `<div class="obj-item"><div class="obj-row"><span class="obj-val">${this.escapeHtml(String(item || ''))}</span></div></div>`;
                    }
                    return `
                        <div class="obj-item moment-card-corrective">
                            <div class="obj-row"><span class="obj-key">Issue:</span> <span class="obj-val">${this.escapeHtml(String(item.issue || ''))}</span></div>
                            <div class="obj-row"><span class="obj-key">Impact:</span> <span class="obj-val">${this.escapeHtml(String(item.impact || ''))}</span></div>
                        </div>
                    `;
                }).join('');

            detailsHtml += `
                <div class="criterion-detail strategic-missing-box">
                    <h4 class="subsection-label strategic-label-missing">Structural Flow Issues</h4>
                    ${cards}
                </div>
            `;
        }

        if (Array.isArray(section.recommended_refinements)) {
            const refinements = section.recommended_refinements;
            detailsHtml += `
                <div class="criterion-detail strategic-rewrite-box">
                    <h4 class="subsection-label strategic-label-rewrite">Recommended Refinements</h4>
                    ${refinements.length === 0
                        ? '<p class="summary-muted">No refinements provided.</p>'
                        : `
                            <ol class="summary-list">
                                ${refinements.map(item => `<li>${this.escapeHtml(String(item || ''))}</li>`).join('')}
                            </ol>
                        `
                    }
                </div>
            `;
        }

        const handledKeys = new Set([
            'criterion',
            'verdict',
            'overall_assessment',
            'lacking_content',
            'structural_flow_issues',
            'recommended_refinements',
        ]);
        Object.entries(section).forEach(([key, value]) => {
            if (handledKeys.has(key)) return;
            if (Array.isArray(value)) {
                const hasObjects = value.length > 0 && typeof value[0] === 'object' && value[0] !== null;
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${hasObjects ? this.renderObjectList(value) : this.renderStringList(value, 'None')}
                    </div>
                `;
            } else if (typeof value === 'string' || typeof value === 'number') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        <p>${this.escapeHtml(String(value))}</p>
                    </div>
                `;
            } else if (value && typeof value === 'object') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${this.renderMetricSummary(value)}
                    </div>
                `;
            }
        });

        return `
            <div class="section-card">
                <div class="section-card-header">
                    <h3 class="section-card-title">${criterion}</h3>
                    <span class="verdict-badge ${verdictClass}">${verdictLabel}</span>
                </div>
                <div class="section-card-body">
                    ${detailsHtml || '<p class="summary-muted">No details available</p>'}
                </div>
            </div>
        `;
    }

    renderPostureSection(section, criterion, verdictLabel, verdictClass) {
        let detailsHtml = '';

        // 1) Overall Assessment (first)
        if (section.overall_assessment) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label">Overall Assessment</h4>
                    <p>${this.escapeHtml(String(section.overall_assessment))}</p>
                </div>
            `;
        }

        // 2) Stability Percentage
        if (section.stability_percentage != null) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label">Stability Percentage</h4>
                    <p>${this.escapeHtml(String(section.stability_percentage))}</p>
                </div>
            `;
        }

        // 3) Stable Moments (positive / green)
        const stableMoments = section.stable_moments;
        if (Array.isArray(stableMoments)) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label moment-label-positive">Stable Moments</h4>
                    ${this.renderMomentCards(stableMoments, 'positive')}
                </div>
            `;
        }

        // 4) Unstable Moments (corrective / yellow)
        const unstableMoments = section.unstable_moments;
        if (Array.isArray(unstableMoments)) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label moment-label-corrective">Unstable Moments</h4>
                    ${this.renderMomentCards(unstableMoments, 'corrective')}
                </div>
            `;
        }

        // 5) Remaining keys (catch-all for any extra fields)
        const handledKeys = new Set(['criterion', 'verdict', 'overall_assessment', 'stability_percentage', 'stable_moments', 'unstable_moments']);
        Object.entries(section).forEach(([key, value]) => {
            if (handledKeys.has(key)) return;
            if (Array.isArray(value)) {
                const hasObjects = value.length > 0 && typeof value[0] === 'object' && value[0] !== null;
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${hasObjects ? this.renderObjectList(value) : this.renderStringList(value, 'None')}
                    </div>
                `;
            } else if (typeof value === 'string' || typeof value === 'number') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        <p>${this.escapeHtml(String(value))}</p>
                    </div>
                `;
            } else if (value && typeof value === 'object') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${this.renderMetricSummary(value)}
                    </div>
                `;
            }
        });

        return `
            <div class="section-card">
                <div class="section-card-header">
                    <h3 class="section-card-title">${criterion}</h3>
                    <span class="verdict-badge ${verdictClass}">${verdictLabel}</span>
                </div>
                <div class="section-card-body">
                    ${detailsHtml || '<p class="summary-muted">No details available</p>'}
                </div>
            </div>
        `;
    }

    renderEyeContactSection(section, criterion, verdictLabel, verdictClass) {
        let detailsHtml = '';

        // 1) Overall Assessment (first)
        if (section.overall_assessment) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label">Overall Assessment</h4>
                    <p>${this.escapeHtml(String(section.overall_assessment))}</p>
                </div>
            `;
        }

        // 2) Strong Eye Contact Moments (positive / green)
        const strongMoments = section.strong_eye_contact_moments;
        if (Array.isArray(strongMoments)) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label moment-label-positive">Strong Eye Contact Moments</h4>
                    ${this.renderMomentCards(strongMoments, 'positive')}
                </div>
            `;
        }

        // 3) Look Away Moments (corrective / yellow)
        const lookAwayMoments = section.look_away_moments;
        if (Array.isArray(lookAwayMoments)) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label moment-label-corrective">Look Away Moments</h4>
                    ${this.renderMomentCards(lookAwayMoments, 'corrective')}
                </div>
            `;
        }

        // 4) Eye Contact Percentage
        if (section.eye_contact_percentage != null) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label">Eye Contact Percentage</h4>
                    <p>${this.escapeHtml(String(section.eye_contact_percentage))}</p>
                </div>
            `;
        }

        // 5) Remaining keys (catch-all for any extra fields)
        const handledKeys = new Set(['criterion', 'verdict', 'overall_assessment', 'eye_contact_percentage', 'strong_eye_contact_moments', 'look_away_moments']);
        Object.entries(section).forEach(([key, value]) => {
            if (handledKeys.has(key)) return;
            if (Array.isArray(value)) {
                const hasObjects = value.length > 0 && typeof value[0] === 'object' && value[0] !== null;
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${hasObjects ? this.renderObjectList(value) : this.renderStringList(value, 'None')}
                    </div>
                `;
            } else if (typeof value === 'string' || typeof value === 'number') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        <p>${this.escapeHtml(String(value))}</p>
                    </div>
                `;
            } else if (value && typeof value === 'object') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${this.renderMetricSummary(value)}
                    </div>
                `;
            }
        });

        return `
            <div class="section-card">
                <div class="section-card-header">
                    <h3 class="section-card-title">${criterion}</h3>
                    <span class="verdict-badge ${verdictClass}">${verdictLabel}</span>
                </div>
                <div class="section-card-body">
                    ${detailsHtml || '<p class="summary-muted">No details available</p>'}
                </div>
            </div>
        `;
    }

    renderCalmConfidenceSection(section, criterion, verdictLabel, verdictClass) {
        let detailsHtml = '';

        // 1) Overall Assessment (first)
        if (section.overall_assessment) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label">Overall Assessment</h4>
                    <p>${this.escapeHtml(String(section.overall_assessment))}</p>
                </div>
            `;
        }

        // 2) Confident Moments (positive / green)
        const confidentMoments = section.confident_moments;
        if (Array.isArray(confidentMoments)) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label moment-label-positive">Confident Moments</h4>
                    ${this.renderMomentCards(confidentMoments, 'positive')}
                </div>
            `;
        }

        // 3) Turned Away Events (corrective / yellow)
        const turnedAwayEvents = section.turned_away_events;
        if (Array.isArray(turnedAwayEvents)) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label moment-label-corrective">Turned Away Events</h4>
                    ${this.renderMomentCards(turnedAwayEvents, 'corrective')}
                </div>
            `;
        }

        // 4) Why Facing Matters
        if (section.why_facing_matters) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label">Why Facing Matters</h4>
                    <p>${this.escapeHtml(String(section.why_facing_matters))}</p>
                </div>
            `;
        }

        // 5) Facing Camera Percentage
        if (section.facing_camera_percentage != null) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label">Facing Camera Percentage</h4>
                    <p>${this.escapeHtml(String(section.facing_camera_percentage))}</p>
                </div>
            `;
        }

        // 6) Recommended Stance Adjustments
        const stanceAdj = section.recommended_stance_adjustments;
        if (Array.isArray(stanceAdj) && stanceAdj.length > 0) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label">Recommended Stance Adjustments</h4>
                    ${this.renderStringList(stanceAdj, 'None')}
                </div>
            `;
        }

        // 7) Remaining keys (catch-all)
        const handledKeys = new Set(['criterion', 'verdict', 'overall_assessment', 'confident_moments', 'turned_away_events', 'why_facing_matters', 'facing_camera_percentage', 'recommended_stance_adjustments']);
        Object.entries(section).forEach(([key, value]) => {
            if (handledKeys.has(key)) return;
            if (Array.isArray(value)) {
                const hasObjects = value.length > 0 && typeof value[0] === 'object' && value[0] !== null;
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${hasObjects ? this.renderObjectList(value) : this.renderStringList(value, 'None')}
                    </div>
                `;
            } else if (typeof value === 'string' || typeof value === 'number') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        <p>${this.escapeHtml(String(value))}</p>
                    </div>
                `;
            } else if (value && typeof value === 'object') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${this.renderMetricSummary(value)}
                    </div>
                `;
            }
        });

        return `
            <div class="section-card">
                <div class="section-card-header">
                    <h3 class="section-card-title">${criterion}</h3>
                    <span class="verdict-badge ${verdictClass}">${verdictLabel}</span>
                </div>
                <div class="section-card-body">
                    ${detailsHtml || '<p class="summary-muted">No details available</p>'}
                </div>
            </div>
        `;
    }

    renderEnergyPresenceSection(section, criterion, verdictLabel, verdictClass) {
        let detailsHtml = '';

        // 1) Overall Assessment (first)
        if (section.overall_assessment) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label">Overall Assessment</h4>
                    <p>${this.escapeHtml(String(section.overall_assessment))}</p>
                </div>
            `;
        }

        // 2) Well Delivered Moments (positive / green)
        const wellDelivered = section.well_delivered_moments;
        if (Array.isArray(wellDelivered)) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label moment-label-positive">Well Delivered Moments</h4>
                    ${this.renderMomentCards(wellDelivered, 'positive')}
                </div>
            `;
        }

        // 3) Misaligned Moments (corrective / yellow)
        const misaligned = section.misaligned_moments;
        if (Array.isArray(misaligned)) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label moment-label-corrective">Misaligned Moments</h4>
                    ${this.renderMomentCards(misaligned, 'corrective')}
                </div>
            `;
        }

        // Remaining keys — but explicitly skip removed metrics
        const hiddenKeys = new Set([
            'criterion', 'verdict',
            'overall_assessment',
            'well_delivered_moments', 'misaligned_moments',
            'energy_timeline_summary', 'avg_f0_hz', 'avg_rms_db',
            'pitch_range_hz', 'energy_range_db',
        ]);
        Object.entries(section).forEach(([key, value]) => {
            if (hiddenKeys.has(key)) return;
            if (Array.isArray(value)) {
                const hasObjects = value.length > 0 && typeof value[0] === 'object' && value[0] !== null;
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${hasObjects ? this.renderObjectList(value) : this.renderStringList(value, 'None')}
                    </div>
                `;
            } else if (typeof value === 'string' || typeof value === 'number') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        <p>${this.escapeHtml(String(value))}</p>
                    </div>
                `;
            } else if (value && typeof value === 'object') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${this.renderMetricSummary(value)}
                    </div>
                `;
            }
        });

        return `
            <div class="section-card">
                <div class="section-card-header">
                    <h3 class="section-card-title">${criterion}</h3>
                    <span class="verdict-badge ${verdictClass}">${verdictLabel}</span>
                </div>
                <div class="section-card-body">
                    ${detailsHtml || '<p class="summary-muted">No details available</p>'}
                </div>
            </div>
        `;
    }

    renderPacingEmphasisSection(section, criterion, verdictLabel, verdictClass) {
        let detailsHtml = '';

        // 1) Overall Assessment (first)
        const pacingAssessment = section.overall_assessment;
        if (typeof pacingAssessment === 'string' && pacingAssessment.trim()) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label">Overall Assessment</h4>
                    <p>${this.escapeHtml(pacingAssessment)}</p>
                </div>
            `;
        } else if (Array.isArray(pacingAssessment) && pacingAssessment.length > 0) {
            // Backward compatibility for older v1 payloads.
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label">Overall Assessment</h4>
                    <p>${this.escapeHtml(pacingAssessment.map(v => String(v || '')).join(' '))}</p>
                </div>
            `;
        }
        const hasRenderedPacingAssessment = Boolean(
            (typeof pacingAssessment === 'string' && pacingAssessment.trim()) ||
            (Array.isArray(pacingAssessment) && pacingAssessment.length > 0)
        );

        // 2) Well Paced Sentences (positive / green)
        const wellPaced = section.well_paced_sentences;
        if (Array.isArray(wellPaced)) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label moment-label-positive">Well Paced Sentences</h4>
                    ${this.renderPacingCards(wellPaced, 'positive')}
                </div>
            `;
        }

        // 3) Rushed Important Sentences (corrective / yellow)
        const rushed = section.rushed_important_sentences;
        if (Array.isArray(rushed)) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label moment-label-corrective">Rushed Important Sentences</h4>
                    ${this.renderPacingCards(rushed, 'corrective')}
                </div>
            `;
        }

        // 4) Slow Low Priority Sentences (corrective / yellow)
        const slow = section.slow_low_priority_sentences;
        if (Array.isArray(slow)) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label moment-label-corrective">Slow Low Priority Sentences</h4>
                    ${this.renderPacingCards(slow, 'corrective')}
                </div>
            `;
        }

        // Skip handled keys
        const hiddenKeys = new Set([
            'criterion', 'verdict',
            'well_paced_sentences', 'rushed_important_sentences',
            'slow_low_priority_sentences',
        ]);
        Object.entries(section).forEach(([key, value]) => {
            if (hiddenKeys.has(key)) return;
            if (key === 'overall_assessment' && hasRenderedPacingAssessment) return;
            if (Array.isArray(value)) {
                const hasObjects = value.length > 0 && typeof value[0] === 'object' && value[0] !== null;
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${hasObjects ? this.renderObjectList(value) : this.renderStringList(value, 'None')}
                    </div>
                `;
            } else if (typeof value === 'string' || typeof value === 'number') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        <p>${this.escapeHtml(String(value))}</p>
                    </div>
                `;
            } else if (value && typeof value === 'object') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${this.renderMetricSummary(value)}
                    </div>
                `;
            }
        });

        return `
            <div class="section-card">
                <div class="section-card-header">
                    <h3 class="section-card-title">${criterion}</h3>
                    <span class="verdict-badge ${verdictClass}">${verdictLabel}</span>
                </div>
                <div class="section-card-body">
                    ${detailsHtml || '<p class="summary-muted">No details available</p>'}
                </div>
            </div>
        `;
    }

    /**
     * Renders pacing sentence cards with structure:
     * Time Range → Sentence → Why/Note → WPM → Target WPM
     */
    renderPacingCards(items, colorTheme = 'positive') {
        if (!Array.isArray(items) || items.length === 0) {
            return '<p class="summary-muted">None</p>';
        }

        const cardClass = colorTheme === 'corrective' ? 'moment-card-corrective' : 'moment-card-positive';

        return items.map(item => {
            if (!item || typeof item !== 'object') {
                return `<p>${this.escapeHtml(String(item || ''))}</p>`;
            }

            const timeRange = item.time_range || '';
            const text = item.sentence_text || item.sentence || item.text || '';
            const why = item.why || item.note || '';
            const wpm = item.wpm;
            const targetWpm = item.target_wpm;

            let rows = '';

            // 1) Time Range — primary
            if (timeRange) {
                rows += `<div class="moment-primary"><span class="moment-time">${this.escapeHtml(String(timeRange))}</span></div>`;
            }

            // 2) Sentence — primary
            if (text) {
                rows += `<div class="moment-primary"><span class="moment-text">"${this.escapeHtml(String(text))}"</span></div>`;
            } else {
                rows += `<div class="moment-primary"><span class="moment-text summary-muted">(Transcript unavailable for this segment)</span></div>`;
            }

            // 3) Why/Note — secondary
            if (why) {
                rows += `<div class="moment-secondary"><span class="moment-detail-key">Why:</span> <span class="moment-detail-val">${this.escapeHtml(String(why))}</span></div>`;
            }

            // 4) WPM — secondary metric
            if (wpm != null) {
                rows += `<div class="moment-secondary"><span class="moment-detail-key">WPM:</span> <span class="moment-detail-val">${this.escapeHtml(String(wpm))}</span></div>`;
            }

            // 5) Target WPM — secondary metric
            if (targetWpm != null) {
                rows += `<div class="moment-secondary"><span class="moment-detail-key">Target WPM:</span> <span class="moment-detail-val">${this.escapeHtml(String(targetWpm))}</span></div>`;
            }

            // 6) Remaining fields
            const handledKeys = new Set(['time_range', 'sentence_text', 'sentence', 'text', 'why', 'note', 'wpm', 'target_wpm']);
            Object.entries(item).forEach(([k, v]) => {
                if (handledKeys.has(k)) return;
                const label = this.humanizeKey(k);
                const val = typeof v === 'object' ? JSON.stringify(v) : String(v ?? '');
                rows += `<div class="moment-secondary"><span class="moment-detail-key">${this.escapeHtml(label)}:</span> <span class="moment-detail-val">${this.escapeHtml(val)}</span></div>`;
            });

            return `<div class="obj-item ${cardClass}">${rows}</div>`;
        }).join('');
    }

    renderToneProductSection(section, criterion, verdictLabel, verdictClass) {
        let detailsHtml = '';

        // 1) Overall Assessment (first)
        if (section.overall_assessment) {
            detailsHtml += `
                <div class="criterion-detail">
                    <h4 class="subsection-label">Overall Assessment</h4>
                    <p>${this.escapeHtml(String(section.overall_assessment))}</p>
                </div>
            `;
        }

        // 2) Target Tone Profile → Neutral
        const targetTone = section.target_tone_profile;
        if (Array.isArray(targetTone) && targetTone.length > 0) {
            detailsHtml += `
                <div class="criterion-detail semantic-neutral-box">
                    <h4 class="subsection-label semantic-label-neutral">Target Tone Profile</h4>
                    ${this.renderStringList(targetTone, 'None')}
                </div>
            `;
        }

        // 3) Why This Tone → Neutral
        if (section.why_this_tone) {
            detailsHtml += `
                <div class="criterion-detail semantic-neutral-box">
                    <h4 class="subsection-label semantic-label-neutral">Why This Tone</h4>
                    <p>${this.escapeHtml(String(section.why_this_tone))}</p>
                </div>
            `;
        }

        // 4) Your Actual Tone → Neutral
        if (section.your_actual_tone) {
            detailsHtml += `
                <div class="criterion-detail semantic-neutral-box">
                    <h4 class="subsection-label semantic-label-neutral">Your Actual Tone</h4>
                    <p>${this.escapeHtml(String(section.your_actual_tone))}</p>
                </div>
            `;
        }

        // 5) Alignment Assessment → Negative (evaluation + shortcomings)
        const alignment = section.alignment_assessment;
        if (Array.isArray(alignment) && alignment.length > 0) {
            detailsHtml += `
                <div class="criterion-detail semantic-negative-box">
                    <h4 class="subsection-label semantic-label-negative">Alignment Assessment</h4>
                    ${this.renderStringList(alignment, 'None')}
                </div>
            `;
        }

        // Skip: inferred_product_type (removed), recommended_adjustments, and handled keys
        const hiddenKeys = new Set([
            'criterion', 'verdict',
            'overall_assessment',
            'target_tone_profile', 'why_this_tone', 'your_actual_tone',
            'alignment_assessment', 'inferred_product_type', 'recommended_adjustments',
        ]);
        Object.entries(section).forEach(([key, value]) => {
            if (hiddenKeys.has(key)) return;
            if (Array.isArray(value)) {
                const hasObjects = value.length > 0 && typeof value[0] === 'object' && value[0] !== null;
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${hasObjects ? this.renderObjectList(value) : this.renderStringList(value, 'None')}
                    </div>
                `;
            } else if (typeof value === 'string' || typeof value === 'number') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        <p>${this.escapeHtml(String(value))}</p>
                    </div>
                `;
            } else if (value && typeof value === 'object') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${this.renderMetricSummary(value)}
                    </div>
                `;
            }
        });

        return `
            <div class="section-card">
                <div class="section-card-header">
                    <h3 class="section-card-title">${criterion}</h3>
                    <span class="verdict-badge ${verdictClass}">${verdictLabel}</span>
                </div>
                <div class="section-card-body">
                    ${detailsHtml || '<p class="summary-muted">No details available</p>'}
                </div>
            </div>
        `;
    }

    /**
     * Custom renderer for Clarity & Conviction section (Round 2).
     * Ordered: Diagnosis (neg) → Timing Signals (neutral) → What Investors Felt (neg)
     *        → What To Fix Next (neg) → Rewrite Lines (neutral) → catch-all
     */
    renderClarityConvictionSection(section, criterion, verdictLabel, verdictClass) {
        let detailsHtml = '';
        const handledKeys = new Set(['criterion', 'verdict']);

        const renderSub = (key, boxCls, labelCls) => {
            const value = section[key];
            if (value == null) return;
            handledKeys.add(key);
            const label = this.humanizeKey(key);

            if (Array.isArray(value)) {
                const hasObjects = value.length > 0 && typeof value[0] === 'object' && value[0] !== null;
                detailsHtml += `
                    <div class="criterion-detail ${boxCls}">
                        <h4 class="subsection-label ${labelCls}">${this.escapeHtml(label)}</h4>
                        ${hasObjects ? this.renderObjectList(value) : this.renderStringList(value, 'None')}
                    </div>
                `;
            } else if (typeof value === 'string' || typeof value === 'number') {
                detailsHtml += `
                    <div class="criterion-detail ${boxCls}">
                        <h4 class="subsection-label ${labelCls}">${this.escapeHtml(label)}</h4>
                        <p>${this.escapeHtml(String(value))}</p>
                    </div>
                `;
            } else if (value && typeof value === 'object') {
                detailsHtml += `
                    <div class="criterion-detail ${boxCls}">
                        <h4 class="subsection-label ${labelCls}">${this.escapeHtml(label)}</h4>
                        ${this.renderMetricSummary(value)}
                    </div>
                `;
            }
        };

        // 1) Diagnosis → Negative
        renderSub('diagnosis', 'semantic-negative-box', 'semantic-label-negative');

        // 2) Timing Signals Used → Neutral
        renderSub('timing_signals_used', 'semantic-neutral-box', 'semantic-label-neutral');

        // 3) What Investors Felt → Negative
        renderSub('what_investors_felt', 'semantic-negative-box', 'semantic-label-negative');

        // 4) What To Fix Next → Negative
        renderSub('what_to_fix_next', 'semantic-negative-box', 'semantic-label-negative');

        // 5) Rewrite Lines To Increase Conviction → Neutral (actionable)
        renderSub('rewrite_lines_to_increase_conviction', 'semantic-neutral-box', 'semantic-label-neutral');

        // Catch-all for remaining fields
        Object.entries(section).forEach(([key, value]) => {
            if (handledKeys.has(key)) return;
            if (Array.isArray(value)) {
                const hasObjects = value.length > 0 && typeof value[0] === 'object' && value[0] !== null;
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${hasObjects ? this.renderObjectList(value) : this.renderStringList(value, 'None')}
                    </div>
                `;
            } else if (typeof value === 'string' || typeof value === 'number') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        <p>${this.escapeHtml(String(value))}</p>
                    </div>
                `;
            } else if (value && typeof value === 'object') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${this.renderMetricSummary(value)}
                    </div>
                `;
            }
        });

        return `
            <div class="section-card">
                <div class="section-card-header">
                    <h3 class="section-card-title">${criterion}</h3>
                    <span class="verdict-badge ${verdictClass}">${verdictLabel}</span>
                </div>
                <div class="section-card-body">
                    ${detailsHtml || '<p class="summary-muted">No details available</p>'}
                </div>
            </div>
        `;
    }

    /**
     * Shared renderer for moment cards across all sections.
     * Renders each card in the order: Time Range → Text → Fix (if present) → Why.
     * @param {Array} moments - Array of moment objects
     * @param {'positive'|'corrective'} colorTheme - Visual theme for the card
     */
    renderMomentCards(moments, colorTheme = 'positive') {
        if (!Array.isArray(moments) || moments.length === 0) {
            return '<p class="summary-muted">None</p>';
        }

        const cardClass = colorTheme === 'corrective' ? 'moment-card-corrective' : 'moment-card-positive';

        return moments.map(item => {
            if (!item || typeof item !== 'object') {
                return `<p>${this.escapeHtml(String(item || ''))}</p>`;
            }

            // Extract known fields for ordered rendering
            const timeRange = item.time_range || '';
            const text = item.sentence_text || item.text || '';
            const fix = item.fix || '';
            const why = item.why || '';

            let rows = '';

            // 1) Time Range — primary emphasis
            if (timeRange) {
                rows += `<div class="moment-primary"><span class="moment-time">${this.escapeHtml(String(timeRange))}</span></div>`;
            }

            // 2) Text / What was said — primary emphasis
            if (text) {
                rows += `<div class="moment-primary"><span class="moment-text">"${this.escapeHtml(String(text))}"</span></div>`;
            } else {
                rows += `<div class="moment-primary"><span class="moment-text summary-muted">(Transcript unavailable for this segment)</span></div>`;
            }

            // 3) Fix — secondary detail (if present)
            if (fix) {
                rows += `<div class="moment-secondary"><span class="moment-detail-key">Fix:</span> <span class="moment-detail-val">${this.escapeHtml(String(fix))}</span></div>`;
            }

            // 4) Why — secondary detail
            if (why) {
                rows += `<div class="moment-secondary"><span class="moment-detail-key">Why:</span> <span class="moment-detail-val">${this.escapeHtml(String(why))}</span></div>`;
            }

            // 5) Any remaining fields not already rendered
            const handledMomentKeys = new Set(['time_range', 'sentence_text', 'text', 'why', 'fix']);
            Object.entries(item).forEach(([k, v]) => {
                if (handledMomentKeys.has(k)) return;
                const label = this.humanizeKey(k);
                const val = typeof v === 'object' ? JSON.stringify(v) : String(v ?? '');
                rows += `<div class="moment-secondary"><span class="moment-detail-key">${this.escapeHtml(label)}:</span> <span class="moment-detail-val">${this.escapeHtml(val)}</span></div>`;
            });

            return `<div class="obj-item ${cardClass}">${rows}</div>`;
        }).join('');
    }

    /**
     * Returns true if the criterion belongs to a strategic content section
     * (Problem Framing, Value Proposition, Differentiation, Business Model, Market Potential).
     */
    _isStrategicSection(criterion) {
        const prefixes = [
            'Problem Framing',
            'Value Proposition',
            'Differentiation & Defensibility',
            'Business Model',
            'Market Potential',
        ];
        return prefixes.some(p => criterion.startsWith(p));
    }

    /**
     * Custom renderer for strategic content sections.
     *
     * Enforced internal order:
     *   1. Neutral framing summaries (Diagnosis, Credible Market Framing)
     *   2. Evidence Quotes            → GREEN
     *   3. Missing Information / Vague → YELLOW
     *   4. What Investors Will Question → YELLOW
     *   5. Recommended Rewrites        → BLUE (always last)
     *
     * Color semantics are SEPARATE from the performance green/yellow
     * used by Posture & Eye-Contact (Stable vs Unstable).
     */
    renderStrategicSection(section, criterion, verdictLabel, verdictClass) {
        let detailsHtml = '';
        const handledKeys = new Set(['criterion', 'verdict']);

        // Helper: render one sub-section with optional color box / label class
        const renderSub = (key, boxCls, labelCls) => {
            const value = section[key];
            if (value == null) return;
            handledKeys.add(key);

            const label = this.humanizeKey(key);

            if (Array.isArray(value)) {
                const hasObjects = value.length > 0 && typeof value[0] === 'object' && value[0] !== null;
                detailsHtml += `
                    <div class="criterion-detail ${boxCls}">
                        <h4 class="subsection-label ${labelCls}">${this.escapeHtml(label)}</h4>
                        ${hasObjects ? this.renderObjectList(value) : this.renderStringList(value, 'None')}
                    </div>
                `;
            } else if (typeof value === 'string' || typeof value === 'number') {
                detailsHtml += `
                    <div class="criterion-detail ${boxCls}">
                        <h4 class="subsection-label ${labelCls}">${this.escapeHtml(label)}</h4>
                        <p>${this.escapeHtml(String(value))}</p>
                    </div>
                `;
            } else if (value && typeof value === 'object') {
                detailsHtml += `
                    <div class="criterion-detail ${boxCls}">
                        <h4 class="subsection-label ${labelCls}">${this.escapeHtml(label)}</h4>
                        ${this.renderMetricSummary(value)}
                    </div>
                `;
            }
        };

        // ── 1) Diagnosis → Negative (yellow/amber), Credible Market Framing → Neutral ──
        renderSub('diagnosis', 'semantic-negative-box', 'semantic-label-negative');
        renderSub('credible_market_framing', 'semantic-neutral-box', 'semantic-label-neutral');
        renderSub('timing_signals_used', 'semantic-neutral-box', 'semantic-label-neutral');

        // ── 2) Evidence Quotes → GREEN ──
        for (const key of ['evidence_quotes']) {
            renderSub(key, 'strategic-evidence-box', 'strategic-label-evidence');
        }

        // ── 3) Missing Information / Missing or Vague → YELLOW ──
        for (const key of ['missing_information', 'missing_or_vague']) {
            renderSub(key, 'strategic-missing-box', 'strategic-label-missing');
        }

        // ── 4) What Investors Will Question → YELLOW ──
        for (const key of ['what_investors_will_question', 'what_investors_need_to_hear']) {
            renderSub(key, 'strategic-missing-box', 'strategic-label-missing');
        }

        // ── 5) Recommended Rewrites → BLUE (last) ──
        for (const key of ['recommended_rewrites', 'recommended_lines']) {
            renderSub(key, 'strategic-rewrite-box', 'strategic-label-rewrite');
        }

        // ── 6) Catch-all for any remaining unlisted fields ──
        Object.entries(section).forEach(([key, value]) => {
            if (handledKeys.has(key)) return;

            if (Array.isArray(value)) {
                const hasObjects = value.length > 0 && typeof value[0] === 'object' && value[0] !== null;
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${hasObjects ? this.renderObjectList(value) : this.renderStringList(value, 'None')}
                    </div>
                `;
            } else if (typeof value === 'string' || typeof value === 'number') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        <p>${this.escapeHtml(String(value))}</p>
                    </div>
                `;
            } else if (value && typeof value === 'object') {
                detailsHtml += `
                    <div class="criterion-detail">
                        <h4 class="subsection-label">${this.escapeHtml(this.humanizeKey(key))}</h4>
                        ${this.renderMetricSummary(value)}
                    </div>
                `;
            }
        });

        return `
            <div class="section-card">
                <div class="section-card-header">
                    <h3 class="section-card-title">${criterion}</h3>
                    <span class="verdict-badge ${verdictClass}">${verdictLabel}</span>
                </div>
                <div class="section-card-body">
                    ${detailsHtml || '<p class="summary-muted">No details available</p>'}
                </div>
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

    renderObjectList(items) {
        if (!Array.isArray(items) || items.length === 0) {
            return '<p class="summary-muted">None</p>';
        }

        return items.map(item => {
            if (!item || typeof item !== 'object') {
                return `<p>${this.escapeHtml(String(item || ''))}</p>`;
            }

            const rows = Object.entries(item).map(([k, v]) => {
                const label = this.humanizeKey(k);
                const val = typeof v === 'object' ? JSON.stringify(v) : String(v ?? '');
                return `<div class="obj-row"><span class="obj-key">${this.escapeHtml(label)}:</span> <span class="obj-val">${this.escapeHtml(val)}</span></div>`;
            }).join('');

            return `<div class="obj-item">${rows}</div>`;
        }).join('');
    }

    renderMetricSummary(obj) {
        if (!obj || typeof obj !== 'object') {
            return '<p class="summary-muted">No data</p>';
        }

        const rows = Object.entries(obj).map(([k, v]) => {
            const label = this.humanizeKey(k);

            // Special handling for top_fillers (array of {token, count} objects)
            if (k === 'top_fillers' && Array.isArray(v)) {
                const chips = v.map(f => {
                    if (f && typeof f === 'object') {
                        const word = f.token || f.word || f.filler || '';
                        const count = f.count != null ? f.count : '';
                        return `<span class="filler-chip">${this.escapeHtml(String(word))}${count !== '' ? ` <span class="filler-chip-count">&times;${this.escapeHtml(String(count))}</span>` : ''}</span>`;
                    }
                    return `<span class="filler-chip">${this.escapeHtml(String(f))}</span>`;
                }).join(' ');
                return `<div class="metric-row metric-row-wide"><span class="metric-label">${this.escapeHtml(label)}</span><span class="metric-value filler-chips">${chips}</span></div>`;
            }

            // Handle arrays / objects gracefully (prevent [object Object])
            let val;
            if (typeof v === 'number') {
                val = v.toFixed(1);
            } else if (Array.isArray(v)) {
                val = v.map(item => typeof item === 'object' ? JSON.stringify(item) : String(item ?? '')).join(', ');
            } else if (v && typeof v === 'object') {
                val = JSON.stringify(v);
            } else {
                val = String(v ?? '');
            }
            return `<div class="metric-row"><span class="metric-label">${this.escapeHtml(label)}</span><span class="metric-value">${this.escapeHtml(val)}</span></div>`;
        }).join('');

        return `<div class="metric-summary">${rows}</div>`;
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
            jobMeta.textContent = 'No active session.';
            return;
        }

        const status = String(job.status || 'unknown')
            .replaceAll('_', ' ')
            .replace(/\b\w/g, (m) => m.toUpperCase());
        const progress = typeof job.progress === 'number' ? `${job.progress}%` : 'N/A';
        jobMeta.textContent = `Session status: ${status} (${progress})`;
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
        const jobMeta = document.getElementById('job-meta');
        const hasTranscript = !!this.transcriptionData;
        const isActiveProcessing = ['uploading', 'transcribing', 'feedbacking'].includes(this.stage);

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

        if (jobMeta) {
            jobMeta.classList.toggle('hidden', isActiveProcessing);
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

    _clampPercent(value, fallback = 0) {
        const numeric = Number(value);
        if (Number.isFinite(numeric)) {
            return Math.max(0, Math.min(100, numeric));
        }
        return Math.max(0, Math.min(100, Number(fallback) || 0));
    }

    _transcriptionStageLabel(status) {
        const labels = {
            queued: 'Queued',
            deck_processing: 'Preparing deck',
            transcribing: 'Preparing audio',
            uploading_audio_to_gcs: 'Uploading audio',
            stt_batch_recognize: 'Submitting speech job',
            waiting_for_stt: 'Recognizing speech',
            parsing_results: 'Parsing results',
            computing_metrics: 'Computing metrics',
            writing_artifacts: 'Writing artifacts',
            done: 'Completed',
        };
        return labels[status] || 'Processing';
    }

    _transcriptionStageSubtitle(status) {
        const subtitles = {
            queued: 'Your pitch is queued and will start in a moment.',
            deck_processing: 'Extracting deck context for richer analysis.',
            transcribing: 'Converting recording for speech recognition.',
            uploading_audio_to_gcs: 'Uploading audio to secure processing.',
            stt_batch_recognize: 'Submitting transcription workload.',
            waiting_for_stt: 'Speech model is decoding your recording.',
            parsing_results: 'Parsing transcript, segments, and word timings.',
            computing_metrics: 'Building tone and body-language metrics.',
            writing_artifacts: 'Saving transcript artifacts and metadata.',
            done: 'Transcription is complete.',
        };
        return subtitles[status] || 'Processing your recording.';
    }

    _setTranscriptionProgressStatus(job) {
        const status = String(job?.status || 'transcribing');
        const pct = this._clampPercent(job?.progress, this.transcriptionProgressPct);
        this.transcriptionProgressPct = pct;
        const label = `${Math.round(pct)}% ${this._transcriptionStageLabel(status)}`;
        this.setRecordingStatus(label, 'info', true, {
            progress: {
                title: 'Transcription in Progress',
                subtitle: this._transcriptionStageSubtitle(status),
                percent: pct,
                label,
            },
        });
    }

    _completedFeedbackRounds(job) {
        if (!job || typeof job !== 'object') {
            return 0;
        }
        let done = 0;
        if (this.hasRoundFeedback(job, 1)) done += 1;
        if (this.hasRoundFeedback(job, 2)) done += 1;
        if (this.hasRoundFeedback(job, 3)) done += 1;
        if (this.hasRoundFeedback(job, 4)) done += 1;
        if (this.hasRoundFeedback(job, 5)) done += 1;
        return done;
    }

    _roundSubtitle(round) {
        const map = {
            1: 'Round 1: Product Fundamentals',
            2: 'Round 2: Delivery & Business',
            3: 'Round 3: Vocal Tone & Energy',
            4: 'Round 4: Body Language & Presence',
            5: 'Round 5: Overview & Deck Evaluation',
        };
        return map[round] || 'Generating feedback';
    }

    _setFeedbackProgressStatus({ round, job = null, label = '', progress = null }) {
        const safeRound = Math.max(1, Math.min(5, Number(round) || 1));
        const segmentSize = 20;
        const segmentStart = (safeRound - 1) * segmentSize;
        const segmentEnd = safeRound * segmentSize;
        if (this.feedbackActiveRound !== safeRound) {
            this.feedbackActiveRound = safeRound;
            this.feedbackPulseTick = 0;
            this.feedbackProgressPct = Math.max(this.feedbackProgressPct, segmentStart);
        }

        if (typeof progress === 'number') {
            this.feedbackProgressPct = this._clampPercent(progress, this.feedbackProgressPct);
        } else if (job) {
            if (this.hasRoundFeedback(job, safeRound)) {
                this.feedbackProgressPct = Math.max(this.feedbackProgressPct, segmentEnd);
            } else {
                this.feedbackPulseTick += 1;
                const pulseWithinSegment = Math.min(this.feedbackPulseTick * 2.2, segmentSize - 2);
                const animated = segmentStart + pulseWithinSegment;
                const cap = segmentEnd - 2;
                this.feedbackProgressPct = Math.max(
                    this.feedbackProgressPct,
                    this._clampPercent(Math.min(animated, cap), segmentStart),
                );
            }
        } else {
            this.feedbackProgressPct = Math.max(this.feedbackProgressPct, segmentStart);
        }

        const pct = this._clampPercent(this.feedbackProgressPct, segmentStart);
        const autoLabel = (job && this.hasRoundFeedback(job, safeRound))
            ? `${Math.round(pct)}% Round ${safeRound} feedback is done`
            : `${Math.round(pct)}% Processing round ${safeRound}`;
        const progressText = label || autoLabel;
        this.setSummaryStatus(progressText, 'info', true, {
            progress: {
                title: 'Feedback Generation',
                subtitle: this._roundSubtitle(safeRound),
                percent: pct,
                label: progressText,
            },
        });
    }

    setRecordingStatus(message, type, loading, options = null) {
        const status = document.getElementById('recording-status');
        this.setStatusElement(status, message, type, loading, options);
    }

    setSummaryStatus(message, type, loading, options = null) {
        const status = document.getElementById('summary-status');
        this.setStatusElement(status, message, type, loading, options);
    }

    setStatusElement(element, message, type, loading, options = null) {
        if (!element) {
            return;
        }

        const text = (message || '').trim();
        if (!text) {
            element.textContent = '';
            element.className = 'status-message';
            return;
        }

        const safeType = ['success', 'error', 'info', 'warning'].includes(type) ? type : 'info';
        const progressData = options && typeof options === 'object' ? options.progress : null;

        if (progressData && typeof progressData.percent === 'number') {
            const percent = Math.round(this._clampPercent(progressData.percent));
            const title = this.escapeHtml(progressData.title || 'In Progress');
            const subtitle = this.escapeHtml(progressData.subtitle || text);
            const label = this.escapeHtml(progressData.label || `${percent}%`);

            element.className = `status-message active ${safeType} progress`;
            element.innerHTML = `
                <div class="status-progress-head">
                    <p class="status-progress-title">${title}</p>
                    <p class="status-progress-subtitle">${subtitle}</p>
                </div>
                <div class="status-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}">
                    <div class="status-progress-fill" style="width: ${percent}%"></div>
                    <span class="status-progress-text">${label}</span>
                </div>
            `;
            return;
        }

        element.textContent = text;
        element.className = `status-message active ${safeType}${loading ? ' loading' : ''}`;
    }

    /**
     * Show a distance-status indicator on the camera container.
     * @param {'ok'|'too_close'|'too_far'} status
     * @param {string} message
     */
    _showDistanceFeedback(status, message) {
        const container = document.getElementById('camera-container');
        if (!container) return;

        // Remove any previous feedback
        container.querySelectorAll('.distance-feedback').forEach(el => el.remove());

        const el = document.createElement('div');
        el.className = `distance-feedback ${status === 'ok' ? 'distance-ok' : 'distance-warn'}`;
        el.innerHTML = `<span class="distance-icon">${status === 'ok' ? '✅' : '⚠️'}</span> <span>${message}</span>`;
        container.appendChild(el);

        // Auto-hide after 6 seconds
        setTimeout(() => el.remove(), 6000);
    }

    /** Remove the distance guide and banner overlays. */
    _hideDistanceOverlays() {
        const guide = document.getElementById('distance-guide');
        const banner = document.getElementById('distance-banner');
        if (guide) guide.classList.remove('visible');
        if (banner) {
            banner.classList.remove('visible', 'recording-reminder');
        }
        // Remove any lingering feedback badges
        const container = document.getElementById('camera-container');
        if (container) {
            container.querySelectorAll('.distance-feedback').forEach(el => el.remove());
        }
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
        this.round3RequestedForJobId = null;
        this.round4RequestedForJobId = null;
        this.round5RequestedForJobId = null;
        this.transcriptionProgressPct = 0;
        this.feedbackProgressPct = 0;
        this.feedbackPulseTick = 0;
        this.feedbackActiveRound = null;
        this._dismissNoDeckModal(false);
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
        this._hideDistanceOverlays();
        this.applyStudioLayout();

        // Keep record controls synced after reset (deck is optional).
        this._syncDeckRecordingState();
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
        const keyMap = { 1: 'feedback_round_1', 2: 'feedback_round_2', 3: 'feedback_round_3', 4: 'feedback_round_4', 5: 'feedback_round_5' };
        const key = keyMap[roundNumber];
        if (!key) return false;
        const payload = job[key];
        return !!(payload && typeof payload === 'object');
    }

    getFeedbackFromJob(job) {
        if (!job || typeof job !== 'object') {
            return null;
        }

        const round1 = this.hasRoundFeedback(job, 1) ? job.feedback_round_1 : null;
        const round2 = this.hasRoundFeedback(job, 2) ? job.feedback_round_2 : null;
        const round3 = this.hasRoundFeedback(job, 3) ? job.feedback_round_3 : null;
        const round4 = this.hasRoundFeedback(job, 4) ? job.feedback_round_4 : null;
        const round5 = this.hasRoundFeedback(job, 5) ? job.feedback_round_5 : null;
        if (round1 || round2 || round3 || round4 || round5) {
            return { round1, round2, round3, round4, round5, legacy: null };
        }

        const legacy = job.feedback || job.summary;
        if (!legacy || typeof legacy !== 'object') {
            return { round1: null, round2: null, round3: null, round4: null, round5: null, legacy: null };
        }
        return { round1: null, round2: null, round3: null, round4: null, round5: null, legacy };
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
