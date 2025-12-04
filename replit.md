# Video Generator API

## Overview
FastAPI-based REST API service that generates MP4 videos by combining images and audio. The service downloads images and audio from provided URLs, creates a video using MoviePy, and optionally adds text overlays.

## Features
- **POST /generate_video/**: Main endpoint with two modes (requires Basic Auth):
  - **Auto Duration**: Images divided equally based on audio length
  - **Custom Duration**: Specify duration in seconds for each image individually
- **GET /videos/{filename}**: Download generated videos (requires Basic Auth)
- **GET /**: Health check endpoint returning API status and version (public)
- **Basic Authentication**: Protected endpoints require HTTP Basic Auth
- Automatic video generation with libx264 codec and AAC audio at 24 fps
- Optional centered text overlay at the bottom of videos
- User-Agent headers for compatibility with services like catbox.moe
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

**Request Body Option 1 - Auto Duration (divides audio equally):**
```json
{
  "image_urls": ["https://example.com/image1.jpg", "https://example.com/image2.jpg"],
  "audio_url": "https://example.com/audio.mp3",
  "title_text": "Optional subtitle text"
}
```

**Request Body Option 2 - Custom Duration (specify seconds per image):**
```json
{
  "images": [
    {"url": "https://example.com/image1.jpg", "duration": 3},
    {"url": "https://example.com/image2.jpg", "duration": 5},
    {"url": "https://example.com/image3.jpg", "duration": 2.5}
  ],
  "audio_url": "https://example.com/audio.mp3",
  "title_text": "Optional subtitle text"
}
```

**Response:**
```json
{
  "message": "Video generated successfully",
  "video_filename": "video_<uuid>.mp4",
  "download_url": "https://your-domain/videos/video_<uuid>.mp4",
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
- 2025-11-07: Added User-Agent headers to fix downloads from catbox.moe and similar services
- 2025-11-07: Added custom duration feature - users can now specify duration for each image individually
- 2025-12-04: Added Basic Authentication to POST /generate_video/ and GET /videos/{filename}
- 2025-12-04: Added download_url to response for easy video access
