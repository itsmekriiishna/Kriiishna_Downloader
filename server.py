from flask import Flask, request, jsonify, Response, send_file
from flask_cors import CORS
import yt_dlp
import re
import os
import tempfile
import uuid
import urllib.request

app = Flask(__name__)
CORS(app)

# Set ffmpeg path for yt-dlp
import imageio_ffmpeg
FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
FFMPEG_DIR = os.path.dirname(FFMPEG_PATH)

DOWNLOAD_DIR = os.path.join(tempfile.gettempdir(), "download_media")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


@app.route("/")
def serve_index():
    return send_file(os.path.join(os.path.dirname(__file__), "index.html"))


@app.route("/api/thumb")
def proxy_thumbnail():
    img_url = request.args.get("url", "")
    if not img_url:
        return "", 404
    try:
        req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
            content_type = resp.headers.get("Content-Type", "image/jpeg")
        return Response(data, mimetype=content_type)
    except Exception:
        return "", 404


def detect_platform(url):
    if re.search(r'(youtube\.com|youtu\.be)', url):
        return "youtube"
    if re.search(r'(instagram\.com)', url):
        return "instagram"
    if re.search(r'(pinterest\.com|pin\.it)', url):
        return "pinterest"
    return None


def is_valid_url(url):
    return detect_platform(url) is not None


# ─── YouTube Info ───
def get_youtube_info(url):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ffmpeg_location": FFMPEG_DIR,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    duration = info.get("duration", 0)

    def get_filesize(f):
        size = f.get("filesize") or f.get("filesize_approx")
        if size:
            return size
        tbr = f.get("tbr")
        if tbr and duration:
            return int(tbr * 1000 / 8 * duration)
        return None

    formats = []
    seen_qualities = set()

    best_audio_size = 0
    for f in info.get("formats", []):
        if f.get("acodec") != "none" and f.get("vcodec") == "none":
            s = get_filesize(f)
            if s and s > best_audio_size:
                best_audio_size = s

    for f in reversed(info.get("formats", [])):
        height = f.get("height")
        if not height or f.get("vcodec") == "none":
            continue
        label = f"{height}p"
        if label in seen_qualities:
            continue
        seen_qualities.add(label)
        video_size = get_filesize(f)
        total_size = (video_size + best_audio_size) if video_size else None
        formats.append({
            "quality": label,
            "ext": "mp4",
            "filesize": total_size,
            "type": "video",
        })

    best_audio = None
    for f in reversed(info.get("formats", [])):
        if f.get("acodec") != "none" and f.get("vcodec") == "none":
            best_audio = f
            break

    if best_audio:
        formats.append({
            "quality": "Audio Only",
            "ext": "mp3",
            "filesize": get_filesize(best_audio),
            "type": "audio",
        })

    def sort_key(f):
        if f["type"] == "audio":
            return 0
        q = f["quality"].replace("p", "")
        return int(q) if q.isdigit() else 0

    formats.sort(key=sort_key, reverse=True)

    return {
        "title": info.get("title", "Unknown"),
        "description": info.get("description", ""),
        "tags": info.get("tags", []),
        "thumbnail": info.get("thumbnail", ""),
        "duration": duration,
        "channel": info.get("channel", info.get("uploader", "Unknown")),
        "platform": "youtube",
        "formats": formats,
    }


# ─── Instagram Info ───
def get_instagram_info(url):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ffmpeg_location": FFMPEG_DIR,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    # Instagram can return a playlist (carousel) or single post
    entries = []
    if info.get("_type") == "playlist":
        entries = info.get("entries", [])
    else:
        entries = [info]

    formats = []
    for i, entry in enumerate(entries):
        # Determine if it's video or image
        is_video = entry.get("ext") not in ("jpg", "png", "webp") and entry.get("formats")

        if is_video:
            # Get best quality video
            best = None
            for f in reversed(entry.get("formats", [])):
                if f.get("vcodec") != "none":
                    best = f
                    break
            if best:
                height = best.get("height", 0)
                filesize = best.get("filesize") or best.get("filesize_approx")
                label = f"Video {i+1}" if len(entries) > 1 else "Video"
                formats.append({
                    "quality": f"{height}p" if height else label,
                    "ext": "mp4",
                    "filesize": filesize,
                    "type": "video",
                    "index": i,
                })
        else:
            # Image post
            label = f"Image {i+1}" if len(entries) > 1 else "Image"
            formats.append({
                "quality": label,
                "ext": "jpg",
                "filesize": None,
                "type": "image",
                "index": i,
            })

    description = info.get("description", "")
    title = info.get("title") or ""
    if not title and description:
        title = description[:60] + ("..." if len(description) > 60 else "")
    elif not title:
        title = "Instagram Post"

    # Extract hashtags from description as tags
    tags = re.findall(r'#(\w+)', description) if description else []

    return {
        "title": title,
        "description": description,
        "tags": tags,
        "thumbnail": info.get("thumbnail", entries[0].get("thumbnail", "") if entries else ""),
        "duration": info.get("duration", 0),
        "channel": info.get("channel", info.get("uploader", "Unknown")),
        "platform": "instagram",
        "formats": formats,
    }


# ─── Pinterest Info ───
def get_pinterest_info(url):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ffmpeg_location": FFMPEG_DIR,
    }

    info = {}
    is_video = False
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        is_video = True
    except Exception:
        pass

    # Scrape page for image URL and metadata
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        # Find original quality image
        orig_match = re.search(r'https://i\.pinimg\.com/originals/[^"\\s]+', html)
        # Find any pinimg URL as fallback
        any_match = re.search(r'https://i\.pinimg\.com/[^"\\s]+', html)

        image_url = ""
        if orig_match:
            image_url = orig_match.group(0).split(")")[0].split("}")[0]
        elif any_match:
            image_url = any_match.group(0).split(")")[0].split("}")[0]

        # Also try to get 236x as thumbnail
        thumb_match = re.search(r'https://i\.pinimg\.com/236x/[^"\\s]+', html)
        thumbnail = thumb_match.group(0).split(")")[0] if thumb_match else image_url

        if not info.get("title"):
            title_match = re.search(r'<title>([^<]+)</title>', html)
            info["title"] = title_match.group(1).replace(" | Pinterest", "").strip() if title_match else ""

        if not info.get("description"):
            desc_match = re.search(r'"description":"([^"]*)"', html)
            info["description"] = desc_match.group(1) if desc_match else ""

        info["_image_url"] = image_url
        info["thumbnail"] = info.get("thumbnail") or thumbnail
    except Exception:
        pass

    description = info.get("description", "")
    title = info.get("title") or ""
    if not title and description:
        title = description[:60] + ("..." if len(description) > 60 else "")
    elif not title:
        title = "Pinterest Pin"

    tags = re.findall(r'#(\w+)', description) if description else []

    formats = []

    if is_video:
        best = None
        for f in reversed(info.get("formats", [])):
            if f.get("vcodec") != "none":
                best = f
                break
        if best:
            height = best.get("height", 0)
            filesize = best.get("filesize") or best.get("filesize_approx")
            formats.append({
                "quality": f"{height}p" if height else "Video",
                "ext": "mp4",
                "filesize": filesize,
                "type": "video",
            })

    # Always add image option
    if info.get("thumbnail") or info.get("_image_url"):
        formats.append({
            "quality": "Image",
            "ext": "jpg",
            "filesize": None,
            "type": "image",
            "image_url": info.get("_image_url") or info.get("thumbnail", ""),
        })

    return {
        "title": title,
        "description": description,
        "tags": tags,
        "thumbnail": info.get("thumbnail", ""),
        "duration": info.get("duration", 0),
        "channel": info.get("channel", info.get("uploader", "Unknown")),
        "platform": "pinterest",
        "formats": formats,
    }


# ─── API: Get Info ───
@app.route("/api/info", methods=["POST"])
def get_video_info():
    data = request.get_json()
    url = data.get("url", "").strip()

    if not url or not is_valid_url(url):
        return jsonify({"error": "Please enter a valid URL (YouTube, Instagram, or Pinterest)"}), 400

    try:
        platform = detect_platform(url)
        if platform == "youtube":
            result = get_youtube_info(url)
        elif platform == "instagram":
            result = get_instagram_info(url)
        else:
            result = get_pinterest_info(url)
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── API: Download ───
@app.route("/api/download", methods=["POST"])
def download_video():
    data = request.get_json()
    url = data.get("url", "").strip()
    quality = data.get("quality", "").strip()
    dl_type = data.get("type", "video")

    if not url or not is_valid_url(url):
        return jsonify({"error": "Invalid URL"}), 400

    if not quality:
        return jsonify({"error": "Quality required"}), 400

    try:
        platform = detect_platform(url)
        temp_id = str(uuid.uuid4())
        output_path = os.path.join(DOWNLOAD_DIR, temp_id)

        if platform == "youtube":
            if dl_type == "audio":
                ydl_opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "format": "bestaudio/best",
                    "ffmpeg_location": FFMPEG_DIR,
                    "outtmpl": output_path + ".%(ext)s",
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                }
            else:
                height = quality.replace("p", "")
                ydl_opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "format": f"bestvideo[height<={height}]+bestaudio/best[height<={height}]",
                    "ffmpeg_location": FFMPEG_DIR,
                    "outtmpl": output_path + ".%(ext)s",
                    "merge_output_format": "mp4",
                }
        else:
            # Instagram / Pinterest
            if dl_type == "image":
                # Download image directly
                image_url = data.get("image_url", "")
                title = "download"

                if not image_url or platform == "pinterest":
                    pin_info = get_pinterest_info(url)
                    title = re.sub(r'[^\w\s-]', '', pin_info.get("title", "download") or "download").strip()
                    if not image_url:
                        for fmt in pin_info.get("formats", []):
                            if fmt.get("type") == "image" and fmt.get("image_url"):
                                image_url = fmt["image_url"]
                                break
                    if not image_url:
                        return jsonify({"error": "Image URL not found"}), 400

                if not title:
                    title = "download"

                req = urllib.request.Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    img_data = resp.read()

                # Detect actual extension from URL
                ext = "jpg"
                if ".png" in image_url:
                    ext = "png"
                elif ".gif" in image_url:
                    ext = "gif"

                filename = f"{title[:80]}.{ext}"

                return Response(
                    img_data,
                    mimetype="application/octet-stream",
                    headers={
                        "Content-Disposition": f'attachment; filename="{filename}"',
                        "Content-Length": str(len(img_data)),
                    },
                )

            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "format": "best",
                "ffmpeg_location": FFMPEG_DIR,
                "outtmpl": output_path + ".%(ext)s",
            }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = re.sub(r'[^\w\s-]', '', info.get("title", "download") or "download").strip()
            if not title:
                title = "download"

        # Find the downloaded file
        if dl_type == "audio":
            expected_ext = "mp3"
        elif dl_type == "image":
            expected_ext = "jpg"
        else:
            expected_ext = "mp4"

        final_path = output_path + "." + expected_ext

        if not os.path.exists(final_path):
            for f in os.listdir(DOWNLOAD_DIR):
                if f.startswith(temp_id):
                    final_path = os.path.join(DOWNLOAD_DIR, f)
                    expected_ext = f.split(".")[-1]
                    break

        if not os.path.exists(final_path):
            return jsonify({"error": "Download failed - file not found"}), 500

        filename = f"{title[:80]}.{expected_ext}"
        file_size = os.path.getsize(final_path)

        def generate_and_cleanup():
            try:
                with open(final_path, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        yield chunk
            finally:
                if os.path.exists(final_path):
                    os.remove(final_path)

        return Response(
            generate_and_cleanup(),
            mimetype="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(file_size),
            },
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
