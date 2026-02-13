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
export async function createJob(videoBlob, deckFile = null) {
    const formData = new FormData();
    formData.append('video', videoBlob, 'recording.webm');
    if (deckFile) {
        formData.append('deck', deckFile, deckFile.name || 'deck');
    }

    const MAX_RETRIES = 3;
    let lastError = null;

    for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
        try {
            const controller = new AbortController();
            // 3-minute timeout for large video uploads
            const timeoutId = setTimeout(() => controller.abort(), 3 * 60 * 1000);

            const response = await fetch(`${API_BASE_URL}/api/jobs`, {
                method: 'POST',
                body: formData,
                signal: controller.signal,
            });

            clearTimeout(timeoutId);

            if (!response.ok) {
                const detail = await readErrorDetail(
                    response,
                    `Job creation failed (${response.status})`
                );
                // Don't retry on 4xx client errors (bad request, too large, etc.)
                if (response.status >= 400 && response.status < 500) {
                    throw new Error(detail);
                }
                lastError = new Error(detail);
            } else {
                return response.json();
            }
        } catch (err) {
            lastError = err;
            // Don't retry if it was a deliberate abort or a 4xx error
            if (err.name === 'AbortError') {
                throw new Error('Upload timed out. Please check your connection and try again.');
            }
            if (attempt < MAX_RETRIES - 1 && !err.message.includes('(4')) {
                // Exponential backoff: 1s, 2s
                await new Promise(r => setTimeout(r, 1000 * (attempt + 1)));
                continue;
            }
        }
    }

    throw lastError || new Error('Upload failed after retries.');
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
