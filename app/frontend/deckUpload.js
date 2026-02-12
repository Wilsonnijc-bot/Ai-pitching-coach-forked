// Deck upload functionality

export class DeckUploader {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.uploadArea = null;
        this.fileInput = null;
        this.fileInfo = null;
        this.statusMessage = null;
        this.currentFile = null;
        
        this.init();
    }

    init() {
        if (!this.container) return;

        this.uploadArea = this.container.querySelector('.upload-area');
        this.fileInput = this.container.querySelector('.file-input');
        this.fileInfo = this.container.querySelector('.file-info');
        this.statusMessage = this.container.querySelector('.status-message');

        this.attachEventListeners();
    }

    attachEventListeners() {
        // Click to select file
        this.uploadArea.addEventListener('click', () => {
            this.fileInput.click();
        });

        // File selection
        this.fileInput.addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (file) {
                this.handleFile(file);
            }
        });

        // Drag and drop
        this.uploadArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            this.uploadArea.classList.add('drag-over');
        });

        this.uploadArea.addEventListener('dragleave', () => {
            this.uploadArea.classList.remove('drag-over');
        });

        this.uploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            this.uploadArea.classList.remove('drag-over');
            
            const file = e.dataTransfer.files[0];
            if (file) {
                this.handleFile(file);
            }
        });

        // Remove file button
        const removeBtn = this.container.querySelector('.btn-remove');
        if (removeBtn) {
            removeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.removeFile();
            });
        }

        // Upload button
        const uploadBtn = this.container.querySelector('.btn-upload');
        if (uploadBtn) {
            uploadBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.uploadFile();
            });
        }
    }

    handleFile(file) {
        // Validate file type
        const validTypes = new Set([
            'application/pdf',
            'application/x-pdf',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'application/zip',
            'application/octet-stream'
        ]);
        const name = (file.name || '').toLowerCase();
        const extension = name.includes('.') ? name.slice(name.lastIndexOf('.')) : '';
        const validExtensions = new Set(['.pdf', '.pptx']);

        if (!validExtensions.has(extension)) {
            this.showStatus('Please select a valid file (.pdf, .pptx)', 'error');
            return;
        }

        if (file.type && !validTypes.has(file.type)) {
            this.showStatus('Unsupported file type. Please upload PDF or PPTX.', 'error');
            return;
        }

        // Validate file size (max 25MB)
        const maxSize = 25 * 1024 * 1024;
        if (file.size > maxSize) {
            this.showStatus('File size must be less than 25MB', 'error');
            return;
        }

        this.currentFile = file;
        this.displayFileInfo(file);
        this.hideStatus();
    }

    displayFileInfo(file) {
        const fileName = this.fileInfo.querySelector('.file-name');
        const fileSize = this.fileInfo.querySelector('.file-size');

        fileName.textContent = file.name;
        fileSize.textContent = this.formatFileSize(file.size);

        this.uploadArea.style.display = 'none';
        this.fileInfo.classList.add('active');
    }

    removeFile() {
        this.currentFile = null;
        this.fileInput.value = '';
        this.fileInfo.classList.remove('active');
        this.uploadArea.style.display = 'block';
        this.hideStatus();
    }

    async uploadFile() {
        if (!this.currentFile) {
            this.showStatus('No file selected', 'error');
            return;
        }

        const uploadBtn = this.container.querySelector('.btn-upload');
        uploadBtn.disabled = true;
        uploadBtn.textContent = 'Ready';

        this.showStatus('Deck attached. It will upload with your next recording.', 'success');
        uploadBtn.disabled = false;
    }

    formatFileSize(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
    }

    showStatus(message, type) {
        this.statusMessage.textContent = message;
        this.statusMessage.className = `status-message active ${type}`;
    }

    hideStatus() {
        this.statusMessage.classList.remove('active');
    }
}
