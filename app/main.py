from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, HttpUrl, field_validator, model_validator
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
from pydub import AudioSegment
from pydub.silence import detect_silence
from openai import OpenAI

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

class TextOverlay(BaseModel):
    text: str
    start: float
    end: float
    x: Optional[int] = None
    y: int = 0
    font_size: Optional[int] = 40
    font_color: Optional[str] = "#FFFFFF"
    font_family: Optional[str] = "DejaVu-Sans"
    background_color: Optional[str] = None
    background_opacity: Optional[float] = 0.7
    padding: Optional[int] = 10
    border_color: Optional[str] = None
    border_width: Optional[int] = 0
    stroke_color: Optional[str] = None
    stroke_width: Optional[int] = 0
    align: Optional[str] = "center"
    highlight_word_index: Optional[int] = None
    highlight_color: Optional[str] = None
    
    @field_validator('start', 'end')
    @classmethod
    def validate_times(cls, v):
        if v < 0:
            raise ValueError('time values must be >= 0')
        return v
    
    @field_validator('align')
    @classmethod
    def validate_align(cls, v):
        if v not in ['left', 'center', 'right']:
            raise ValueError('align must be left, center, or right')
        return v

class ImageOverlay(BaseModel):
    image_url: HttpUrl
    start: float
    end: float
    x: Optional[int] = None
    y: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    opacity: Optional[float] = 1.0
    align: Optional[str] = "center"
    
    @field_validator('start', 'end')
    @classmethod
    def validate_times(cls, v):
        if v < 0:
            raise ValueError('time values must be >= 0')
        return v
    
    @field_validator('opacity')
    @classmethod
    def validate_opacity(cls, v):
        if v < 0 or v > 1:
            raise ValueError('opacity must be between 0 and 1')
        return v
    
    @model_validator(mode='after')
    def validate_duration(self):
        if self.end <= self.start:
            raise ValueError('end must be greater than start')
        return self

class SoundEffect(BaseModel):
    audio_url: HttpUrl
    start: float
    volume: Optional[float] = 1.0
    
    @field_validator('start')
    @classmethod
    def validate_start(cls, v):
        if v < 0:
            raise ValueError('start must be >= 0')
        return v
    
    @field_validator('volume')
    @classmethod
    def validate_volume(cls, v):
        if v < 0.01 or v > 2:
            raise ValueError('volume must be between 0.01 and 2')
        return v

class ConcatVideosRequest(BaseModel):
    video_urls: List[HttpUrl]
    audio_url: Optional[HttpUrl] = None
    overlays: Optional[List[TextOverlay]] = None
    image_overlays: Optional[List[ImageOverlay]] = None
    sound_effects: Optional[List[SoundEffect]] = None

class SplitAudioRequest(BaseModel):
    audio_url: HttpUrl
    parts: int
    min_silence_len: Optional[int] = 300
    silence_thresh: Optional[int] = -40
    
    @field_validator('parts')
    @classmethod
    def validate_parts(cls, v):
        if v < 2:
            raise ValueError('parts must be at least 2')
        if v > 100:
            raise ValueError('parts cannot exceed 100')
        return v

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

def hex_to_rgb(hex_color: str) -> tuple:
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

def wrap_text(text: str, font, max_width: int, draw) -> list:
    """Wrap text to fit within max_width, returning list of lines.
    Handles long words by breaking them character by character if needed."""
    words = text.split()
    lines = []
    current_line = []
    
    for word in words:
        bbox = draw.textbbox((0, 0), word, font=font)
        word_width = bbox[2] - bbox[0]
        
        if word_width > max_width:
            if current_line:
                lines.append(' '.join(current_line))
                current_line = []
            
            chars = list(word)
            current_word_part = ""
            for char in chars:
                test_part = current_word_part + char
                bbox = draw.textbbox((0, 0), test_part, font=font)
                part_width = bbox[2] - bbox[0]
                
                if part_width <= max_width:
                    current_word_part = test_part
                else:
                    if current_word_part:
                        lines.append(current_word_part)
                    current_word_part = char
            
            if current_word_part:
                current_line = [current_word_part]
        else:
            test_line = ' '.join(current_line + [word])
            bbox = draw.textbbox((0, 0), test_line, font=font)
            line_width = bbox[2] - bbox[0]
            
            if line_width <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                current_line = [word]
    
    if current_line:
        lines.append(' '.join(current_line))
    
    return lines if lines else [text]


def create_text_clip_with_background(overlay: dict, video_width: int, video_height: int):
    """Create a TextClip with optional background, padding, border, stroke, and auto text wrapping."""
    from PIL import Image as PILImage, ImageDraw, ImageFont
    from moviepy.editor import ImageClip as MoviePyImageClip
    
    text = overlay['text']
    font_size = overlay.get('font_size', 40)
    font_color = overlay.get('font_color', '#FFFFFF')
    font_family = overlay.get('font_family', 'DejaVu-Sans')
    background_color = overlay.get('background_color')
    background_opacity = overlay.get('background_opacity', 0.7)
    padding = overlay.get('padding', 10)
    border_color = overlay.get('border_color')
    border_width = overlay.get('border_width', 0)
    stroke_color = overlay.get('stroke_color')
    stroke_width = overlay.get('stroke_width', 0)
    align = overlay.get('align', 'center')
    x = overlay.get('x')
    y = overlay.get('y', 0)
    start = overlay['start']
    end = overlay['end']
    
    try:
        font_path = f"/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        font = ImageFont.truetype(font_path, font_size)
    except:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", font_size)
        except:
            font = ImageFont.load_default()
    
    temp_img = PILImage.new('RGBA', (1, 1))
    temp_draw = ImageDraw.Draw(temp_img)
    
    margin = 40
    stroke_extra = stroke_width * 2 if stroke_width else 0
    max_text_width = video_width - (margin * 2) - (padding * 2) - (border_width * 2) - stroke_extra
    
    highlight_word_index = overlay.get('highlight_word_index')
    highlight_color = overlay.get('highlight_color')
    
    if highlight_word_index is not None:
        words = text.split()
        lines = []
        current_line = []
        for word in words:
            test_line = ' '.join(current_line + [word])
            bbox = temp_draw.textbbox((0, 0), test_line, font=font)
            if bbox[2] - bbox[0] <= max_text_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                current_line = [word]
        if current_line:
            lines.append(' '.join(current_line))
        if not lines:
            lines = [text]
        
        word_line_mapping = []
        global_word_idx = 0
        for line_idx, line in enumerate(lines):
            words_in_line = line.split()
            for local_idx, word in enumerate(words_in_line):
                word_line_mapping.append({
                    "global_idx": global_word_idx,
                    "line_idx": line_idx,
                    "local_idx": local_idx
                })
                global_word_idx += 1
    else:
        lines = wrap_text(text, font, max_text_width, temp_draw)
        word_line_mapping = []
    
    line_heights = []
    line_widths = []
    for line in lines:
        bbox = temp_draw.textbbox((0, 0), line, font=font)
        line_widths.append(bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])
    
    max_line_width = max(line_widths) if line_widths else 0
    line_spacing = int(font_size * 0.3)
    total_text_height = sum(line_heights) + (line_spacing * (len(lines) - 1)) if lines else 0
    
    stroke_extra = stroke_width * 2 if stroke_width else 0
    total_width = max_line_width + (padding * 2) + (border_width * 2) + stroke_extra
    total_height = total_text_height + (padding * 2) + (border_width * 2) + stroke_extra
    
    img = PILImage.new('RGBA', (total_width, total_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    if background_color:
        bg_rgb = hex_to_rgb(background_color)
        bg_alpha = int(background_opacity * 255)
        bg_color_rgba = (*bg_rgb, bg_alpha)
        draw.rectangle(
            [border_width, border_width, total_width - border_width - 1, total_height - border_width - 1],
            fill=bg_color_rgba
        )
    
    if border_color and border_width > 0:
        border_rgb = hex_to_rgb(border_color)
        for i in range(border_width):
            draw.rectangle(
                [i, i, total_width - i - 1, total_height - i - 1],
                outline=(*border_rgb, 255)
            )
    
    text_rgb = hex_to_rgb(font_color)
    current_y = padding + border_width + (stroke_width if stroke_width else 0)
    
    for i, line in enumerate(lines):
        line_bbox = temp_draw.textbbox((0, 0), line, font=font)
        line_width = line_bbox[2] - line_bbox[0]
        
        if align == 'center':
            text_x = (total_width - line_width) // 2
        elif align == 'right':
            text_x = total_width - line_width - padding - border_width - (stroke_width if stroke_width else 0)
        else:
            text_x = padding + border_width + (stroke_width if stroke_width else 0)
        
        if highlight_word_index is not None and highlight_color and word_line_mapping:
            highlight_local_idx = None
            for mapping in word_line_mapping:
                if mapping["global_idx"] == highlight_word_index and mapping["line_idx"] == i:
                    highlight_local_idx = mapping["local_idx"]
                    break
            
            words = line.split()
            current_x = text_x
            for word_idx, word in enumerate(words):
                if word_idx == highlight_local_idx:
                    word_color = hex_to_rgb(highlight_color)
                else:
                    word_color = text_rgb
                
                if stroke_color and stroke_width > 0:
                    stroke_rgb = hex_to_rgb(stroke_color)
                    draw.text((current_x, current_y), word, font=font, fill=(*word_color, 255),
                              stroke_width=stroke_width, stroke_fill=(*stroke_rgb, 255))
                else:
                    draw.text((current_x, current_y), word, font=font, fill=(*word_color, 255))
                
                word_bbox = temp_draw.textbbox((0, 0), word + " ", font=font)
                current_x += word_bbox[2] - word_bbox[0]
        else:
            if stroke_color and stroke_width > 0:
                stroke_rgb = hex_to_rgb(stroke_color)
                draw.text((text_x, current_y), line, font=font, fill=(*text_rgb, 255), 
                          stroke_width=stroke_width, stroke_fill=(*stroke_rgb, 255))
            else:
                draw.text((text_x, current_y), line, font=font, fill=(*text_rgb, 255))
        
        current_y += line_heights[i] + line_spacing
    
    import numpy as np
    img_array = np.array(img)
    
    clip = MoviePyImageClip(img_array, ismask=False, transparent=True)
    
    if x is not None:
        final_x = x
    elif align == 'center':
        final_x = (video_width - total_width) // 2
    elif align == 'left':
        final_x = margin
    elif align == 'right':
        final_x = video_width - total_width - margin
    else:
        final_x = (video_width - total_width) // 2
    
    clip = clip.set_position((final_x, y))
    clip = clip.set_start(start)
    clip = clip.set_duration(end - start)
    
    return clip

def download_audio_file(url: str, save_path: Path) -> Path:
    """Download audio file."""
    try:
        response = requests.get(url, headers=DOWNLOAD_HEADERS, timeout=60, stream=True)
        response.raise_for_status()
        
        content_type = response.headers.get('content-type', '')
        if 'text/html' in content_type.lower():
            raise ValueError(f"URL returned HTML instead of audio")
        
        ext = os.path.splitext(url.split('?')[0])[1] or '.mp3'
        audio_path = save_path.with_suffix(ext)
        
        with open(audio_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        file_size = audio_path.stat().st_size
        if file_size < 1000:
            raise ValueError(f"Downloaded file too small ({file_size} bytes)")
        
        return audio_path
        
    except requests.exceptions.RequestException as e:
        raise ValueError(f"Download failed: {str(e)}")

def create_image_overlay_clip(overlay: dict, video_width: int, video_height: int):
    """Create an ImageClip from an image URL with positioning and timing."""
    from moviepy.editor import ImageClip as MoviePyImageClip
    from PIL import Image as PILImage
    import numpy as np
    
    image_url = overlay['image_url']
    start = overlay['start']
    end = overlay['end']
    x = overlay.get('x')
    y = overlay.get('y', 0)
    width = overlay.get('width')
    height = overlay.get('height')
    opacity = overlay.get('opacity', 1.0)
    align = overlay.get('align', 'center')
    
    response = requests.get(str(image_url), headers=DOWNLOAD_HEADERS, timeout=30)
    response.raise_for_status()
    
    img = PILImage.open(io.BytesIO(response.content)).convert('RGBA')
    
    orig_width, orig_height = img.size
    
    if width and height:
        img = img.resize((width, height), PILImage.Resampling.LANCZOS)
    elif width:
        ratio = width / orig_width
        new_height = int(orig_height * ratio)
        img = img.resize((width, new_height), PILImage.Resampling.LANCZOS)
    elif height:
        ratio = height / orig_height
        new_width = int(orig_width * ratio)
        img = img.resize((new_width, height), PILImage.Resampling.LANCZOS)
    
    if opacity < 1.0:
        alpha = img.split()[3]
        alpha = alpha.point(lambda p: int(p * opacity))
        img.putalpha(alpha)
    
    img_array = np.array(img)
    clip = MoviePyImageClip(img_array, ismask=False, transparent=True)
    
    img_width, img_height = img.size
    
    if x is not None:
        final_x = x
    elif align == 'center':
        final_x = (video_width - img_width) // 2
    elif align == 'left':
        final_x = 0
    elif align == 'right':
        final_x = video_width - img_width
    else:
        final_x = (video_width - img_width) // 2
    
    if y is None:
        final_y = (video_height - img_height) // 2
    else:
        final_y = y
    
    clip = clip.set_position((final_x, final_y))
    clip = clip.set_start(start)
    clip = clip.set_duration(end - start)
    
    return clip


def mix_sound_effects(main_audio_path: Path, sound_effects: List[dict], output_path: Path, temp_dir: Path):
    """Mix sound effects into the main audio track using pydub."""
    from pydub import AudioSegment
    
    main_audio = AudioSegment.from_file(str(main_audio_path))
    
    for idx, effect in enumerate(sound_effects):
        try:
            effect_url = str(effect['audio_url'])
            start_ms = int(effect['start'] * 1000)
            volume = effect.get('volume', 1.0)
            
            effect_path = temp_dir / f"effect_{idx}.mp3"
            response = requests.get(effect_url, headers=DOWNLOAD_HEADERS, timeout=30)
            response.raise_for_status()
            
            with open(effect_path, 'wb') as f:
                f.write(response.content)
            
            effect_audio = AudioSegment.from_file(str(effect_path))
            
            if volume != 1.0:
                import math
                db_change = 20 * math.log10(volume)
                effect_audio = effect_audio + db_change
            
            if start_ms < len(main_audio):
                main_audio = main_audio.overlay(effect_audio, position=start_ms)
            
        except Exception as e:
            print(f"Warning: Failed to add sound effect {idx}: {e}")
    
    main_audio.export(str(output_path), format="mp3")
    return output_path


def process_concat_job(job_id: str, video_urls: List[str], base_url: str, audio_url: Optional[str] = None, overlays: Optional[List[dict]] = None, image_overlays: Optional[List[dict]] = None, sound_effects: Optional[List[dict]] = None):
    """Background task to concatenate videos with optional overlays."""
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
                jobs_store[job_id]["progress"] = int((idx + 1) / total_videos * 40)
                jobs_store[job_id]["message"] = f"Downloaded {idx + 1}/{total_videos} videos"
            except ValueError as e:
                jobs_store[job_id]["status"] = "failed"
                jobs_store[job_id]["error"] = f"Video {idx}: {str(e)}"
                return
        
        if not downloaded_videos:
            jobs_store[job_id]["status"] = "failed"
            jobs_store[job_id]["error"] = "No videos were successfully downloaded"
            return
        
        audio_path = None
        if audio_url:
            try:
                jobs_store[job_id]["progress"] = 45
                jobs_store[job_id]["message"] = "Downloading audio..."
                audio_path = download_audio_file(audio_url, temp_session_dir / "audio")
            except ValueError as e:
                jobs_store[job_id]["status"] = "failed"
                jobs_store[job_id]["error"] = f"Audio download failed: {str(e)}"
                return
        
        jobs_store[job_id]["status"] = "processing"
        jobs_store[job_id]["progress"] = 50
        jobs_store[job_id]["message"] = "Concatenating videos..."
        
        video_filename = f"concat_{job_id}.mp4"
        output_path = OUTPUT_DIR / video_filename
        
        try:
            has_overlays = (overlays and len(overlays) > 0) or (image_overlays and len(image_overlays) > 0)
            
            if has_overlays:
                jobs_store[job_id]["message"] = "Processing with overlays (re-encoding)..."
                
                video_clips = []
                for video_path in downloaded_videos:
                    clip = VideoFileClip(str(video_path))
                    video_clips.append(clip)
                
                concatenated = concatenate_videoclips(video_clips, method="compose")
                
                video_width = int(concatenated.w)
                video_height = int(concatenated.h)
                
                all_overlay_clips = []
                
                if image_overlays:
                    for img_overlay in image_overlays:
                        try:
                            img_clip = create_image_overlay_clip(img_overlay, video_width, video_height)
                            all_overlay_clips.append(img_clip)
                        except Exception as e:
                            print(f"Warning: Failed to create image overlay: {e}")
                
                if overlays:
                    for overlay in overlays:
                        try:
                            overlay_clip = create_text_clip_with_background(overlay, video_width, video_height)
                            all_overlay_clips.append(overlay_clip)
                        except Exception as e:
                            print(f"Warning: Failed to create text overlay: {e}")
                
                if all_overlay_clips:
                    final_video = CompositeVideoClip([concatenated] + all_overlay_clips)
                else:
                    final_video = concatenated
                
                final_audio_path = audio_path
                if audio_url and audio_path and sound_effects and len(sound_effects) > 0:
                    jobs_store[job_id]["message"] = "Mixing sound effects..."
                    mixed_audio_path = temp_session_dir / "mixed_audio.mp3"
                    final_audio_path = mix_sound_effects(audio_path, sound_effects, mixed_audio_path, temp_session_dir)
                
                if audio_url and final_audio_path:
                    custom_audio = AudioFileClip(str(final_audio_path))
                    final_video = final_video.set_audio(custom_audio)
                
                final_video.write_videofile(
                    str(output_path),
                    codec='libx264',
                    audio_codec='aac',
                    fps=24,
                    preset='medium',
                    bitrate='8000k',
                    ffmpeg_params=['-crf', '18'],
                    logger=None
                )
                
                for clip in video_clips:
                    clip.close()
                final_video.close()
                if audio_url and final_audio_path:
                    custom_audio.close()
            else:
                concat_list_path = temp_session_dir / "concat_list.txt"
                with open(concat_list_path, 'w') as f:
                    for video_path in downloaded_videos:
                        f.write(f"file '{video_path.absolute()}'\n")
                
                final_audio_path = audio_path
                if audio_url and audio_path and sound_effects and len(sound_effects) > 0:
                    jobs_store[job_id]["message"] = "Mixing sound effects..."
                    mixed_audio_path = temp_session_dir / "mixed_audio.mp3"
                    final_audio_path = mix_sound_effects(audio_path, sound_effects, mixed_audio_path, temp_session_dir)
                
                if audio_url and final_audio_path:
                    cmd = [
                        'ffmpeg', '-y',
                        '-f', 'concat',
                        '-safe', '0',
                        '-i', str(concat_list_path),
                        '-i', str(final_audio_path),
                        '-map', '0:v',
                        '-map', '1:a',
                        '-c:v', 'copy',
                        '-c:a', 'aac',
                        '-shortest',
                        '-movflags', '+faststart',
                        str(output_path)
                    ]
                else:
                    cmd = [
                        'ffmpeg', '-y',
                        '-f', 'concat',
                        '-safe', '0',
                        '-i', str(concat_list_path),
                        '-c:v', 'copy',
                        '-c:a', 'copy',
                        '-movflags', '+faststart',
                        str(output_path)
                    ]
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                
                if result.returncode != 0:
                    if audio_url and final_audio_path:
                        cmd_reencode = [
                            'ffmpeg', '-y',
                            '-f', 'concat',
                            '-safe', '0',
                            '-i', str(concat_list_path),
                            '-i', str(final_audio_path),
                            '-map', '0:v',
                            '-map', '1:a',
                            '-c:v', 'libx264',
                            '-c:a', 'aac',
                            '-preset', 'fast',
                            '-shortest',
                            '-movflags', '+faststart',
                            str(output_path)
                        ]
                    else:
                        cmd_reencode = [
                            'ffmpeg', '-y',
                            '-f', 'concat',
                            '-safe', '0',
                            '-i', str(concat_list_path),
                            '-c:v', 'libx264',
                            '-c:a', 'aac',
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
    """
    Start async video concatenation job. Returns job_id immediately.
    
    Supports 4 modes:
    - Only video_urls: Concatenate videos keeping original audio
    - video_urls + audio_url: Concatenate videos and replace audio
    - video_urls + overlays: Concatenate videos with text overlays (uses original audio)
    - video_urls + audio_url + overlays: Full customization
    """
    
    if len(request.video_urls) < 2:
        raise HTTPException(
            status_code=400,
            detail="At least 2 video URLs are required"
        )
    
    job_id = str(uuid.uuid4())
    base_url = str(req.base_url).rstrip('/')
    
    audio_url_str = str(request.audio_url) if request.audio_url else None
    
    overlays_list = None
    if request.overlays:
        overlays_list = [overlay.model_dump() for overlay in request.overlays]
    
    image_overlays_list = None
    if request.image_overlays:
        image_overlays_list = [img_overlay.model_dump() for img_overlay in request.image_overlays]
    
    sound_effects_list = None
    if request.sound_effects:
        sound_effects_list = [effect.model_dump() for effect in request.sound_effects]
    
    jobs_store[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "message": "Job queued",
        "total_videos": len(request.video_urls),
        "has_custom_audio": audio_url_str is not None,
        "has_overlays": overlays_list is not None and len(overlays_list) > 0,
        "has_image_overlays": image_overlays_list is not None and len(image_overlays_list) > 0,
        "has_sound_effects": sound_effects_list is not None and len(sound_effects_list) > 0,
        "created_at": datetime.now().isoformat(),
        "video_filename": None,
        "download_url": None,
        "error": None
    }
    
    video_urls = [str(url) for url in request.video_urls]
    thread = threading.Thread(
        target=process_concat_job, 
        args=(job_id, video_urls, base_url, audio_url_str, overlays_list, image_overlays_list, sound_effects_list)
    )
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

def find_nearest_silence(target_ms: float, silence_ranges: List[tuple], tolerance_ms: float = 2000) -> Optional[float]:
    """Find the silence point nearest to target_ms within tolerance."""
    best_point = None
    best_distance = float('inf')
    
    for start, end in silence_ranges:
        midpoint = (start + end) / 2
        distance = abs(midpoint - target_ms)
        
        if distance < best_distance and distance <= tolerance_ms:
            best_distance = distance
            best_point = midpoint
    
    return best_point

def smart_split_audio(audio: AudioSegment, num_parts: int, min_silence_len: int = 300, silence_thresh: int = -40) -> List[tuple]:
    """
    Split audio into N parts, cutting at silence points when possible.
    Returns list of (start_ms, end_ms) tuples.
    """
    total_duration = len(audio)
    ideal_segment_length = total_duration / num_parts
    
    silence_ranges = detect_silence(
        audio,
        min_silence_len=min_silence_len,
        silence_thresh=silence_thresh,
        seek_step=10
    )
    
    segments = []
    current_start = 0
    
    for i in range(num_parts - 1):
        ideal_end = current_start + ideal_segment_length
        
        tolerance = ideal_segment_length * 0.4
        
        silence_point = find_nearest_silence(ideal_end, silence_ranges, tolerance)
        
        if silence_point is not None:
            actual_end = silence_point
        else:
            actual_end = ideal_end
        
        actual_end = min(actual_end, total_duration)
        
        segments.append((current_start, actual_end))
        current_start = actual_end
    
    segments.append((current_start, total_duration))
    
    return segments

@app.post("/split_audio/")
async def split_audio(request: SplitAudioRequest, req: Request):
    """
    Split audio into N parts, cutting at natural silence/pause points.
    Returns URLs to download each audio segment.
    """
    temp_session_dir = TEMP_DIR / str(uuid.uuid4())
    temp_session_dir.mkdir(parents=True, exist_ok=True)
    
    split_id = str(uuid.uuid4())[:8]
    
    try:
        try:
            response = requests.get(str(request.audio_url), headers=DOWNLOAD_HEADERS, timeout=60)
            response.raise_for_status()
            
            content_type = response.headers.get('content-type', '')
            if 'text/html' in content_type.lower():
                raise ValueError("URL returned HTML instead of audio")
            
            ext = os.path.splitext(str(request.audio_url).split('?')[0])[1] or '.mp3'
            audio_path = temp_session_dir / f"original{ext}"
            
            with open(audio_path, 'wb') as f:
                f.write(response.content)
                
            if audio_path.stat().st_size < 1000:
                raise ValueError("Downloaded file too small, likely not valid audio")
                
        except requests.exceptions.RequestException as e:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to download audio: {str(e)}"
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        
        try:
            audio = AudioSegment.from_file(str(audio_path))
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to process audio file: {str(e)}"
            )
        
        total_duration_ms = len(audio)
        total_duration_sec = total_duration_ms / 1000.0
        
        segments = smart_split_audio(
            audio,
            request.parts,
            min_silence_len=request.min_silence_len,
            silence_thresh=request.silence_thresh
        )
        
        base_url = str(req.base_url).rstrip('/')
        segment_results = []
        
        for idx, (start_ms, end_ms) in enumerate(segments):
            segment_audio = audio[start_ms:end_ms]
            
            segment_filename = f"segment_{split_id}_{idx + 1}.mp3"
            segment_path = OUTPUT_DIR / segment_filename
            
            segment_audio.export(str(segment_path), format="mp3", bitrate="192k")
            
            segment_results.append({
                "index": idx + 1,
                "start": round(start_ms / 1000.0, 2),
                "end": round(end_ms / 1000.0, 2),
                "duration": round((end_ms - start_ms) / 1000.0, 2),
                "filename": segment_filename,
                "download_url": f"{base_url}/audios/{segment_filename}"
            })
        
        return JSONResponse(content={
            "message": "Audio split successfully",
            "split_id": split_id,
            "original_duration": round(total_duration_sec, 2),
            "requested_parts": request.parts,
            "actual_parts": len(segments),
            "segments": segment_results
        })
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")
    
    finally:
        if temp_session_dir.exists():
            try:
                shutil.rmtree(temp_session_dir)
            except:
                pass

@app.get("/audios/{filename}")
async def get_audio(filename: str):
    """Download a generated audio segment."""
    audio_path = OUTPUT_DIR / filename
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(audio_path, media_type="audio/mpeg", filename=filename)


class KaraokeRequest(BaseModel):
    audio_url: HttpUrl
    script: Optional[str] = None
    mode: str = "word"
    words_per_line: int = 5
    x: Optional[int] = None
    y: int = 900
    font_size: int = 48
    font_color: str = "#FFFFFF"
    highlight_color: str = "#FFFFFF"
    inactive_color: str = "#666666"
    stroke_color: Optional[str] = "#000000"
    stroke_width: int = 2
    background_color: Optional[str] = None
    background_opacity: float = 0.7
    padding: int = 10
    align: str = "center"
    style_prompt: Optional[str] = None


@app.post("/generate_karaoke_subtitles/")
async def generate_karaoke_subtitles(request: KaraokeRequest, req: Request):
    """
    Generate karaoke-style subtitles where words appear one by one.
    Uses OpenAI Whisper to get word-level timestamps.
    If 'script' is provided, uses your exact text with Whisper's timestamps.
    Returns overlays ready to use in /concat_videos/
    """
    openai_api_key = os.environ.get("OPENAI_API_KEY")
    if not openai_api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured")
    
    temp_session_dir = TEMP_DIR / f"karaoke_{uuid.uuid4().hex[:8]}"
    temp_session_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        response = requests.get(str(request.audio_url), headers=DOWNLOAD_HEADERS, timeout=120)
        response.raise_for_status()
        
        audio_path = temp_session_dir / "audio.mp3"
        with open(audio_path, 'wb') as f:
            f.write(response.content)
        
        client = OpenAI(api_key=openai_api_key)
        
        with open(audio_path, 'rb') as audio_file:
            transcription = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="verbose_json",
                timestamp_granularities=["word"]
            )
        
        whisper_words = transcription.words if hasattr(transcription, 'words') else []
        
        if not whisper_words:
            raise HTTPException(status_code=400, detail="Could not extract word timestamps from audio")
        
        if request.script:
            import re
            script_words = re.findall(r'\S+', request.script)
            
            if len(script_words) != len(whisper_words):
                if abs(len(script_words) - len(whisper_words)) <= 3:
                    min_len = min(len(script_words), len(whisper_words))
                    script_words = script_words[:min_len]
                    whisper_words = whisper_words[:min_len]
                else:
                    raise HTTPException(
                        status_code=400, 
                        detail=f"Word count mismatch: script has {len(script_words)} words, audio has {len(whisper_words)} words. They must match for accurate sync."
                    )
            
            final_words = []
            for i, script_word in enumerate(script_words):
                whisper_data = whisper_words[i]
                start = whisper_data.start if hasattr(whisper_data, 'start') else whisper_data.get('start', 0)
                end = whisper_data.end if hasattr(whisper_data, 'end') else whisper_data.get('end', start + 0.3)
                final_words.append({
                    "word": script_word,
                    "start": start,
                    "end": end
                })
            full_text = request.script
        else:
            final_words = []
            for wd in whisper_words:
                word = wd.word if hasattr(wd, 'word') else wd.get('word', '')
                start = wd.start if hasattr(wd, 'start') else wd.get('start', 0)
                end = wd.end if hasattr(wd, 'end') else wd.get('end', start + 0.3)
                final_words.append({"word": word, "start": start, "end": end})
            full_text = transcription.text if hasattr(transcription, 'text') else ""
        
        overlays = []
        
        style_config = None
        if request.style_prompt:
            try:
                style_response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "system",
                            "content": """You are a subtitle style generator. Given a style description, return a JSON object with subtitle styling.
Only return valid JSON with these fields (use hex colors):
- font_color: main text color (hex like #FFFFFF)
- stroke_color: text outline color (hex like #000000)  
- stroke_width: outline thickness (1-4)
- font_size: size in pixels (36-72)
Keep it professional and readable. No background colors unless specifically requested."""
                        },
                        {
                            "role": "user", 
                            "content": f"Generate subtitle style for: {request.style_prompt}"
                        }
                    ],
                    response_format={"type": "json_object"}
                )
                import json
                style_config = json.loads(style_response.choices[0].message.content)
            except Exception as e:
                print(f"Style generation failed, using defaults: {e}")
        
        if request.mode == "highlight":
            lines = []
            current_line = []
            for word_data in final_words:
                current_line.append(word_data)
                if len(current_line) >= request.words_per_line:
                    lines.append(current_line)
                    current_line = []
            if current_line:
                lines.append(current_line)
            
            for line_words in lines:
                line_text_parts = [w["word"] for w in line_words]
                
                for i, word_data in enumerate(line_words):
                    start = word_data["start"]
                    
                    if i + 1 < len(line_words):
                        end = line_words[i + 1]["start"]
                    else:
                        end = word_data["end"]
                    
                    highlight_index = i
                    
                    overlay = {
                        "text": " ".join(line_text_parts),
                        "start": round(start, 2),
                        "end": round(end, 2),
                        "y": request.y,
                        "font_size": style_config.get("font_size", request.font_size) if style_config else request.font_size,
                        "font_color": request.inactive_color,
                        "highlight_word_index": highlight_index,
                        "highlight_color": request.highlight_color,
                        "align": request.align
                    }
                    
                    if request.x is not None:
                        overlay["x"] = request.x
                    
                    stroke_color = style_config.get("stroke_color", request.stroke_color) if style_config else request.stroke_color
                    stroke_width = style_config.get("stroke_width", request.stroke_width) if style_config else request.stroke_width
                    
                    if stroke_color:
                        overlay["stroke_color"] = stroke_color
                        overlay["stroke_width"] = stroke_width
                    
                    if request.background_color:
                        overlay["background_color"] = request.background_color
                        overlay["background_opacity"] = request.background_opacity
                        overlay["padding"] = request.padding
                    
                    overlays.append(overlay)
        else:
            for i, word_data in enumerate(final_words):
                word = word_data["word"]
                start = word_data["start"]
                
                if i + 1 < len(final_words):
                    end = final_words[i + 1]["start"]
                else:
                    end = word_data["end"]
                
                overlay = {
                    "text": word,
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "y": request.y,
                    "font_size": style_config.get("font_size", request.font_size) if style_config else request.font_size,
                    "font_color": style_config.get("font_color", request.font_color) if style_config else request.font_color,
                    "align": request.align
                }
                
                if request.x is not None:
                    overlay["x"] = request.x
                
                stroke_color = style_config.get("stroke_color", request.stroke_color) if style_config else request.stroke_color
                stroke_width = style_config.get("stroke_width", request.stroke_width) if style_config else request.stroke_width
                
                if stroke_color:
                    overlay["stroke_color"] = stroke_color
                    overlay["stroke_width"] = stroke_width
                
                if request.background_color:
                    overlay["background_color"] = request.background_color
                    overlay["background_opacity"] = request.background_opacity
                    overlay["padding"] = request.padding
                
                overlays.append(overlay)
        
        return JSONResponse(content={
            "message": "Karaoke subtitles generated successfully",
            "mode": request.mode,
            "total_words": len(final_words),
            "script_provided": request.script is not None,
            "style_applied": style_config is not None,
            "style_config": style_config,
            "full_text": full_text,
            "overlays": overlays
        })
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating karaoke subtitles: {str(e)}")
    
    finally:
        if temp_session_dir.exists():
            try:
                shutil.rmtree(temp_session_dir)
            except:
                pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
