// API configuration and wrapper functions
const isLocalFrontendDev =
    ['localhost', '127.0.0.1'].includes(window.location.hostname) &&
    window.location.port === '5173';
const API_BASE_URL = isLocalFrontendDev ? 'http://localhost:8000' : '';

async function readErrorDetail(response, fallbackMessage) {
    const contentType = response.headers.get('content-type') || '';
    try {
        if (contentType.includes('application/json')) {
            const payload = await response.json();
            if (payload && payload.detail) {
                return String(payload.detail);
            }
            return JSON.stringify(payload);
        }
        const text = await response.text();
        const normalized = String(text || '').trim();
        const looksLikeHtml = contentType.includes('text/html') || normalized.startsWith('<!DOCTYPE html') || normalized.startsWith('<html');
        if (looksLikeHtml && normalized.toLowerCase().includes('application error')) {
            return 'Backend temporarily unavailable (Heroku restart). Please retry in 10-20 seconds.';
        }
        return normalized || fallbackMessage;
    } catch {
        return fallbackMessage;
    }
}

/**
 * Create a transcription job by uploading video (and optional deck).
 * Includes automatic retry with exponential backoff for upload reliability.
 * @param {Blob} videoBlob
 * @param {File|null} deckFile
 * @returns {Promise<{job_id:string,status:string}>}
 */
export async function createJob(videoBlob, deckFile = null, { onProgress } = {}) {
    const formData = new FormData();
    formData.append('video', videoBlob, 'recording.webm');
    if (deckFile) {
        formData.append('deck', deckFile, deckFile.name || 'deck');
    }

    const MAX_RETRIES = 3;
    let lastError = null;

    for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
        try {
            const result = await _uploadWithProgress(
                `${API_BASE_URL}/api/jobs`,
                formData,
                { timeoutMs: 3 * 60 * 1000, onProgress },
            );
            return result;
        } catch (err) {
            lastError = err;
            if (err.name === 'AbortError' || err.message.includes('timed out')) {
                throw new Error('Upload timed out. Please check your connection and try again.');
            }
            // Don't retry on 4xx client errors
            if (err.statusCode && err.statusCode >= 400 && err.statusCode < 500) {
                throw err;
            }
            if (attempt < MAX_RETRIES - 1) {
                await new Promise(r => setTimeout(r, 1000 * (attempt + 1)));
                continue;
            }
        }
    }

    throw lastError || new Error('Upload failed after retries.');
}

/**
 * Upload FormData using XMLHttpRequest for progress tracking.
 * @returns {Promise<Object>} Parsed JSON response
 */
function _uploadWithProgress(url, formData, { timeoutMs = 180000, onProgress } = {}) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', url, true);
        xhr.timeout = timeoutMs;

        xhr.upload.addEventListener('progress', (e) => {
            if (e.lengthComputable && onProgress) {
                const pct = Math.round((e.loaded / e.total) * 100);
                onProgress(pct);
            }
        });

        xhr.addEventListener('load', () => {
            try {
                const body = JSON.parse(xhr.responseText);
                if (xhr.status >= 200 && xhr.status < 300) {
                    resolve(body);
                } else {
                    const detail = body?.detail || `Job creation failed (${xhr.status})`;
                    const err = new Error(detail);
                    err.statusCode = xhr.status;
                    reject(err);
                }
            } catch {
                const err = new Error(`Server error (${xhr.status})`);
                err.statusCode = xhr.status;
                reject(err);
            }
        });

        xhr.addEventListener('error', () => reject(new Error('Network error during upload.')));
        xhr.addEventListener('timeout', () => reject(new Error('Upload timed out. Please check your connection and try again.')));
        xhr.addEventListener('abort', () => reject(new Error('Upload was cancelled.')));

        xhr.send(formData);
    });
}

/**
 * Fetch current job state.
 * @param {string} jobId
 * @returns {Promise<Object>}
 */
export async function getJob(jobId) {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}`, {
        method: 'GET',
    });

    if (!response.ok) {
        const detail = await readErrorDetail(
            response,
            `Job polling failed (${response.status})`
        );
        throw new Error(detail);
    }

    return response.json();
}

/**
 * Start Round 1 feedback generation for an existing job.
 * @param {string} jobId
 * @returns {Promise<{job_id:string,status:string}>}
 */
export async function startRound1Feedback(jobId) {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/feedback/round1`, {
        method: 'POST',
    });

    if (!response.ok) {
        const detail = await readErrorDetail(
            response,
            `Failed to start round 1 feedback (${response.status})`
        );
        throw new Error(detail);
    }

    return response.json();
}

/**
 * Start Round 2 feedback generation for an existing job.
 * @param {string} jobId
 * @returns {Promise<{job_id:string,status:string}>}
 */
export async function startRound2Feedback(jobId) {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/feedback/round2`, {
        method: 'POST',
    });

    if (!response.ok) {
        const detail = await readErrorDetail(
            response,
            `Failed to start round 2 feedback (${response.status})`
        );
        throw new Error(detail);
    }

    return response.json();
}

/**
 * Start Round 3 (Vocal Tone & Energy) feedback generation for an existing job.
 * @param {string} jobId
 * @returns {Promise<{job_id:string,status:string}>}
 */
export async function startRound3Feedback(jobId) {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/feedback/round3`, {
        method: 'POST',
    });

    if (!response.ok) {
        const detail = await readErrorDetail(
            response,
            `Failed to start round 3 feedback (${response.status})`
        );
        throw new Error(detail);
    }

    return response.json();
}

/**
 * Start Round 4 (Body Language & Presence) feedback generation for an existing job.
 * @param {string} jobId
 * @returns {Promise<{job_id:string,status:string}>}
 */
export async function startRound4Feedback(jobId) {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/feedback/round4`, {
        method: 'POST',
    });

    if (!response.ok) {
        const detail = await readErrorDetail(
            response,
            `Failed to start round 4 feedback (${response.status})`
        );
        throw new Error(detail);
    }

    return response.json();
}

/**
 * Prepare a job: creates the job on the server and returns a GCS signed URL
 * for direct video upload (bypasses Heroku router timeout).
 * @returns {Promise<{job_id:string, upload_url:string, video_blob_path:string}>}
 */
export async function prepareJob() {
    const response = await fetch(`${API_BASE_URL}/api/jobs/prepare`, {
        method: 'POST',
    });
    if (!response.ok) {
        const detail = await readErrorDetail(
            response,
            `Failed to prepare job (${response.status})`
        );
        throw new Error(detail);
    }
    return response.json();
}

/**
 * Upload a video blob directly to GCS via a signed URL.
 * Uses XHR for upload progress tracking.
 * @param {string} signedUrl  - GCS V4 signed PUT URL
 * @param {Blob}   videoBlob  - The recorded video
 * @param {Object} opts
 * @param {Function} opts.onProgress - called with percentage (0-100)
 * @returns {Promise<void>}
 */
export function uploadVideoToGCS(signedUrl, videoBlob, { onProgress } = {}) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('PUT', signedUrl, true);
        xhr.setRequestHeader('Content-Type', 'video/webm');
        xhr.timeout = 10 * 60 * 1000; // 10 minutes — no Heroku limit

        xhr.upload.addEventListener('progress', (e) => {
            if (e.lengthComputable && onProgress) {
                const pct = Math.round((e.loaded / e.total) * 100);
                onProgress(pct);
            }
        });

        xhr.addEventListener('load', () => {
            if (xhr.status >= 200 && xhr.status < 300) {
                resolve();
            } else {
                reject(new Error(`GCS upload failed (HTTP ${xhr.status})`));
            }
        });

        xhr.addEventListener('error', () =>
            reject(new Error('Network error during GCS upload')));
        xhr.addEventListener('timeout', () =>
            reject(new Error('GCS upload timed out')));
        xhr.addEventListener('abort', () =>
            reject(new Error('GCS upload aborted')));

        xhr.send(videoBlob);
    });
}

/**
 * Tell the backend to start processing a job whose video is already in GCS.
 * Optionally attach a deck file (small, goes through Heroku).
 * @param {string} jobId
 * @param {File|null} deckFile
 * @returns {Promise<{job_id:string, status:string}>}
 */
export async function startProcessing(jobId, deckFile = null) {
    const formData = new FormData();
    if (deckFile) {
        formData.append('deck', deckFile, deckFile.name || 'deck');
    }
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/process`, {
        method: 'POST',
        body: formData,
    });
    if (!response.ok) {
        const detail = await readErrorDetail(
            response,
            `Failed to start processing (${response.status})`
        );
        throw new Error(detail);
    }
    return response.json();
}

/**
 * Check if backend is available
 * @returns {Promise<boolean>}
 */
export async function checkBackendHealth() {
    try {
        const response = await fetch(`${API_BASE_URL}/health`, {
            method: 'GET',
        });
        return response.ok;
    } catch (error) {
        console.warn('Backend health check failed:', error);
        return false;
    }
}
