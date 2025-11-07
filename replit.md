# Video Generator API

## Overview
FastAPI-based REST API service that generates MP4 videos by combining images and audio. The service downloads images and audio from provided URLs, creates a video using MoviePy, and optionally adds text overlays.

## Features
- **POST /generate_video/**: Main endpoint that accepts JSON with image URLs, audio URL, and optional title text
- **GET /**: Health check endpoint returning API status and version
- Automatic video generation with libx264 codec and AAC audio at 24 fps
- Optional centered text overlay at the bottom of videos
- Automatic cleanup of temporary files
- Comprehensive error handling

## Project Structure
```
/app
  └── main.py          # FastAPI application and video generation logic
requirements.txt       # Python dependencies
output/                # Directory for generated videos
temp/                  # Temporary files (auto-cleaned)
```

## API Endpoints

### GET /
Returns API status and version information.

**Response:**
```json
{
  "status": "ok",
  "api_version": "1.0"
}
```

### POST /generate_video/
Generates a video from images and audio.

**Request Body:**
```json
{
  "image_urls": ["https://example.com/image1.jpg", "https://example.com/image2.jpg"],
  "audio_url": "https://example.com/audio.mp3",
  "title_text": "Optional subtitle text"
}
```

**Response:**
```json
{
  "message": "Video generated successfully",
  "video_filename": "video_<uuid>.mp4",
  "local_path": "/path/to/output/video_<uuid>.mp4"
}
```

## Technical Details
- **Framework**: FastAPI with Pydantic validation
- **Video Processing**: MoviePy for video generation
- **Codec**: libx264 (video), AAC (audio)
- **FPS**: 24 frames per second
- **Server**: Uvicorn ASGI server running on port 5000
- **Text Overlay**: Uses DejaVu Sans font with graceful fallback to system default

## Dependencies

### Python Packages
- FastAPI: Web framework
- MoviePy: Video editing library
- Requests: HTTP library for downloading files
- Pillow: Image processing
- Uvicorn: ASGI server

### System Dependencies
- FFmpeg: Video encoding and processing (installed via Replit)
- ImageMagick: Image manipulation for MoviePy (installed via Replit)
- DejaVu Sans font: Used for text overlays (available by default)

## Recent Changes
- 2025-11-07: Initial project setup with FastAPI and MoviePy integration
- 2025-11-07: Fixed text overlay to use DejaVu Sans font instead of Arial for compatibility
