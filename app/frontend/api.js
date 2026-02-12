// API configuration and wrapper functions
const API_BASE_URL = 'http://localhost:8000';

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
        const response = await fetch(`${API_BASE_URL}/api/stt`, {
            method: 'POST',
            body: formData,
        });

        if (!response.ok) {
            throw new Error(`Transcription failed: ${response.statusText}`);
        }

        const result = await response.json();
        return result;
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