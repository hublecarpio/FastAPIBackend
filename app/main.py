from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, HttpUrl, field_validator
from typing import List, Optional, Dict, Any
import requests
import os
import uuid
import shutil
import subprocess
import threading
from pathlib import Path
from PIL import Image
import io
from datetime import datetime
from moviepy.editor import ImageClip, VideoFileClip, concatenate_videoclips, AudioFileClip, TextClip, CompositeVideoClip

app = FastAPI(title="Video Generator API", version="1.0")

OUTPUT_DIR = Path("output")
TEMP_DIR = Path("temp")

OUTPUT_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

jobs_store: Dict[str, Dict[str, Any]] = {}

DOWNLOAD_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

class ImageWithDuration(BaseModel):
    url: HttpUrl
    duration: float
    
    @field_validator('duration')
    @classmethod
    def validate_duration(cls, v):
        if v <= 0:
            raise ValueError('duration must be greater than 0')
        if not float('-inf') < v < float('inf'):
            raise ValueError('duration must be a finite number')
        return v

class VideoRequest(BaseModel):
    image_urls: Optional[List[HttpUrl]] = None
    images: Optional[List[ImageWithDuration]] = None
    audio_url: HttpUrl
    title_text: Optional[str] = None

class ConcatVideosRequest(BaseModel):
    video_urls: List[HttpUrl]

def download_and_validate_image(url: str, save_path: Path, idx: int) -> Path:
    """Download image, validate it's a real image, and save as PNG."""
    try:
        response = requests.get(url, headers=DOWNLOAD_HEADERS, timeout=30)
        response.raise_for_status()
        
        content_length = len(response.content)
        if content_length < 100:
            raise ValueError(f"Downloaded file too small ({content_length} bytes), likely not a valid image")
        
        content_type = response.headers.get('content-type', '')
        if 'text/html' in content_type.lower():
            raise ValueError(f"URL returned HTML instead of an image (content-type: {content_type})")
        
        try:
            img = Image.open(io.BytesIO(response.content))
            img.verify()
            img = Image.open(io.BytesIO(response.content))
            
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            
            png_path = save_path.with_suffix('.png')
            img.save(png_path, 'PNG')
            img.close()
            
            return png_path
            
        except Exception as e:
            raise ValueError(f"Failed to process as image: {str(e)}")
            
    except requests.exceptions.RequestException as e:
        raise ValueError(f"Download failed: {str(e)}")

@app.get("/")
async def root():
    return {"status": "ok", "api_version": "1.0"}

@app.get("/videos/{filename}")
async def get_video(filename: str):
    video_path = OUTPUT_DIR / filename
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(video_path, media_type="video/mp4", filename=filename)

@app.post("/generate_video/")
async def generate_video(request: VideoRequest, req: Request):
    if not request.image_urls and not request.images:
        raise HTTPException(
            status_code=400,
            detail="Either 'image_urls' or 'images' must be provided"
        )
    
    if request.image_urls and request.images:
        raise HTTPException(
            status_code=400,
            detail="Provide either 'image_urls' OR 'images', not both"
        )
    
    temp_session_dir = TEMP_DIR / str(uuid.uuid4())
    temp_session_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        image_data = []
        
        if request.images:
            for idx, img in enumerate(request.images):
                try:
                    image_path = temp_session_dir / f"image_{idx}"
                    validated_path = download_and_validate_image(str(img.url), image_path, idx)
                    image_data.append({"path": validated_path, "duration": img.duration})
                except ValueError as e:
                    raise HTTPException(
                        status_code=400, 
                        detail=f"Image {idx} from {img.url}: {str(e)}"
                    )
        else:
            for idx, image_url in enumerate(request.image_urls):
                try:
                    image_path = temp_session_dir / f"image_{idx}"
                    validated_path = download_and_validate_image(str(image_url), image_path, idx)
                    image_data.append({"path": validated_path, "duration": None})
                except ValueError as e:
                    raise HTTPException(
                        status_code=400, 
                        detail=f"Image {idx} from {image_url}: {str(e)}"
                    )
        
        if not image_data:
            raise HTTPException(status_code=400, detail="No images were successfully downloaded")
        
        try:
            response = requests.get(str(request.audio_url), headers=DOWNLOAD_HEADERS, timeout=30)
            response.raise_for_status()
            
            audio_ext = os.path.splitext(str(request.audio_url).split('?')[0])[1] or '.mp3'
            audio_path = temp_session_dir / f"audio{audio_ext}"
            
            with open(audio_path, 'wb') as f:
                f.write(response.content)
                
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to download audio from {request.audio_url}: {str(e)}"
            )
        
        try:
            audio_clip = AudioFileClip(str(audio_path))
            audio_duration = audio_clip.duration
            
            use_custom_durations = all(img["duration"] is not None for img in image_data)
            
            if use_custom_durations:
                video_clips = []
                for img in image_data:
                    img_clip = ImageClip(str(img["path"]), duration=img["duration"])
                    video_clips.append(img_clip)
            else:
                duration_per_image = audio_duration / len(image_data)
                video_clips = []
                for img in image_data:
                    img_clip = ImageClip(str(img["path"]), duration=duration_per_image)
                    video_clips.append(img_clip)
            
            video = concatenate_videoclips(video_clips, method="compose")
            video = video.set_audio(audio_clip)
            
            if request.title_text:
                try:
                    text_clip = TextClip(
                        request.title_text,
                        fontsize=40,
                        color='white',
                        font='DejaVu-Sans',
                        stroke_color='black',
                        stroke_width=2
                    )
                except Exception:
                    text_clip = TextClip(
                        request.title_text,
                        fontsize=40,
                        color='white',
                        stroke_color='black',
                        stroke_width=2
                    )
                
                text_position = ('center', video.h - 100)
                text_clip = text_clip.set_position(text_position).set_duration(video.duration)
                
                video = CompositeVideoClip([video, text_clip])
            
            video_filename = f"video_{uuid.uuid4()}.mp4"
            video_path = OUTPUT_DIR / video_filename
            
            video.write_videofile(
                str(video_path),
                codec='libx264',
                audio_codec='aac',
                fps=24,
                preset='medium',
                logger=None
            )
            
            video.close()
            audio_clip.close()
            
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to generate video: {str(e)}"
            )
        
        base_url = str(req.base_url).rstrip('/')
        download_url = f"{base_url}/videos/{video_filename}"
        
        return JSONResponse(content={
            "message": "Video generated successfully",
            "video_filename": video_filename,
            "download_url": download_url,
            "local_path": str(video_path.absolute())
        })
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")
    
    finally:
        if temp_session_dir.exists():
            try:
                shutil.rmtree(temp_session_dir)
            except Exception as e:
                print(f"Warning: Failed to clean up temporary directory {temp_session_dir}: {e}")

def download_and_validate_video(url: str, save_path: Path, idx: int) -> Path:
    """Download video and validate it's a valid MP4."""
    try:
        response = requests.get(url, headers=DOWNLOAD_HEADERS, timeout=120, stream=True)
        response.raise_for_status()
        
        content_type = response.headers.get('content-type', '')
        if 'text/html' in content_type.lower():
            raise ValueError(f"URL returned HTML instead of video (content-type: {content_type})")
        
        video_path = save_path.with_suffix('.mp4')
        with open(video_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        file_size = video_path.stat().st_size
        if file_size < 1000:
            raise ValueError(f"Downloaded file too small ({file_size} bytes), likely not a valid video")
        
        return video_path
        
    except requests.exceptions.RequestException as e:
        raise ValueError(f"Download failed: {str(e)}")

def process_concat_job(job_id: str, video_urls: List[str], base_url: str):
    """Background task to concatenate videos."""
    temp_session_dir = TEMP_DIR / job_id
    temp_session_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        jobs_store[job_id]["status"] = "downloading"
        jobs_store[job_id]["progress"] = 0
        
        downloaded_videos = []
        total_videos = len(video_urls)
        
        for idx, video_url in enumerate(video_urls):
            try:
                video_path = temp_session_dir / f"video_{idx}"
                validated_path = download_and_validate_video(video_url, video_path, idx)
                downloaded_videos.append(validated_path)
                jobs_store[job_id]["progress"] = int((idx + 1) / total_videos * 50)
                jobs_store[job_id]["message"] = f"Downloaded {idx + 1}/{total_videos} videos"
            except ValueError as e:
                jobs_store[job_id]["status"] = "failed"
                jobs_store[job_id]["error"] = f"Video {idx}: {str(e)}"
                return
        
        if not downloaded_videos:
            jobs_store[job_id]["status"] = "failed"
            jobs_store[job_id]["error"] = "No videos were successfully downloaded"
            return
        
        jobs_store[job_id]["status"] = "processing"
        jobs_store[job_id]["progress"] = 50
        jobs_store[job_id]["message"] = "Concatenating videos..."
        
        try:
            concat_list_path = temp_session_dir / "concat_list.txt"
            with open(concat_list_path, 'w') as f:
                for video_path in downloaded_videos:
                    f.write(f"file '{video_path.absolute()}'\n")
            
            video_filename = f"concat_{job_id}.mp4"
            output_path = OUTPUT_DIR / video_filename
            
            cmd = [
                'ffmpeg', '-y',
                '-f', 'concat',
                '-safe', '0',
                '-i', str(concat_list_path),
                '-an',
                '-c:v', 'copy',
                '-movflags', '+faststart',
                str(output_path)
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode != 0:
                cmd_reencode = [
                    'ffmpeg', '-y',
                    '-f', 'concat',
                    '-safe', '0',
                    '-i', str(concat_list_path),
                    '-an',
                    '-c:v', 'libx264',
                    '-preset', 'fast',
                    '-movflags', '+faststart',
                    str(output_path)
                ]
                result = subprocess.run(cmd_reencode, capture_output=True, text=True, timeout=600)
                
                if result.returncode != 0:
                    raise Exception(f"FFmpeg failed: {result.stderr[:500]}")
            
            jobs_store[job_id]["status"] = "completed"
            jobs_store[job_id]["progress"] = 100
            jobs_store[job_id]["message"] = "Video ready"
            jobs_store[job_id]["video_filename"] = video_filename
            jobs_store[job_id]["download_url"] = f"{base_url}/videos/{video_filename}"
            jobs_store[job_id]["completed_at"] = datetime.now().isoformat()
            
        except subprocess.TimeoutExpired:
            jobs_store[job_id]["status"] = "failed"
            jobs_store[job_id]["error"] = "Processing timed out (max 10 minutes)"
        except Exception as e:
            jobs_store[job_id]["status"] = "failed"
            jobs_store[job_id]["error"] = str(e)
            
    except Exception as e:
        jobs_store[job_id]["status"] = "failed"
        jobs_store[job_id]["error"] = f"Unexpected error: {str(e)}"
    
    finally:
        if temp_session_dir.exists():
            try:
                shutil.rmtree(temp_session_dir)
            except:
                pass

@app.post("/concat_videos/")
async def concat_videos(request: ConcatVideosRequest, req: Request):
    """Start async video concatenation job. Returns job_id immediately."""
    
    if len(request.video_urls) < 2:
        raise HTTPException(
            status_code=400,
            detail="At least 2 video URLs are required"
        )
    
    job_id = str(uuid.uuid4())
    base_url = str(req.base_url).rstrip('/')
    
    jobs_store[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "message": "Job queued",
        "total_videos": len(request.video_urls),
        "created_at": datetime.now().isoformat(),
        "video_filename": None,
        "download_url": None,
        "error": None
    }
    
    video_urls = [str(url) for url in request.video_urls]
    thread = threading.Thread(target=process_concat_job, args=(job_id, video_urls, base_url))
    thread.start()
    
    return JSONResponse(content={
        "job_id": job_id,
        "status": "queued",
        "message": "Video concatenation job started",
        "check_status_url": f"{base_url}/jobs/{job_id}"
    })

@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Get the status of a video concatenation job."""
    
    if job_id not in jobs_store:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return JSONResponse(content=jobs_store[job_id])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
