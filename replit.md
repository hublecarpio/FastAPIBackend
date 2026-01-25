# Video Generator API

## Overview
This project is a FastAPI-based REST API service designed to generate and manipulate MP4 videos. Its primary purpose is to combine images and audio into videos, offering features like automatic duration calculation, custom image durations, and optional text overlays. The API also supports concatenating multiple videos, splitting audio intelligently, and generating karaoke-style subtitles. This service aims to provide a robust and flexible solution for automated video content creation.

## User Preferences
I prefer clear, concise communication and explanations. When suggesting code changes, please prioritize functional programming paradigms where appropriate. I prefer an iterative development approach, where we tackle features step-by-step. Before making any major architectural changes or significant modifications to existing functionality, please ask for my approval. Ensure that all generated videos maintain a professional quality and that temporary files are cleaned up automatically.

## System Architecture
The application is built on FastAPI, leveraging its asynchronous capabilities and Pydantic for data validation. Video processing is primarily handled by MoviePy, utilizing FFmpeg and ImageMagick for underlying multimedia operations.

**Key Features and Implementations:**
- **Video Generation (`/generate_video/`):**
    - Combines images (from URLs) and audio (from a URL) into an MP4 video.
    - Supports two modes: auto-duration (images divided equally by audio length) and custom duration (specific duration per image).
    - Videos are generated using `libx264` codec, AAC audio, and a default frame rate of 24 FPS.
    - Optional centered text overlays are supported at the bottom of the video.
- **Video Concatenation (`/concat_videos/`):**
    - Concatenates multiple MP4 videos provided as URLs.
    - Operates asynchronously using a job queue, returning a `job_id` for status tracking.
    - Supports replacing original audio with a custom audio track.
    - Advanced text overlay capabilities with customizable position, timing, font, colors, background, padding, and borders.
- **Audio Splitting (`/split_audio/`):**
    - Splits an audio file into a specified number of segments, intelligently cutting at natural pauses using `pydub`'s silence detection.
    - Returns download URLs for each generated audio segment.
- **Karaoke Subtitle Generation (`/generate_karaoke_subtitles/`):**
    - Generates karaoke-style subtitles from an audio URL and an optional script.
    - Utilizes OpenAI Whisper for word-level timestamp detection.
    - Supports "word" mode (each word appears individually) and "highlight" mode (full line visible, active word highlighted).
    - Customizable subtitle styling including position, font size, colors, stroke, and background.
    - AI-generated style prompts can be used for automatic color and style combinations.
    - Subtitles are auto-centered horizontally.
- **Asynchronous Job Management:**
    - Long-running tasks like video concatenation are handled via an asynchronous job queue.
    - Job status can be retrieved using `GET /jobs/{job_id}` with statuses like `queued`, `downloading`, `processing`, `completed`, and `failed`.
- **System Design:**
    - Dockerized for easy deployment and scalability using `docker-compose` for local development and `docker-stack` for Docker Swarm/Portainer.
    - Uvicorn serves the FastAPI application on port 5000.
    - Automatic cleanup of temporary files is implemented.
    - Comprehensive error handling is in place.
    - User-Agent headers are set for compatibility with various file hosting services.
    - Output videos are stored in an `output/` directory, and temporary files in a `temp/` directory.

## External Dependencies

- **FastAPI**: Web framework for building the API.
- **MoviePy**: Python library for video editing.
- **Requests**: HTTP library for downloading external files.
- **Pillow**: Python Imaging Library for image processing.
- **Uvicorn**: ASGI server to run the FastAPI application.
- **imageio[pyav]**: Backend for image and video I/O in MoviePy.
- **numpy**: Library for numerical operations.
- **pydub**: Python library for audio manipulation and silence detection.
- **openai**: Python client library for interacting with OpenAI APIs (e.g., Whisper).
- **FFmpeg**: External command-line tool for multimedia processing (video encoding, decoding, format conversion).
- **ImageMagick**: External command-line tool for image manipulation.
- **DejaVu Sans font**: Default font for text overlays.