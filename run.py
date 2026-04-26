from flask import Flask, render_template, request, jsonify, send_file, send_from_directory
from flask_socketio import SocketIO, emit
import os
import uuid
import subprocess
from gtts import gTTS
from gemini_fallback import smart_translate
import yt_dlp
import threading
import time
import re

# Import Faster-Whisper thay vì whisper thường
from faster_whisper import WhisperModel

app = Flask(__name__, static_folder='.', static_url_path='')
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")

# Tạo thư mục
os.makedirs('uploads', exist_ok=True)
os.makedirs('outputs', exist_ok=True)
os.makedirs('temp_audio', exist_ok=True)

# Load mô hình Faster-Whisper (nhẹ hơn rất nhiều)
print("🔄 Đang tải mô hình Faster-Whisper (phiên bản nhẹ - dùng cho video dài)...")
# Dùng model "tiny-int8" - chỉ ~200MB RAM, chạy được video 30-60 phút
whisper_model = WhisperModel("tiny-int8", device="cpu", compute_type="int8")
print("✅ Faster-Whisper đã sẵn sàng! Có thể xử lý video dài.")

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
        'cookiefile': 'cookies.txt',
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

def process_video_streaming(task_id, video_path, target_language, api_key):
    """Xử lý video theo từng đoạn - tối ưu cho video dài"""
    try:
        log_message(task_id, "🎬 Bắt đầu xử lý video theo luồng (tối ưu cho video dài)...")
        
        if not os.path.exists(video_path):
            raise Exception(f"Không tìm thấy file video: {video_path}")
        
        # Lấy thời lượng video (phút:giây)
        probe_cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{video_path}"'
        result = subprocess.run(probe_cmd, shell=True, capture_output=True, text=True)
        duration = float(result.stdout.strip())
        log_message(task_id, f"📊 Thời lượng video: {duration:.1f} giây (~{duration/60:.1f} phút)")
        
        # Chia video thành các đoạn 30 giây
        segment_duration = 30
        num_segments = int(duration // segment_duration) + 1
        log_message(task_id, f"✂️ Chia thành {num_segments} đoạn, mỗi đoạn {segment_duration} giây")
        
        # File đầu ra cuối cùng
        output_path = f"outputs/{task_id}_dubbed.mp4"
        
        # Danh sách các đoạn đã xử lý
        processed_segments = []
        lang_map = {
            'Vietnamese': 'vi', 'English': 'en', 'Chinese': 'zh',
            'Japanese': 'ja', 'Korean': 'ko', 'French': 'fr',
            'German': 'de', 'Spanish': 'es'
        }
        lang_code = lang_map.get(target_language, 'en')
        
        full_translated_text = []  # Lưu toàn bộ văn bản đã dịch
        
        for seg_idx in range(num_segments):
            start_time = seg_idx * segment_duration
            log_message(task_id, f"🎬 Đang xử lý đoạn {seg_idx + 1}/{num_segments} (từ giây {start_time})...")
            
            # Đường dẫn file tạm
            segment_file = f"temp_audio/{task_id}_seg_{seg_idx:03d}.mp4"
            audio_seg_file = f"temp_audio/{task_id}_audio_{seg_idx:03d}.wav"
            voice_seg_file = f"temp_audio/{task_id}_voice_{seg_idx:03d}.mp3"
            output_seg_file = f"temp_audio/{task_id}_out_{seg_idx:03d}.mp4"
            
            # Cắt đoạn video
            cut_cmd = f'ffmpeg -i "{video_path}" -ss {start_time} -t {segment_duration} -c copy "{segment_file}" -y'
            subprocess.run(cut_cmd, shell=True, capture_output=True, text=True)
            
            # Trích xuất âm thanh từ đoạn
            cmd = f'ffmpeg -i "{segment_file}" -vn -acodec pcm_s16le -ar 16000 -ac 1 "{audio_seg_file}" -y'
            subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            # Nhận diện giọng nói bằng Faster-Whisper
            segments, info = whisper_model.transcribe(audio_seg_file, beam_size=3, language=None)
            segment_text = " ".join([seg.text for seg in segments])
            
            if segment_text.strip():
                log_message(task_id, f"📝 Đoạn {seg_idx + 1}: \"{segment_text[:100]}...\"")
                
                # Dịch văn bản
                translated_text, method = smart_translate(segment_text, target_language, api_key)
                full_translated_text.append(translated_text)
                log_message(task_id, f"🔄 Đoạn {seg_idx + 1} đã dịch: \"{translated_text[:100]}...\"")
                
                # Tạo giọng đọc cho đoạn này
                tts = gTTS(translated_text, lang=lang_code, slow=False)
                tts.save(voice_seg_file)
                
                # Ghép voice vào video đoạn
                cmd = f'ffmpeg -i "{segment_file}" -i "{voice_seg_file}" -c:v copy -c:a aac -map 0:v:0 -map 1:a:0 -shortest -y "{output_seg_file}"'
                subprocess.run(cmd, shell=True, capture_output=True, text=True)
                processed_segments.append(output_seg_file)
                
                log_message(task_id, f"✅ Đã xử lý xong đoạn {seg_idx + 1}/{num_segments}")
            else:
                # Không có giọng nói, giữ nguyên đoạn video
                log_message(task_id, f"⚠️ Đoạn {seg_idx + 1} không có giọng nói, giữ nguyên")
                processed_segments.append(segment_file)
            
            # Xóa file tạm để giải phóng RAM
            for f in [segment_file, audio_seg_file, voice_seg_file]:
                if os.path.exists(f):
                    try:
                        os.remove(f)
                    except:
                        pass
        
        # Ghép tất cả các đoạn đã xử lý
        log_message(task_id, "🔗 Đang ghép các đoạn đã xử lý...")
        concat_file = f"temp_audio/{task_id}_concat.txt"
        with open(concat_file, 'w') as f:
            for seg_file in processed_segments:
                f.write(f"file '{seg_file}'\n")
        
        cmd = f'ffmpeg -f concat -safe 0 -i "{concat_file}" -c copy "{output_path}" -y'
        subprocess.run(cmd, shell=True, capture_output=True, text=True)
        
        # Lưu toàn bộ văn bản đã dịch
        full_text_path = f"outputs/{task_id}_translated.txt"
        with open(full_text_path, 'w', encoding='utf-8') as f:
            f.write("\n\n---\n\n".join(full_translated_text))
        
        # Dọn dẹp file tạm
        for seg_file in processed_segments:
            if os.path.exists(seg_file) and seg_file.startswith("temp_audio/"):
                try:
                    os.remove(seg_file)
                except:
                    pass
        if os.path.exists(concat_file):
            os.remove(concat_file)
        
        log_message(task_id, f"✅ HOÀN THÀNH! Video đã được thuyết minh xong.")
        log_message(task_id, f"📁 File output: {output_path}")
        log_message(task_id, f"📄 Văn bản dịch đã lưu tại: {full_text_path}")
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
    
    # Dùng hàm xử lý streaming thay vì hàm cũ
    thread = threading.Thread(target=process_video_streaming, args=(task_id, video_path, target_language, api_key))
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
        if not os.path.exists('cookies.txt'):
            log_message(task_id, "⚠️ Không tìm thấy file cookies.txt, có thể bị YouTube chặn!")
        
        video_path = download_youtube_video(youtube_url, task_id)
        # Dùng hàm xử lý streaming
        thread = threading.Thread(target=process_video_streaming, args=(task_id, video_path, target_language, api_key))
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

@app.route('/download-text/<task_id>')
def download_text(task_id):
    """Tải file văn bản đã dịch"""
    text_path = f"outputs/{task_id}_translated.txt"
    if os.path.exists(text_path):
        return send_file(text_path, as_attachment=True, download_name=f"translated_{task_id}.txt")
    return jsonify({'error': 'Text file not found'}), 404

@app.route('/status/<task_id>')
def status(task_id):
    output_path = f"outputs/{task_id}_dubbed.mp4"
    return jsonify({'ready': os.path.exists(output_path)})

if __name__ == '__main__':
    print("🚀 Server đang chạy tại: http://localhost:5000")
    print("📄 index.html phải nằm cùng thư mục với app.py")
    print("🍪 Đảm bảo file cookies.txt nằm cùng thư mục để tải YouTube thành công!")
    print("⚡ Đã tối ưu cho video dài: chia nhỏ thành từng đoạn 30 giây")
    socketio.run(app, debug=True, port=5000)
