from flask import Flask, render_template, request, send_file
import yt_dlp
import uuid
import os
import hashlib
import json
import time
from threading import Thread
import queue
from waitress import serve
import logging
from datetime import datetime

# ========== KONFIGURASI ==========
app = Flask(__name__)

# Di Railway, kita pakai path di direktori kerja saat ini
DOWNLOAD_DIR = os.path.join(os.getcwd(), "downloads")
CACHE_DIR = os.path.join(os.getcwd(), "cache")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Railway environment
PORT = int(os.environ.get("PORT", 5000))

# ========== SISTEM CACHE ==========
def get_cached(url):
    """Cache sederhana berbasis file"""
    url_hash = hashlib.md5(url.encode()).hexdigest()
    cache_file = os.path.join(CACHE_DIR, f"{url_hash}.json")
    
    if os.path.exists(cache_file):
        # Cache valid selama 30 menit
        if time.time() - os.path.getmtime(cache_file) < 1800:
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
    return None

def save_cache(url, data):
    """Simpan data ke cache"""
    url_hash = hashlib.md5(url.encode()).hexdigest()
    cache_file = os.path.join(CACHE_DIR, f"{url_hash}.json")
    
    try:
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error menyimpan cache: {e}")

# ========== CLEANUP FILE LAMA ==========
def cleanup_old_files():
    """Hapus file yang lebih dari 1 jam"""
    try:
        now = time.time()
        for filename in os.listdir(DOWNLOAD_DIR):
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            if os.path.getmtime(filepath) < now - 3600:
                os.remove(filepath)
                logger.info(f"File dihapus: {filename}")
    except Exception as e:
        logger.error(f"Error cleanup: {e}")

# ========== FUNGSI DOWNLOAD TIKTOK ==========
def download_tiktok(url):
    """Fungsi utama untuk download video TikTok"""
    
    # Cek cache dulu
    cached = get_cached(url)
    if cached:
        logger.info(f"Menggunakan cache untuk: {url[:30]}...")
        return cached
    
    logger.info(f"Memulai download: {url}")
    filename = f"{uuid.uuid4()}.mp4"
    filepath = os.path.join(DOWNLOAD_DIR, filename)
    
    # Konfigurasi yt-dlp yang optimal
    ydl_opts = {
        'format': 'bv[ext=mp4][vcodec^=avc]+ba[ext=m4a]/b[ext=mp4]',
        'outtmpl': filepath,
        'quiet': True,
        'no_warnings': True,
        'concurrent_fragment_downloads': 4,
        'buffersize': 8192 * 1024,
        'postprocessors': [],
        'writethumbnail': True,
        'writeinfojson': False,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.tiktok.com/',
        },
        'extractor_args': {
            'tiktok': {'player_client': ['android']}
        },
        'socket_timeout': 10,
        'retries': 2,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            # Ambil thumbnail
            thumbnail = info.get('thumbnail')
            if not thumbnail:
                thumbnails = info.get('thumbnails', [])
                if thumbnails:
                    thumbnail = thumbnails[-1].get('url')
            
            # Format angka menjadi singkatan
            def format_num(num):
                try:
                    num = int(num)
                    if num >= 1000000:
                        return f"{num/1000000:.1f}Jt"
                    elif num >= 1000:
                        return f"{num/1000:.1f}K"
                    return str(num)
                except:
                    return "0"
            
            # Data untuk response
            data = {
                "title": info.get("title", "Video TikTok")[:100],
                "duration": info.get("duration", 0),
                "uploader": info.get("uploader", "TikTok User"),
                "like_count": format_num(info.get("like_count", 0)),
                "comment_count": format_num(info.get("comment_count", 0)),
                "repost_count": format_num(info.get("repost_count", 0)),
                "view_count": format_num(info.get("view_count", 0)),
                "thumbnail": thumbnail,
                "file": filename
            }
            
            # Bersihkan file lama
            cleanup_old_files()
            
            # Simpan ke cache
            save_cache(url, data)
            
            logger.info(f"Download berhasil: {filename}")
            return data
            
    except Exception as e:
        logger.error(f"Download gagal: {e}")
        return {"error": f"Error: {str(e)[:100]}"}

# ========== DOWNLOAD DALAM THREAD ==========
def download_in_thread(url, result_queue):
    """Download di thread terpisah"""
    try:
        result = download_tiktok(url)
        result_queue.put(result)
    except Exception as e:
        result_queue.put({"error": str(e)})

# ========== ROUTES / HALAMAN ==========
@app.route("/", methods=["GET", "POST"])
def index():
    """Halaman utama"""
    data = None
    if request.method == "POST":
        url = request.form.get("url", "").strip()
        
        if not url or "tiktok.com" not in url:
            data = {"error": "URL TikTok tidak valid"}
        else:
            # Gunakan thread agar tidak blocking
            result_queue = queue.Queue()
            thread = Thread(target=download_in_thread, args=(url, result_queue))
            thread.start()
            thread.join(timeout=25)  # Timeout 25 detik
            
            try:
                data = result_queue.get_nowait()
            except:
                data = {"error": "Timeout: Proses terlalu lama"}
    
    return render_template("index.html", data=data)

@app.route("/download/<filename>")
def download_file(filename):
    """Endpoint untuk download file"""
    filepath = os.path.join(DOWNLOAD_DIR, filename)
    
    if os.path.exists(filepath):
        try:
            return send_file(
                filepath,
                as_attachment=True,
                download_name=f"tiktok_video_{filename}.mp4"
            )
        except Exception as e:
            return f"Error: {e}", 500
    return "File tidak ditemukan", 404

# ========== ERROR HANDLING ==========
@app.errorhandler(404)
def not_found(e):
    return render_template("index.html", data={"error": "Halaman tidak ditemukan"}), 404

@app.errorhandler(500)
def server_error(e):
    return render_template("index.html", data={"error": "Terjadi error di server"}), 500

# ========== JALANKAN APLIKASI ==========
if __name__ == "__main__":
    logger.info(f"🚀 TikTok Downloader starting on port {PORT}")
    logger.info(f"📁 Download folder: {DOWNLOAD_DIR}")
    
    # Gunakan Waitress untuk production (lebih baik dari Flask dev server)
    serve(
        app, 
        host="0.0.0.0", 
        port=PORT,
        threads=4
    )