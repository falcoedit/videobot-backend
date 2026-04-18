#!/usr/bin/env python3
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp
import os
import threading
import time
import requests

app = Flask(__name__)
CORS(app)

# Render-safe directory
DOWNLOAD_DIR = "/tmp/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

downloads = {}

# ----------------------------
# Helpers
# ----------------------------

def is_direct_video(url):
    direct_ext = ['.mp4', '.m3u8', '.webm', '.mov', '.mkv']
    return any(url.lower().split('?')[0].endswith(ext) for ext in direct_ext)


def get_video_info(url):
    try:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                'title': info.get('title', 'Unknown'),
                'duration': info.get('duration', 0),
                'thumbnail': info.get('thumbnail', ''),
                'uploader': info.get('uploader', 'Unknown')
            }
    except:
        return None


# ----------------------------
# Direct Download (no yt-dlp)
# ----------------------------

def direct_download(url, download_id):
    try:
        local_filename = os.path.join(DOWNLOAD_DIR, f"{download_id}.mp4")

        with requests.get(url, stream=True) as r:
            total = int(r.headers.get('content-length', 0))
            downloaded = 0

            with open(local_filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

                        if total > 0:
                            downloads[download_id]['status'] = 'downloading'
                            downloads[download_id]['progress'] = (downloaded / total) * 100

        downloads[download_id].update({
            'status': 'completed',
            'progress': 100,
            'filename': os.path.basename(local_filename),
            'path': local_filename
        })

    except Exception as e:
        downloads[download_id].update({
            'status': 'error',
            'error': str(e)
        })


# ----------------------------
# yt-dlp Download
# ----------------------------

def process_download(url, quality, download_id):

    format_map = {
        'best': 'bestvideo+bestaudio/best',
        '1080p': 'bestvideo[height<=1080]+bestaudio/best',
        '720p': 'bestvideo[height<=720]+bestaudio/best',
        '480p': 'bestvideo[height<=480]+bestaudio/best',
        '360p': 'bestvideo[height<=360]+bestaudio/best',
        'audio': 'bestaudio/best'
    }

    fmt = format_map.get(quality, 'bestvideo+bestaudio/best')
    is_audio = quality == 'audio'

    def progress_hook(d):
        if d['status'] == 'downloading':
            downloads[download_id]['status'] = 'downloading'
            downloads[download_id]['speed'] = d.get('speed_string', 'N/A')

            if d.get('total_bytes'):
                downloads[download_id]['progress'] = (
                    d['downloaded_bytes'] / d['total_bytes'] * 100
                )
            elif d.get('total_bytes_estimate'):
                downloads[download_id]['progress'] = (
                    d['downloaded_bytes'] / d['total_bytes_estimate'] * 100
                )

        elif d['status'] == 'finished':
            downloads[download_id]['status'] = 'processing'

    output_template = os.path.join(DOWNLOAD_DIR, '%(title).50s.%(ext)s')

    opts = {
        'format': fmt,
        'outtmpl': output_template,
        'progress_hooks': [progress_hook],
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'retries': 3,
        'fragment_retries': 3
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)

            if is_audio:
                filename = filename.replace('.webm', '.mp3').replace('.m4a', '.mp3')

            downloads[download_id].update({
                'status': 'completed',
                'progress': 100,
                'filename': os.path.basename(filename),
                'path': filename
            })

    except Exception as e:
        downloads[download_id].update({
            'status': 'error',
            'error': str(e)
        })


# ----------------------------
# Routes
# ----------------------------

@app.route('/')
def index():
    return "Backend is running"


@app.route('/api/info', methods=['POST'])
def video_info():
    url = request.json.get('url', '')
    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    info = get_video_info(url)
    if info:
        return jsonify(info)

    return jsonify({'error': 'Failed to fetch video info'}), 400


@app.route('/api/download', methods=['POST'])
def download():
    data = request.json
    url = data.get('url')
    quality = data.get('quality', 'best')

    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    download_id = str(int(time.time()))

    downloads[download_id] = {
        'status': 'starting',
        'progress': 0,
        'filename': None,
        'speed': 'N/A'
    }

    # 🔥 AUTO SWITCH
    if is_direct_video(url):
        thread = threading.Thread(
            target=direct_download,
            args=(url, download_id)
        )
    else:
        thread = threading.Thread(
            target=process_download,
            args=(url, quality, download_id)
        )

    thread.start()

    return jsonify({'download_id': download_id})


@app.route('/api/progress/<download_id>')
def check_progress(download_id):
    return jsonify(downloads.get(download_id, {'status': 'unknown'}))


@app.route('/api/downloads')
def list_downloads():
    files = []
    for f in os.listdir(DOWNLOAD_DIR):
        path = os.path.join(DOWNLOAD_DIR, f)
        files.append({
            'name': f,
            'size': os.path.getsize(path),
            'date': os.path.getmtime(path)
        })
    return jsonify(sorted(files, key=lambda x: x['date'], reverse=True))


@app.route('/api/file/<filename>')
def serve_file(filename):
    return send_file(
        os.path.join(DOWNLOAD_DIR, filename),
        as_attachment=True
    )


# ----------------------------
# Cleanup
# ----------------------------

def cleanup_old_files():
    while True:
        time.sleep(3600)
        for f in os.listdir(DOWNLOAD_DIR):
            path = os.path.join(DOWNLOAD_DIR, f)
            if time.time() - os.path.getmtime(path) > 3600:
                try:
                    os.remove(path)
                except:
                    pass

threading.Thread(target=cleanup_old_files, daemon=True).start()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)