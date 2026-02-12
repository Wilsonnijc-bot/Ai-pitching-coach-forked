// Main application logic and routing

import { AudioRecorder, formatTime } from './recorder.js';
import { DeckUploader } from './deckUpload.js';
import { transcribeAudio } from './api.js';

class App {
    constructor() {
        this.currentRoute = 'home';
        this.audioRecorder = null;
        this.deckUploader = null;
        this.transcriptionData = null;
        
        this.init();
    }

    init() {
        this.setupRouter();
        this.setupSidebar();
        this.handleInitialRoute();
    }

    setupRouter() {
        // Handle hash changes
        window.addEventListener('hashchange', () => {
            this.handleRoute();
        });

        // Handle navigation clicks
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

        // Close sidebar on navigation (mobile)
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

        // Update active nav item
        document.querySelectorAll('.nav-item').forEach(item => {
            item.classList.remove('active');
            if (item.dataset.route === route) {
                item.classList.add('active');
            }
        });

        // Render appropriate page
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

                <div class="studio-grid">
                    <!-- Pitch Deck Upload Card -->
                    <div class="card" id="deck-upload-card">
                        <h2 class="card-title">Upload your pitch deck</h2>
                        
                        <div class="upload-area">
                            <img src="https://mgx-backend-cdn.metadl.com/generate/images/960660/2026-02-11/e9275271-5cc3-4538-9599-50f4e1bc9b2f.png" 
                                 alt="Upload" class="upload-illustration">
                            <p class="upload-text">Drag and drop your deck here, or click to browse</p>
                            <p class="upload-hint">Supports .pdf, .ppt, .pptx (max 50MB)</p>
                            <input type="file" class="file-input" accept=".pdf,.ppt,.pptx">
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

                    <!-- Recording Card -->
                    <div class="card" id="recording-card">
                        <h2 class="card-title">Record your pitch</h2>
                        
                        <div class="recording-area">
                            <img src="https://mgx-backend-cdn.metadl.com/generate/images/960660/2026-02-11/630938a7-3ec3-4e35-a03a-123708d88ef1.png" 
                                 alt="Recording" class="recording-visual">
                            
                            <button class="record-button" id="record-btn">
                                <div class="record-icon"></div>
                                <span id="record-text">Start</span>
                            </button>
                            
                            <div class="timer" id="timer">00:00</div>
                        </div>

                        <div class="status-message" id="recording-status"></div>
                    </div>
                </div>

                <!-- Transcription Results -->
                <div class="card results-panel" id="results-panel">
                    <div class="results-header">
                        <h2 class="results-title">Transcription Results</h2>
                        <button class="btn btn-secondary" id="copy-transcript-btn">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                            </svg>
                            Copy transcript
                        </button>
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
                </div>
            </div>
        `;

        // Initialize deck uploader
        this.deckUploader = new DeckUploader('deck-upload-card');

        // Initialize recording functionality
        this.setupRecording();

        // Setup tabs
        this.setupTabs();
    }

    setupRecording() {
        const recordBtn = document.getElementById('record-btn');
        const recordText = document.getElementById('record-text');
        const timer = document.getElementById('timer');
        const status = document.getElementById('recording-status');

        let isRecording = false;
        let recordingSeconds = 0;

        recordBtn.addEventListener('click', async () => {
            if (!isRecording) {
                // Start recording
                try {
                    status.textContent = 'Requesting microphone permission...';
                    status.className = 'status-message active info';

                    if (!this.audioRecorder) {
                        this.audioRecorder = new AudioRecorder();
                        await this.audioRecorder.initialize();
                    }

                    status.textContent = 'Recording...';
                    recordBtn.classList.add('recording');
                    recordText.textContent = 'Stop';
                    isRecording = true;
                    recordingSeconds = 0;

                    this.audioRecorder.startRecording(
                        (seconds) => {
                            recordingSeconds = seconds;
                            timer.textContent = formatTime(seconds);
                        },
                        () => {
                            // Max duration reached
                            status.textContent = 'Maximum recording time (5 minutes) reached. Stopping...';
                            status.className = 'status-message active info';
                            recordBtn.click(); // Trigger stop
                        }
                    );

                } catch (error) {
                    console.error('Recording error:', error);
                    status.textContent = `Microphone access denied. Please allow microphone access in your browser settings and try again.`;
                    status.className = 'status-message active error';
                    isRecording = false;
                }

            } else {
                // Stop recording
                try {
                    recordBtn.disabled = true;
                    status.textContent = 'Stopping recording...';
                    status.className = 'status-message active info';

                    const audioBlob = await this.audioRecorder.stopRecording();
                    
                    recordBtn.classList.remove('recording');
                    recordText.textContent = 'Start';
                    isRecording = false;
                    timer.textContent = '00:00';

                    // Upload and transcribe
                    await this.handleTranscription(audioBlob);

                } catch (error) {
                    console.error('Stop recording error:', error);
                    status.textContent = `Error stopping recording: ${error.message}`;
                    status.className = 'status-message active error';
                } finally {
                    recordBtn.disabled = false;
                }
            }
        });
    }

    async handleTranscription(audioBlob) {
        const status = document.getElementById('recording-status');
        const recordBtn = document.getElementById('record-btn');

        try {
            recordBtn.disabled = true;
            status.textContent = 'Uploading audio...';
            status.className = 'status-message active info';

            const result = await transcribeAudio(audioBlob);

            status.textContent = 'Transcription complete!';
            status.className = 'status-message active success';

            this.transcriptionData = result;
            this.displayTranscription(result);

        } catch (error) {
            console.error('Transcription error:', error);
            status.textContent = `Transcription failed: ${error.message}. Please ensure the backend is running at http://localhost:8000`;
            status.className = 'status-message active error';
        } finally {
            recordBtn.disabled = false;
        }
    }

    displayTranscription(data) {
        const resultsPanel = document.getElementById('results-panel');
        const transcriptText = document.getElementById('transcript-text');
        const segmentsTbody = document.getElementById('segments-tbody');
        const wordsTbody = document.getElementById('words-tbody');

        // Show results panel
        resultsPanel.classList.add('active');

        // Display full transcript
        transcriptText.textContent = data.full_text || 'No transcript available';

        // Display segments
        segmentsTbody.innerHTML = '';
        if (data.segments && data.segments.length > 0) {
            data.segments.forEach(segment => {
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td>${this.formatTimestamp(segment.start)}</td>
                    <td>${this.formatTimestamp(segment.end)}</td>
                    <td>${segment.text}</td>
                `;
                segmentsTbody.appendChild(row);
            });
        } else {
            segmentsTbody.innerHTML = '<tr><td colspan="3">No segments available</td></tr>';
        }

        // Display words
        wordsTbody.innerHTML = '';
        if (data.words && data.words.length > 0) {
            data.words.forEach(word => {
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td>${this.formatTimestamp(word.start)}</td>
                    <td>${this.formatTimestamp(word.end)}</td>
                    <td>${word.word}</td>
                `;
                wordsTbody.appendChild(row);
            });
        } else {
            wordsTbody.innerHTML = '<tr><td colspan="3">No word-level data available</td></tr>';
        }

        // Setup copy button
        const copyBtn = document.getElementById('copy-transcript-btn');
        copyBtn.onclick = () => {
            navigator.clipboard.writeText(data.full_text).then(() => {
                const originalText = copyBtn.innerHTML;
                copyBtn.innerHTML = '<span>✓ Copied!</span>';
                setTimeout(() => {
                    copyBtn.innerHTML = originalText;
                }, 2000);
            });
        };

        // Scroll to results
        resultsPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    setupTabs() {
        const tabs = document.querySelectorAll('.tab');
        const tabContents = document.querySelectorAll('.tab-content');

        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                const targetTab = tab.dataset.tab;

                // Remove active class from all tabs and contents
                tabs.forEach(t => t.classList.remove('active'));
                tabContents.forEach(tc => tc.classList.remove('active'));

                // Add active class to clicked tab and corresponding content
                tab.classList.add('active');
                document.getElementById(`${targetTab}-tab`).classList.add('active');
            });
        });
    }

    formatTimestamp(seconds) {
        const mins = Math.floor(seconds / 60);
        const secs = (seconds % 60).toFixed(2);
        return `${String(mins).padStart(2, '0')}:${String(secs).padStart(5, '2')}`;
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

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    new App();
});