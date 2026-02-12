# AI Pitching Coach - Frontend

A modern web application for perfecting your pitch presentations with AI-powered transcription and feedback.

## Features

- **Persistent Sidebar Navigation**: ChatGPT-style interface with always-visible navigation
- **Pitch Deck Upload**: Drag-and-drop or browse to attach .pdf or .pptx files
- **Flexible Audio Recording**: Stop anytime; 5:00 is only the maximum cap
- **Real-time Transcription**: Get instant speech-to-text transcription with detailed segments and word-level timing
- **Professional Feedback (Round 1 + Round 2)**: Uses the same transcript and renders both coaching rounds
- **Responsive Design**: Works seamlessly on desktop and mobile devices
- **Clean, Modern UI**: Professional aesthetic with smooth animations and transitions

## Tech Stack

- **Frontend**: Pure HTML5, CSS3, and vanilla JavaScript (ES6+)
- **APIs**: MediaRecorder API for audio capture, Fetch API for backend communication
- **Backend**: Python FastAPI (separate repository)

## Prerequisites

- Modern web browser (Chrome, Firefox, Edge, Safari)
- Microphone access for recording functionality
- Python backend running on `http://localhost:8000` (see Backend Setup)

## Quick Start

### 1. Running the Frontend

You can run the frontend using any static file server. Here are several options:

**Option A: Python HTTP Server (Recommended)**
```bash
# Navigate to the frontend directory
cd frontend

# Python 3
python -m http.server 5173

# Python 2
python -m SimpleHTTPServer 5173
```

**Option B: Node.js HTTP Server**
```bash
# Install http-server globally (one-time)
npm install -g http-server

# Run server
http-server -p 5173
```

**Option C: VS Code Live Server**
- Install the "Live Server" extension in VS Code
- Right-click `index.html` and select "Open with Live Server"

### 2. Access the Application

Open your browser and navigate to:
```
http://localhost:5173
```

## Backend Setup

The frontend expects a FastAPI backend running at `http://localhost:8000` with the following endpoints:

### API Endpoints

**1. Speech-to-Text + Optional Deck (Async Job)**
```
POST /api/jobs
Content-Type: multipart/form-data
Field: audio (file)
Field: deck (optional file)

Immediate Response:
{
  "job_id": "<uuid>",
  "status": "queued"
}
```

```
GET /api/jobs/{job_id}

Response:
{
  "job_id": "<uuid>",
  "status": "queued | deck_processing | transcribing | done | failed",
  "progress": 0-100,
  "transcript": {
    "full_text": "Complete transcription text...",
    "segments": [
      {
        "start": 0.0,
        "end": 5.2,
        "text": "Hello, I'm presenting..."
      }
    ],
    "words": [
      {
        "start": 0.0,
        "end": 0.5,
        "word": "Hello"
      }
    ]
  } | null,
  "deck": {
    "filename": "deck.pdf",
    "content_type": "application/pdf",
    "size_bytes": 12345,
    "text_excerpt": "first 500 chars...",
    "num_pages_or_slides": 10
  } | null,
  "feedback_round_1_status": "pending|running|done|failed",
  "feedback_round_1": { ... } | null,
  "feedback_round_2_status": "pending|running|done|failed",
  "feedback_round_2": { ... } | null,
  "error": "string | null"
}
```

**2. Start Round 1 Feedback (Async)**
```
POST /api/jobs/{job_id}/feedback/round1

Immediate Response:
{
  "job_id": "<uuid>",
  "status": "running|done"
}
```

Then poll `GET /api/jobs/{job_id}` until `feedback_round_1_status` is `done` or `failed`.

**3. Start Round 2 Feedback (Async)**
```
POST /api/jobs/{job_id}/feedback/round2

Immediate Response:
{
  "job_id": "<uuid>",
  "status": "running|done"
}
```

Then poll `GET /api/jobs/{job_id}` until `feedback_round_2_status` is `done` or `failed`.

**4. Health Check (Optional)**
```
GET /health

Response:
{
  "status": "ok"
}
```

### Backend Configuration

To change the backend URL, edit `api.js`:

```javascript
const API_BASE_URL = 'http://localhost:8000'; // Change this to your backend URL
```

## Project Structure

```
frontend/
├── index.html           # Main HTML file with sidebar and content structure
├── styles.css           # All CSS styles (design system, components, responsive)
├── app.js              # Main application logic and routing
├── api.js              # Backend API wrapper functions
├── recorder.js         # MediaRecorder functionality for audio capture
├── deckUpload.js       # File upload logic with drag-and-drop
├── README.md           # This file
└── assets/             # Generated images (logo, illustrations)
```

## Browser Compatibility

### Recommended Browsers
- **Chrome/Edge**: Full support (recommended)
- **Firefox**: Full support
- **Safari**: Full support (macOS 14.1+, iOS 14.3+)

### Required Browser Features
- MediaRecorder API (for audio recording)
- Fetch API (for backend communication)
- ES6+ JavaScript support
- CSS Grid and Flexbox

### Microphone Permission

The app requires microphone access for recording. If denied:
1. Click the lock icon in the address bar
2. Change microphone permission to "Allow"
3. Refresh the page

## Usage Guide

### 1. Landing Page
- Click "Start polishing your pitch" to navigate to the Studio

### 2. Upload Pitch Deck
- Drag and drop your deck file onto the upload area, or click to browse
- Supported formats: .pdf, .pptx (max 25MB each)
- Click "Upload" to mark the deck for the next recording upload

### 3. Record Your Pitch
- Click the "Start" button to begin recording
- Speak your pitch and click "Stop" at any time
- Maximum cap is 5:00 and auto-stop will trigger at that point
- Recordings shorter than 2 seconds are rejected in UI
- Audio is automatically uploaded and transcribed

### 4. View Transcription Results
- **Transcript Tab**: Full text transcription
- **Segments Tab**: Time-stamped segments with start/end times
- **Words Tab**: Word-level timing data
- Click "Copy transcript" to copy the full text to clipboard

### 5. Professional Feedback (Automatic Round 1 -> Round 2)
- After transcript is ready, UI starts Round 1 feedback on the same job/transcript
- Once Round 1 is done, UI starts Round 2 feedback on the same job/transcript
- Both rounds are rendered in the Professional Feedback panel
- Optional **View raw JSON** is available for debugging

## Troubleshooting

### Microphone Not Working
- Ensure microphone permission is granted in browser settings
- Check that no other application is using the microphone
- Try refreshing the page

### Backend Connection Failed
- Verify the backend is running at `http://localhost:8000`
- Check browser console for detailed error messages
- Ensure CORS is properly configured on the backend

### Upload Failed
- Check file size (must be under 25MB)
- Verify file format (.pdf, .pptx)
- Ensure backend endpoint is accessible

### Recording Stops Automatically
- Maximum recording duration is 5 minutes
- Check available disk space
- Verify browser supports MediaRecorder API

### Recording Too Short
- Minimum length is 2 seconds
- Record a little longer before stopping

## Development Notes

### Adding New Routes
1. Add navigation item in `index.html` sidebar
2. Create render function in `app.js` (e.g., `renderNewPage()`)
3. Add case in `handleRoute()` switch statement

### Customizing Styles
- All colors are defined as CSS variables in `:root` (styles.css)
- Modify `--primary`, `--secondary-bg`, etc. to change theme
- Responsive breakpoints: 768px (tablet), 480px (mobile)

### API Integration
- All API calls are in `api.js`
- Add new endpoints by creating new functions
- Error handling is built into each API function

## Security Notes

- **No credentials on frontend**: Google Service Account JSON and all secrets remain on the backend
- **HTTPS recommended**: Use HTTPS in production for secure audio transmission
- **CORS configuration**: Backend must allow requests from frontend origin

## Performance Optimization

- Images are served from CDN for fast loading
- CSS and JS are minified in production
- Lazy loading for transcription results
- Efficient DOM manipulation with minimal reflows

## Future Enhancements

- [ ] User authentication and account management
- [ ] Recording history with playback
- [ ] AI-powered pitch analysis and feedback
- [ ] Export transcription to various formats
- [ ] Real-time collaboration features
- [ ] Advanced audio editing tools

## Support

For issues or questions:
1. Check browser console for error messages
2. Verify backend is running and accessible
3. Review this README for troubleshooting steps
4. Check browser compatibility requirements

## License

This project is part of the AI Pitching Coach application suite.
