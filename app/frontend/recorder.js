// Audio recording functionality using MediaRecorder API

export class AudioRecorder {
    constructor() {
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.stream = null;
        this.startTime = null;
        this.timerInterval = null;
        this.maxDurationTriggered = false;
        this.maxDuration = 5 * 60 * 1000; // 5 minutes in milliseconds
    }

    /**
     * Request microphone permission and initialize MediaRecorder
     * @returns {Promise<boolean>} Success status
     */
    async initialize() {
        try {
            this.stream = await navigator.mediaDevices.getUserMedia({ 
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    sampleRate: 44100
                } 
            });

            // Try preferred mimeType, fallback to supported types
            const mimeTypes = [
                'audio/webm;codecs=opus',
                'audio/webm',
                'audio/ogg;codecs=opus',
                'audio/mp4'
            ];

            let selectedMimeType = '';
            for (const mimeType of mimeTypes) {
                if (MediaRecorder.isTypeSupported(mimeType)) {
                    selectedMimeType = mimeType;
                    break;
                }
            }

            if (!selectedMimeType) {
                throw new Error('No supported audio MIME type found');
            }

            this.mediaRecorder = new MediaRecorder(this.stream, {
                mimeType: selectedMimeType
            });

            this.mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    this.audioChunks.push(event.data);
                }
            };

            return true;
        } catch (error) {
            console.error('Microphone initialization error:', error);
            throw error;
        }
    }

    /**
     * Start recording audio
     * @param {Function} onTimerUpdate - Callback for timer updates (seconds)
     * @param {Function} onMaxDuration - Callback when max duration reached
     */
    startRecording(onTimerUpdate, onMaxDuration) {
        if (!this.mediaRecorder) {
            throw new Error('Recorder not initialized');
        }
        if (this.mediaRecorder.state === 'recording') {
            throw new Error('Recorder is already recording');
        }

        this.audioChunks = [];
        this.startTime = Date.now();
        this.maxDurationTriggered = false;
        this.mediaRecorder.start(250); // Collect data frequently for responsive stop/upload

        // Update timer every second
        this.timerInterval = setInterval(() => {
            const elapsed = Date.now() - this.startTime;
            const seconds = Math.floor(elapsed / 1000);
            
            if (onTimerUpdate) {
                onTimerUpdate(seconds);
            }

            // 5:00 is max cap. Let UI trigger stop flow for consistent upload handling.
            if (elapsed >= this.maxDuration && !this.maxDurationTriggered) {
                this.maxDurationTriggered = true;
                clearInterval(this.timerInterval);
                if (onTimerUpdate) {
                    onTimerUpdate(Math.floor(this.maxDuration / 1000));
                }
                if (onMaxDuration) {
                    onMaxDuration();
                }
            }
        }, 250);
    }

    /**
     * Stop recording and return audio blob
     * @returns {Promise<Blob>} Recorded audio blob
     */
    stopRecording() {
        return new Promise((resolve, reject) => {
            if (!this.mediaRecorder || this.mediaRecorder.state === 'inactive') {
                reject(new Error('Recorder not active'));
                return;
            }

            clearInterval(this.timerInterval);

            this.mediaRecorder.onstop = () => {
                const audioBlob = new Blob(this.audioChunks, { 
                    type: this.mediaRecorder.mimeType 
                });
                resolve(audioBlob);
            };

            this.mediaRecorder.stop();
        });
    }

    /**
     * Returns elapsed recording seconds based on start timestamp.
     * @returns {number}
     */
    getElapsedSeconds() {
        if (!this.startTime) {
            return 0;
        }
        return Math.floor((Date.now() - this.startTime) / 1000);
    }

    /**
     * Clean up resources
     */
    cleanup() {
        if (this.timerInterval) {
            clearInterval(this.timerInterval);
        }

        if (this.stream) {
            this.stream.getTracks().forEach(track => track.stop());
        }

        this.mediaRecorder = null;
        this.audioChunks = [];
        this.stream = null;
        this.startTime = null;
        this.maxDurationTriggered = false;
    }

    /**
     * Get current recording state
     * @returns {string} Recording state
     */
    getState() {
        return this.mediaRecorder ? this.mediaRecorder.state : 'inactive';
    }
}

/**
 * Format seconds to MM:SS
 * @param {number} seconds
 * @returns {string}
 */
export function formatTime(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}
