# AI Pitching Coach - Frontend

A modern web application for perfecting your pitch presentations with AI-powered transcription and feedback.

## Features

- **Persistent Sidebar Navigation**: ChatGPT-style interface with always-visible navigation
- **Pitch Deck Upload**: Drag-and-drop or browse to upload .pdf, .ppt, or .pptx files
- **Audio Recording**: Record up to 5 minutes of your pitch using your microphone
- **Real-time Transcription**: Get instant speech-to-text transcription with detailed segments and word-level timing
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

**1. Upload Pitch Deck**
```
POST /api/deck/upload
Content-Type: multipart/form-data
Field: deck (file)

Response:
{
  "status": "success",
  "message": "Deck uploaded successfully"
}
```

**2. Speech-to-Text Transcription**
```
POST /api/stt
Content-Type: multipart/form-data
Field: audio (file)

Response:
{
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
}
```

**3. Health Check (Optional)**
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
- Supported formats: .pdf, .ppt, .pptx (max 50MB)
- Click "Upload" to send to backend

### 3. Record Your Pitch
- Click the "Start" button to begin recording
- Speak your pitch (max 5 minutes)
- Click "Stop" to end recording
- Audio is automatically uploaded and transcribed

### 4. View Transcription Results
- **Transcript Tab**: Full text transcription
- **Segments Tab**: Time-stamped segments with start/end times
- **Words Tab**: Word-level timing data
- Click "Copy transcript" to copy the full text to clipboard

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
- Check file size (must be under 50MB)
- Verify file format (.pdf, .ppt, .pptx)
- Ensure backend endpoint is accessible

### Recording Stops Automatically
- Maximum recording duration is 5 minutes
- Check available disk space
- Verify browser supports MediaRecorder API

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