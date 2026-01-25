# Video Generator API

## Overview
FastAPI-based REST API service that generates MP4 videos by combining images and audio. The service downloads images and audio from provided URLs, creates a video using MoviePy, and optionally adds text overlays.

## Features
- **POST /generate_video/**: Generate video from images + audio with two modes:
  - **Auto Duration**: Images divided equally based on audio length
  - **Custom Duration**: Specify duration in seconds for each image individually
- **POST /concat_videos/**: Concatenate multiple MP4 videos (async with job queue)
  - Returns job_id immediately, process runs in background
  - Optional: Replace all video audio with a custom audio track
  - Check status via GET /jobs/{job_id}
- **GET /jobs/{job_id}**: Check status of async video jobs (queued, downloading, processing, completed, failed)
- **POST /split_audio/**: Split audio into N parts cutting at natural pauses
  - Detects silence/pauses using pydub
  - Cuts at nearest pause to avoid mid-word cuts
  - Returns download URLs for each segment
- **GET /audios/{filename}**: Download generated audio segments
- **GET /videos/{filename}**: Download generated videos
- **GET /**: Health check endpoint returning API status and version
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
Dockerfile             # Docker image configuration
docker-compose.yml     # Local Docker development
docker-stack.yml       # Docker Swarm / Portainer deployment
.dockerignore          # Docker build exclusions
output/                # Directory for generated videos
temp/                  # Temporary files (auto-cleaned)
```

## Docker Deployment

### Local Development (docker-compose)
```bash
git clone <repo>
cd video-generator-api
docker-compose up -d --build
```
API available at: `http://localhost:5000`

### Docker Swarm / Portainer

**Step 1: Build and push image**
```bash
docker build -t iamhuble/video-generator-api:latest .
docker push iamhuble/video-generator-api:latest
```

**Step 2: Deploy stack via Portainer**
1. Go to Portainer > Stacks > Add stack
2. Upload or paste `docker-stack.yml`
3. Update image name if using private registry
4. Deploy

**Or via CLI:**
```bash
docker stack deploy -c docker-stack.yml video-api
```

### Swarm Configuration
- **Replicas**: 2 (adjustable based on server resources)
- **Resources**: 2 CPU / 2GB RAM limit per replica
- **Healthcheck**: GET / every 30s
- **Restart policy**: on-failure with max 3 attempts

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

### POST /concat_videos/
Concatenate multiple MP4 videos into one. Runs asynchronously - returns job_id immediately.

**Request Body - Keep original audio:**
```json
{
  "video_urls": [
    "https://example.com/video1.mp4",
    "https://example.com/video2.mp4",
    "https://example.com/video3.mp4"
  ]
}
```

**Request Body - Replace with custom audio:**
```json
{
  "video_urls": [
    "https://example.com/video1.mp4",
    "https://example.com/video2.mp4"
  ],
  "audio_url": "https://example.com/background-music.mp3"
}
```

**Response (immediate):**
```json
{
  "job_id": "abc-123-def",
  "status": "queued",
  "message": "Video concatenation job started",
  "check_status_url": "https://your-domain/jobs/abc-123-def"
}
```

### GET /jobs/{job_id}
Check status of an async video job.

**Response (in progress):**
```json
{
  "job_id": "abc-123-def",
  "status": "downloading",
  "progress": 25,
  "message": "Downloaded 5/20 videos",
  "total_videos": 20,
  "has_custom_audio": true
}
```

**Response (completed):**
```json
{
  "job_id": "abc-123-def",
  "status": "completed",
  "progress": 100,
  "message": "Video ready",
  "video_filename": "concat_abc-123-def.mp4",
  "download_url": "https://your-domain/videos/concat_abc-123-def.mp4"
}
```

**Job statuses:** queued → downloading → processing → completed/failed

### POST /split_audio/
Split audio into N parts, cutting at natural silence/pause points to avoid mid-word cuts.

**Request Body:**
```json
{
  "audio_url": "https://example.com/speech.mp3",
  "parts": 7,
  "min_silence_len": 300,
  "silence_thresh": -40
}
```

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| audio_url | Yes | - | URL of audio to split |
| parts | Yes | - | Number of segments (2-100) |
| min_silence_len | No | 300 | Minimum silence duration in ms |
| silence_thresh | No | -40 | Silence threshold in dBFS |

**Response:**
```json
{
  "message": "Audio split successfully",
  "split_id": "abc123",
  "original_duration": 21.0,
  "requested_parts": 7,
  "actual_parts": 7,
  "segments": [
    {"index": 1, "start": 0.0, "end": 2.8, "duration": 2.8, "filename": "segment_abc123_1.mp3", "download_url": "https://your-domain/audios/segment_abc123_1.mp3"},
    {"index": 2, "start": 2.8, "end": 5.9, "duration": 3.1, "filename": "segment_abc123_2.mp3", "download_url": "https://your-domain/audios/segment_abc123_2.mp3"}
  ]
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
- imageio[pyav]: Image/video I/O backend
- numpy: Numerical processing
- pydub: Audio processing and silence detection

### System Dependencies
- FFmpeg: Video encoding and processing (installed via Replit)
- ImageMagick: Image manipulation for MoviePy (installed via Replit)
- DejaVu Sans font: Used for text overlays (available by default)

## Recent Changes
- 2025-11-07: Initial project setup with FastAPI and MoviePy integration
- 2025-11-07: Fixed text overlay to use DejaVu Sans font instead of Arial for compatibility
- 2025-11-07: Added User-Agent headers to fix downloads from catbox.moe and similar services
- 2025-11-07: Added custom duration feature - users can now specify duration for each image individually
- 2025-12-04: Added download_url to response for easy video access
- 2025-12-08: Added Docker support with Dockerfile, docker-compose.yml, docker-stack.yml
- 2025-12-08: Fixed imageio backend error by adding imageio[pyav] dependency
- 2025-12-08: Added Docker Swarm / Portainer compatible deployment configuration
- 2026-01-25: Added POST /concat_videos/ endpoint to concatenate multiple MP4 videos
- 2026-01-25: Implemented async job queue with GET /jobs/{job_id} for status checking
- 2026-01-25: Added optional audio_url parameter to replace video audio with custom track
- 2026-01-25: Improved image validation using Pillow before MoviePy processing
- 2026-01-25: Switched to FFmpeg concat demuxer for faster video concatenation
- 2026-01-25: Added POST /split_audio/ endpoint for intelligent audio splitting at silence points
