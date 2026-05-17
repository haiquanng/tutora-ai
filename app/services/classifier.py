from google import genai
from google.genai import types
import json

CLASSIFY_PROMPT = """Phân tích input và trả về JSON:
{
  "is_problem": true/false,
  "grade": "9/10/11/12/thi_vao_10/thi_thpt hoặc null nếu không phải bài toán",
  "chapter": "tên chương snake_case theo SGK Kết Nối hoặc null",
  "topic": "dai_so/giai_tich/hinh_hoc_phang/hinh_hoc_khong_gian/xac_suat_thong_ke hoặc null",
  "confidence": 0.0-1.0
}
is_problem = false nếu là câu chào, hỏi thăm, câu hỏi chung không phải bài toán cụ thể.
CHỈ trả về JSON."""

async def classify_problem(client: genai.Client, problem_text: str) -> dict:
    """Phân loại bài toán → grade, chapter."""
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"{CLASSIFY_PROMPT}\n\nBài toán: {problem_text}",
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json"
            )
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"Classifier error: {e}")
        return {"grade": None, "chapter": None, "topic": None, "confidence": 0.0}
