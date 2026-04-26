from flask import Flask, render_template, request, jsonify, send_file, send_from_directory
from flask_socketio import SocketIO, emit
import os
import uuid
import subprocess
from gtts import gTTS
from gemini_fallback import smart_translate
import yt_dlp
import whisper
import threading
import time
import re

app = Flask(__name__, static_folder='.', static_url_path='')
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")

# Tạo thư mục
os.makedirs('uploads', exist_ok=True)
os.makedirs('outputs', exist_ok=True)

# Load mô hình Whisper
print("🔄 Đang tải mô hình Whisper (lần đầu hơi lâu)...")
whisper_model = whisper.load_model("base")
print("✅ Whisper đã sẵn sàng!")

def log_message(task_id, message):
    """Gửi log realtime qua WebSocket"""
    print(f"[{task_id[:8]}] {message}")
    socketio.emit('log', {'task_id': task_id, 'message': message})

def download_youtube_video(url, task_id):
    """Tải video từ YouTube với hỗ trợ cookies"""
    output_template = f'uploads/{task_id}_%(title)s.%(ext)s'
    
    ydl_opts = {
        'outtmpl': output_template,
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'cookiefile': 'cookies.txt',  # THÊM DÒNG NÀY - sử dụng file cookies
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            log_message(task_id, "📥 Đang tải video từ YouTube...")
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            if not os.path.exists(filename):
                base = os.path.splitext(filename)[0]
                if os.path.exists(f"{base}.mp4"):
                    filename = f"{base}.mp4"
                elif os.path.exists(f"{base}.webm"):
                    filename = f"{base}.webm"
                    
            log_message(task_id, f"✅ Đã tải xong: {filename}")
            return filename
    except Exception as e:
        log_message(task_id, f"❌ Lỗi tải YouTube: {str(e)}")
        raise

def process_video(task_id, video_path, target_language, api_key):
    """Xử lý video: dịch và thuyết minh"""
    try:
        log_message(task_id, "🎬 Bắt đầu xử lý video...")
        
        if not os.path.exists(video_path):
            raise Exception(f"Không tìm thấy file video: {video_path}")
        
        # 1. Trích xuất âm thanh
        audio_path = f"uploads/{task_id}_audio.wav"
        log_message(task_id, "🎵 Đang trích xuất âm thanh từ video...")
        cmd = f'ffmpeg -i "{video_path}" -vn -acodec pcm_s16le -ar 16000 -ac 1 "{audio_path}" -y'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            log_message(task_id, f"FFmpeg error: {result.stderr}")
            raise Exception("Không thể trích xuất âm thanh")
        
        # 2. Nhận diện giọng nói
        log_message(task_id, "📝 Đang nhận diện giọng nói (có thể mất 1-2 phút)...")
        result = whisper_model.transcribe(audio_path, language=None, task="transcribe")
        original_text = result["text"]
        
        if not original_text or len(original_text.strip()) < 10:
            original_text = "Xin chào, đây là video thử nghiệm."
        
        log_message(task_id, f"📄 Văn bản gốc: {original_text[:200]}...")
        
        # 3. Dịch văn bản
        log_message(task_id, f"🔄 Đang dịch sang {target_language}...")
        translated_text, method = smart_translate(original_text, target_language, api_key)
        log_message(task_id, f"✅ Dịch xong (phương thức: {method})")
        log_message(task_id, f"📄 Văn bản dịch: {translated_text[:200]}...")
        
        # 4. Tạo giọng đọc
        log_message(task_id, "🔊 Đang tạo giọng đọc...")
        lang_map = {
            'Vietnamese': 'vi',
            'English': 'en',
            'Chinese': 'zh',
            'Japanese': 'ja',
            'Korean': 'ko',
            'French': 'fr',
            'German': 'de',
            'Spanish': 'es'
        }
        lang_code = lang_map.get(target_language, 'en')
        
        tts = gTTS(translated_text, lang=lang_code, slow=False)
        tts_audio_path = f"uploads/{task_id}_tts.mp3"
        tts.save(tts_audio_path)
        
        # 5. Ghép âm thanh vào video
        log_message(task_id, "🎬 Đang ghép âm thanh vào video...")
        output_path = f"outputs/{task_id}_dubbed.mp4"
        cmd = f'ffmpeg -i "{video_path}" -i "{tts_audio_path}" -c:v copy -c:a aac -map 0:v:0 -map 1:a:0 -shortest -y "{output_path}"'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        
        if result.returncode != 0:
            log_message(task_id, f"⚠️ Lỗi ghép âm thanh: {result.stderr}")
            # Thử cách khác nếu cách trên thất bại
            cmd = f'ffmpeg -i "{video_path}" -i "{tts_audio_path}" -c:v libx264 -c:a aac -map 0:v:0 -map 1:a:0 -shortest -y "{output_path}"'
            subprocess.run(cmd, shell=True, check=True)
        
        if not os.path.exists(output_path):
            raise Exception("Không tạo được video đầu ra")
        
        log_message(task_id, "✅ HOÀN THÀNH! Video đã được thuyết minh.")
        return output_path
        
    except Exception as e:
        log_message(task_id, f"❌ LỖI: {str(e)}")
        import traceback
        log_message(task_id, traceback.format_exc())
        raise

# Route phục vụ file index.html ở thư mục gốc
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

# Route phục vụ các file tĩnh khác (nếu có)
@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory('.', filename)

@app.route('/upload', methods=['POST'])
def upload():
    task_id = str(uuid.uuid4())
    
    if 'video' not in request.files:
        return jsonify({'error': 'No video file'}), 400
    
    video_file = request.files['video']
    target_language = request.form.get('language', 'Vietnamese')
    api_key = request.form.get('api_key', '')
    
    if video_file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    video_path = f"uploads/{task_id}_input.mp4"
    video_file.save(video_path)
    log_message(task_id, f"📁 Đã lưu video: {video_path}")
    
    thread = threading.Thread(target=process_video, args=(task_id, video_path, target_language, api_key))
    thread.daemon = True
    thread.start()
    
    return jsonify({'task_id': task_id, 'status': 'processing'})

@app.route('/youtube', methods=['POST'])
def youtube():
    task_id = str(uuid.uuid4())
    data = request.get_json()
    youtube_url = data.get('url')
    target_language = data.get('language', 'Vietnamese')
    api_key = data.get('api_key', '')
    
    if not youtube_url:
        return jsonify({'error': 'No YouTube URL'}), 400
    
    try:
        # Kiểm tra file cookies có tồn tại không
        if not os.path.exists('cookies.txt'):
            log_message(task_id, "⚠️ Không tìm thấy file cookies.txt, có thể bị YouTube chặn!")
        
        video_path = download_youtube_video(youtube_url, task_id)
        thread = threading.Thread(target=process_video, args=(task_id, video_path, target_language, api_key))
        thread.daemon = True
        thread.start()
        return jsonify({'task_id': task_id, 'status': 'processing'})
    except Exception as e:
        log_message(task_id, f"❌ Lỗi tải YouTube: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/download/<task_id>')
def download(task_id):
    output_path = f"outputs/{task_id}_dubbed.mp4"
    if os.path.exists(output_path):
        return send_file(output_path, as_attachment=True, download_name=f"dubbed_{task_id}.mp4")
    return jsonify({'error': 'File not found, still processing?'}), 404

@app.route('/status/<task_id>')
def status(task_id):
    output_path = f"outputs/{task_id}_dubbed.mp4"
    return jsonify({'ready': os.path.exists(output_path)})

if __name__ == '__main__':
    print("🚀 Server đang chạy tại: http://localhost:5000")
    print("📄 index.html phải nằm cùng thư mục với app.py")
    print("🍪 Đảm bảo file cookies.txt nằm cùng thư mục để tải YouTube thành công!")
    socketio.run(app, debug=True, port=5000)
