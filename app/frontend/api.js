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
 * Upload a calibration selfie for a prepared job.
 * The backend extracts iris/shoulder/clothing baselines to improve
 * body-language detection during video analysis.
 * @param {string} jobId
 * @param {Blob}   photoBlob - JPEG/PNG image blob
 * @returns {Promise<{job_id:string, calibration:object}>}
 */
export async function uploadCalibrationPhoto(jobId, photoBlob) {
    const formData = new FormData();
    formData.append('photo', photoBlob, 'calibration.jpg');
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/calibrate`, {
        method: 'POST',
        body: formData,
    });
    if (!response.ok) {
        const detail = await readErrorDetail(
            response,
            `Calibration failed (${response.status})`,
        );
        throw new Error(detail);
    }
    return response.json();
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
 * Prepare a job: creates a job shell on the server so video can be uploaded
 * separately via the streaming endpoint.
 * @returns {Promise<{job_id:string, status:string}>}
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
 * Upload video to the backend via the streaming proxy endpoint.
 * Sends raw binary PUT, reads NDJSON progress lines back.
 * This keeps data flowing in both directions, preventing Heroku's H28 timeout.
 * @param {string} jobId
 * @param {Blob}   videoBlob
 * @param {Object} opts
 * @param {Function} opts.onProgress - called with {bytes:number}
 * @returns {Promise<void>}
 */
export async function uploadVideoStreaming(jobId, videoBlob, { onProgress } = {}) {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/upload-video`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: videoBlob,
    });

    if (!response.ok) {
        const detail = await readErrorDetail(
            response,
            `Video upload failed (${response.status})`
        );
        throw new Error(detail);
    }

    // Read the NDJSON stream for progress and final status
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let lastStatus = null;

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // Process complete lines
        const lines = buffer.split('\n');
        buffer = lines.pop(); // keep incomplete line in buffer

        for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed) continue;
            try {
                const msg = JSON.parse(trimmed);
                lastStatus = msg;
                if (msg.status === 'uploading' && onProgress) {
                    onProgress(msg);
                } else if (msg.status === 'error') {
                    throw new Error(msg.detail || 'Upload failed on server');
                }
            } catch (parseErr) {
                if (parseErr.message && !parseErr.message.includes('JSON')) {
                    throw parseErr; // re-throw our own Error from above
                }
                // ignore JSON parse failures for partial lines
            }
        }
    }

    // Process any remaining buffer
    if (buffer.trim()) {
        try {
            const msg = JSON.parse(buffer.trim());
            lastStatus = msg;
            if (msg.status === 'error') {
                throw new Error(msg.detail || 'Upload failed on server');
            }
        } catch (parseErr) {
            if (parseErr.message && !parseErr.message.includes('JSON')) {
                throw parseErr;
            }
        }
    }

    if (!lastStatus || lastStatus.status !== 'done') {
        throw new Error('Upload stream ended without confirmation');
    }
}

/**
 * Upload a video to the backend in small chunks.
 *
 * Each chunk is sent as a separate PATCH request, keeping every request
 * fast enough to avoid Heroku's 30s / 55s timeouts.  Failed chunks are
 * retried with exponential backoff.
 *
 * @param {string} jobId
 * @param {Blob} videoBlob
 * @param {Object} opts
 * @param {function} [opts.onProgress]  – called with {bytes, total, pct}
 * @param {number}   [opts.chunkSize]   – bytes per chunk (default 2 MB)
 * @param {number}   [opts.maxRetries]  – retries per chunk (default 3)
 * @returns {Promise<void>}
 */
export async function uploadVideoChunked(
    jobId,
    videoBlob,
    { onProgress, chunkSize = 2 * 1024 * 1024, maxRetries = 3 } = {},
) {
    const totalSize = videoBlob.size;
    let offset = 0;

    while (offset < totalSize) {
        const end = Math.min(offset + chunkSize, totalSize);
        const chunk = videoBlob.slice(offset, end);
        let lastErr;

        for (let attempt = 0; attempt <= maxRetries; attempt++) {
            try {
                const url =
                    `${API_BASE_URL}/api/jobs/${jobId}/upload-chunk` +
                    `?offset=${offset}&total_size=${totalSize}`;
                const resp = await fetch(url, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/octet-stream' },
                    body: chunk,
                });

                if (!resp.ok) {
                    const detail = await readErrorDetail(
                        resp,
                        `Chunk upload failed (${resp.status})`,
                    );
                    throw new Error(detail);
                }

                const result = await resp.json();
                // Advance to next chunk
                offset = end;

                if (onProgress) {
                    onProgress({
                        bytes: offset,
                        total: totalSize,
                        pct: Math.round((offset / totalSize) * 100),
                    });
                }

                lastErr = null;
                break; // success — move to next chunk
            } catch (err) {
                lastErr = err;
                if (attempt < maxRetries) {
                    // Exponential backoff: 1s, 2s, 4s
                    await new Promise((r) => setTimeout(r, 1000 * Math.pow(2, attempt)));
                }
            }
        }

        if (lastErr) {
            throw new Error(
                `Upload failed at ${Math.round((offset / totalSize) * 100)}%: ${lastErr.message}`,
            );
        }
    }
}

/**
 * Tell the backend to start processing a job whose video has been uploaded.
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


// ─── Direct-to-GCS upload (bypasses Heroku router entirely) ───

/**
 * Get a signed URL for uploading video directly to GCS.
 * @param {string} jobId
 * @returns {Promise<{upload_url:string, content_type:string, bucket:string, blob_path:string, gcs_uri:string, max_bytes:number}>}
 */
export async function getUploadUrl(jobId) {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/upload-url`, {
        method: 'POST',
    });
    if (!response.ok) {
        const detail = await readErrorDetail(
            response,
            `Failed to get upload URL (${response.status})`
        );
        throw new Error(detail);
    }
    return response.json();
}

/**
 * Upload a video blob directly to GCS via a signed URL using XHR for
 * progress tracking.  This completely bypasses Heroku's router and its
 * 55-second idle-connection (H28) timeout.
 *
 * Includes automatic retry with exponential backoff.
 *
 * @param {string}   uploadUrl   - GCS signed PUT URL
 * @param {Blob}     videoBlob   - The recorded video
 * @param {string}   contentType - Must match the signed URL's content-type
 * @param {Object}   opts
 * @param {Function} opts.onProgress - called with percentage (0-100)
 * @param {number}   opts.maxRetries - default 3
 * @returns {Promise<void>}
 */
export function uploadVideoDirectToGcs(
    uploadUrl,
    videoBlob,
    contentType,
    { onProgress, maxRetries = 3 } = {},
) {
    let attempt = 0;

    function tryUpload() {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open('PUT', uploadUrl, true);
            xhr.setRequestHeader('Content-Type', contentType);

            // No timeout — GCS handles large uploads fine.  The browser's
            // network layer will error out if the connection drops.

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
                    const err = new Error(
                        `GCS upload failed (HTTP ${xhr.status}): ${xhr.responseText?.slice(0, 200)}`
                    );
                    err.statusCode = xhr.status;
                    reject(err);
                }
            });

            xhr.addEventListener('error', () =>
                reject(new Error('Network error during GCS upload.'))
            );
            xhr.addEventListener('abort', () =>
                reject(new Error('GCS upload was cancelled.'))
            );

            xhr.send(videoBlob);
        });
    }

    return (async () => {
        let lastError;
        for (attempt = 0; attempt < maxRetries; attempt++) {
            try {
                await tryUpload();
                return; // success
            } catch (err) {
                lastError = err;
                console.warn(`GCS upload attempt ${attempt + 1}/${maxRetries} failed:`, err.message);
                // Don't retry on 4xx (bad signed URL, etc.)
                if (err.statusCode && err.statusCode >= 400 && err.statusCode < 500) {
                    throw err;
                }
                if (attempt < maxRetries - 1) {
                    const delay = 1000 * Math.pow(2, attempt); // 1s, 2s, 4s
                    await new Promise(r => setTimeout(r, delay));
                    if (onProgress) onProgress(0); // reset progress for retry
                }
            }
        }
        throw lastError || new Error('GCS upload failed after retries.');
    })();
}

/**
 * Tell the backend to start processing a job whose video was uploaded
 * directly to GCS.  Optionally attach a deck file.
 * @param {string}    jobId
 * @param {File|null} deckFile
 * @returns {Promise<{job_id:string, status:string}>}
 */
export async function processFromGcs(jobId, deckFile = null) {
    const formData = new FormData();
    if (deckFile) {
        formData.append('deck', deckFile, deckFile.name || 'deck');
    }
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/process-gcs`, {
        method: 'POST',
        body: formData,
    });
    if (!response.ok) {
        const detail = await readErrorDetail(
            response,
            `Failed to start GCS processing (${response.status})`
        );
        throw new Error(detail);
    }
    return response.json();
}
