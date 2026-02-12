# AI Pitching Coach - Development Plan

## Design Guidelines

### Design References
- **ChatGPT Interface**: Persistent left sidebar, clean main content area
- **Style**: Modern Minimalism + Light Mode + Professional

### Color Palette
- Primary: #10A37F (Teal Green - accent/CTAs)
- Secondary: #F7F7F8 (Light Gray - backgrounds)
- Sidebar: #FFFFFF (White - sidebar background)
- Text: #2D333A (Dark Gray - primary text)
- Text Secondary: #6E6E80 (Medium Gray)
- Border: #E5E5E5 (Light borders)
- Hover: #F0F0F0 (Subtle hover states)

### Typography
- Font Family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif
- Heading1: font-weight 600 (32px)
- Heading2: font-weight 600 (24px)
- Heading3: font-weight 600 (18px)
- Body: font-weight 400 (16px)
- Small: font-weight 400 (14px)

### Key Component Styles
- **Buttons**: Primary (#10A37F background, white text, 8px rounded), Secondary (white background, border, hover state)
- **Cards**: White background, 1px border (#E5E5E5), 12px rounded, subtle shadow
- **Sidebar Items**: 8px rounded, hover background (#F0F0F0), active state with teal accent
- **Upload Zone**: Dashed border, hover state, drag-over visual feedback

### Layout & Spacing
- Sidebar: Fixed 260px width (desktop), collapsible on mobile
- Main content: Max-width 1200px, centered, 32px padding
- Card spacing: 24px gaps between cards
- Section padding: 40px vertical

### Images to Generate
1. **logo-icon.png** - Simple microphone or speech bubble icon for branding (Style: minimalist, vector-style, teal accent)
2. **hero-background.jpg** - Subtle abstract background for landing page hero section (Style: soft gradients, professional, light colors)
3. **upload-illustration.png** - Friendly illustration for upload area (Style: minimalist line art, teal accent)
4. **recording-visual.png** - Waveform or audio visualization graphic (Style: modern, dynamic, teal accent)

---

## Development Tasks

1. **Setup & Structure** - Create folder structure, initialize HTML template
2. **Generate Images** - Create all 4 images using ImageCreator.generate_images
3. **Core HTML** - Build index.html with sidebar and main content structure
4. **CSS Styling** - Implement design system, responsive layouts, animations
5. **JavaScript Router** - Implement SPA routing (hash-based)
6. **Landing Page** - Hero section with CTA
7. **Studio Page** - Deck upload card + Recording card with MediaRecorder API
8. **API Integration** - Fetch wrappers for backend endpoints
9. **Transcription Display** - Results panel with tabs (transcript, segments, words)
10. **Responsive Design** - Mobile sidebar, touch interactions
11. **Testing** - Cross-browser testing, error handling
12. **Documentation** - README with setup instructions

## File Structure
```
/frontend
  - index.html (main entry point)
  - styles.css (all styles)
  - app.js (router, main app logic)
  - api.js (backend API wrappers)
  - recorder.js (MediaRecorder logic)
  - deckUpload.js (file upload logic)
  - README.md (setup instructions)
  /assets
    - logo-icon.png
    - hero-background.jpg
    - upload-illustration.png
    - recording-visual.png
```