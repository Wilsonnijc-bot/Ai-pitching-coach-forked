// API configuration and wrapper functions
const API_BASE_URL = 'http://localhost:8000';
const JOB_POLL_INTERVAL_MS = 1500;
const JOB_TIMEOUT_MS = 5 * 60 * 1000;

/**
 * Upload pitch deck file to backend
 * @param {File} file - The deck file to upload
 * @returns {Promise<Object>} Response from backend
 */
export async function uploadDeck(file) {
    const formData = new FormData();
    formData.append('deck', file);

    try {
        const response = await fetch(`${API_BASE_URL}/api/deck/upload`, {
            method: 'POST',
            body: formData,
        });

        if (!response.ok) {
            throw new Error(`Upload failed: ${response.statusText}`);
        }

        return await response.json();
    } catch (error) {
        console.error('Deck upload error:', error);
        throw error;
    }
}

/**
 * Upload audio recording for speech-to-text transcription
 * @param {Blob} audioBlob - The recorded audio blob
 * @returns {Promise<Object>} Transcription results
 */
export async function transcribeAudio(audioBlob) {
    const formData = new FormData();
    formData.append('audio', audioBlob, 'recording.webm');

    try {
        // Step 1: create async transcription job.
        const createResponse = await fetch(`${API_BASE_URL}/api/jobs`, {
            method: 'POST',
            body: formData,
        });

        if (!createResponse.ok) {
            const errorText = await createResponse.text();
            throw new Error(`Transcription job creation failed (${createResponse.status}): ${errorText || createResponse.statusText}`);
        }

        const createPayload = await createResponse.json();
        const jobId = createPayload.job_id;
        if (!jobId) {
            throw new Error('Transcription job creation returned no job_id.');
        }

        // Step 2: poll for completion.
        const startedAt = Date.now();
        while (Date.now() - startedAt < JOB_TIMEOUT_MS) {
            await new Promise(resolve => setTimeout(resolve, JOB_POLL_INTERVAL_MS));

            const statusResponse = await fetch(`${API_BASE_URL}/api/jobs/${jobId}`, {
                method: 'GET',
            });

            if (!statusResponse.ok) {
                const errorText = await statusResponse.text();
                throw new Error(`Polling transcription job failed (${statusResponse.status}): ${errorText || statusResponse.statusText}`);
            }

            const job = await statusResponse.json();
            if (job.status === 'done') {
                if (!job.result) {
                    throw new Error('Transcription completed without result payload.');
                }
                return job.result;
            }

            if (job.status === 'failed') {
                throw new Error(job.error || 'Transcription failed.');
            }
        }

        throw new Error('Transcription timed out while waiting for job completion.');
    } catch (error) {
        console.error('Transcription error:', error);
        throw error;
    }
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
