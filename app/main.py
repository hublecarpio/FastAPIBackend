from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl, field_validator
from typing import List, Optional
import requests
import os
import uuid
import shutil
from pathlib import Path
from moviepy.editor import ImageClip, concatenate_videoclips, AudioFileClip, TextClip, CompositeVideoClip

app = FastAPI(title="Video Generator API", version="1.0")

OUTPUT_DIR = Path("output")
TEMP_DIR = Path("temp")

OUTPUT_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

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

@app.get("/")
async def root():
    return {"status": "ok", "api_version": "1.0"}

@app.post("/generate_video/")
async def generate_video(request: VideoRequest):
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
                    response = requests.get(str(img.url), headers=DOWNLOAD_HEADERS, timeout=30)
                    response.raise_for_status()
                    
                    ext = os.path.splitext(str(img.url).split('?')[0])[1] or '.jpg'
                    image_path = temp_session_dir / f"image_{idx}{ext}"
                    
                    with open(image_path, 'wb') as f:
                        f.write(response.content)
                    
                    image_data.append({"path": image_path, "duration": img.duration})
                    
                except Exception as e:
                    raise HTTPException(
                        status_code=400, 
                        detail=f"Failed to download image {idx} from {img.url}: {str(e)}"
                    )
        else:
            for idx, image_url in enumerate(request.image_urls):
                try:
                    response = requests.get(str(image_url), headers=DOWNLOAD_HEADERS, timeout=30)
                    response.raise_for_status()
                    
                    ext = os.path.splitext(str(image_url).split('?')[0])[1] or '.jpg'
                    image_path = temp_session_dir / f"image_{idx}{ext}"
                    
                    with open(image_path, 'wb') as f:
                        f.write(response.content)
                    
                    image_data.append({"path": image_path, "duration": None})
                    
                except Exception as e:
                    raise HTTPException(
                        status_code=400, 
                        detail=f"Failed to download image {idx} from {image_url}: {str(e)}"
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
        
        return JSONResponse(content={
            "message": "Video generated successfully",
            "video_filename": video_filename,
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
