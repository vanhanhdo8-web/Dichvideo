"""
Gemini Fallback - Dùng API nếu có, không thì dùng Web
ĐÃ CẬP NHẬT: Sử dụng google-genai SDK mới (không còn FutureWarning)
"""
# Dùng SDK mới thay vì google.generativeai cũ
from google import genai
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
import time
import re

def translate_with_api(text, target_language, api_key):
    """Dùng Gemini API (SDK mới) để dịch"""
    try:
        # Khởi tạo client với API key (cách dùng mới)
        client = genai.Client(api_key=api_key)
        
        # Tạo prompt
        prompt = f"Dịch đoạn văn sau sang {target_language}. Chỉ trả về bản dịch, không giải thích:\n\n{text}"
        
        # Gọi API với model mới
        response = client.models.generate_content(
            model="gemini-2.0-flash-exp",  # model mới, nhanh và miễn phí
            contents=prompt
        )
        return {"success": True, "result": response.text, "method": "API"}
    except Exception as e:
        return {"success": False, "error": str(e), "method": "API"}

def translate_with_web(text, target_language):
    """Dùng Selenium để tương tác với Gemini Web (không cần API)"""
    driver = None
    try:
        # Cấu hình Chrome chạy ngầm
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        
        driver = webdriver.Chrome(options=options)
        driver.get("https://gemini.google.com")
        
        # Đợi trang load và xử lý cookie (nếu có)
        time.sleep(5)
        
        # Tìm textarea nhập liệu
        try:
            textarea = driver.find_element(By.TAG_NAME, "textarea")
        except:
            # Thử với selector khác
            textarea = driver.find_element(By.CSS_SELECTOR, "div[contenteditable='true']")
        
        # Tạo prompt
        prompt = f"""Dịch đoạn văn sau sang {target_language}. Chỉ trả về bản dịch, KHÔNG thêm bất kỳ lời giải thích hay từ nào khác:

{text}"""
        
        # Nhập prompt
        textarea.send_keys(prompt)
        textarea.send_keys(Keys.ENTER)
        
        # Đợi response
        time.sleep(10)
        
        # Lấy kết quả
        responses = driver.find_elements(By.CSS_SELECTOR, ".markdown, .message-content, [data-message-content]")
        if responses:
            result_text = responses[-1].text
            # Làm sạch kết quả
            result_text = re.sub(r'^Dịch:|^Translation:|^Here is the translation:.*?\n', '', result_text, flags=re.IGNORECASE)
            return {"success": True, "result": result_text.strip(), "method": "WEB"}
        
        return {"success": False, "error": "Không lấy được kết quả từ Gemini Web", "method": "WEB"}
        
    except Exception as e:
        return {"success": False, "error": str(e), "method": "WEB"}
    finally:
        if driver:
            driver.quit()

def smart_translate(text, target_language, api_key=None):
    """
    Hàm thông minh: ưu tiên API, fallback sang Web
    """
    # Thử API nếu có key
    if api_key and api_key.strip():
        print(f"📡 Đang dùng Gemini API (SDK mới)...")
        result = translate_with_api(text, target_language, api_key)
        if result["success"]:
            print(f"✅ API thành công!")
            return result["result"], result["method"]
        else:
            print(f"⚠️ API thất bại: {result['error']}")
            print(f"🌐 Chuyển sang Web Mode...")
    else:
        print(f"🌐 Không có API key, dùng Web Mode...")
    
    # Fallback sang Web
    result = translate_with_web(text, target_language)
    if result["success"]:
        print(f"✅ Web Mode thành công!")
        return result["result"], result["method"]
    else:
        raise Exception(f"Cả API và Web đều thất bại: {result['error']}")
