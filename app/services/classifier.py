from google import genai
from google.genai import types
import json

CLASSIFY_PROMPT = """Phân tích input và trả về JSON:
{
  "is_math_related": true/false,
  "is_problem": true/false,
  "is_learning_content": true/false,
  "grade": "9/10/11/12/thi_vao_10/thi_thpt hoặc null",
  "chapter": "Lấy ĐÚNG TÊN từ danh sách bên dưới, hoặc null nếu không khớp",
  "topic": "dai_so/giai_tich/hinh_hoc_phang/hinh_hoc_khong_gian/xac_suat_thong_ke hoặc null",
  "confidence": 0.0-1.0
}

DANH SÁCH CHAPTER HỢP LỆ THEO BỘ GIÁO DỤC (chỉ dùng đúng tên này):
bat_phuong_trinh_bac_nhat_hai_an, cac_so_dac_trung_do_muc_do_phan_tan_cua_mau_so_lieu_ghep_nhom,
cac_so_dac_trung_do_xu_the_trung_tam_cua_mau_so_lieu_ghep_nhom, can_bac_hai, cap_so_cong,
da_thuc, dao_ham, day_so_cap_so_cong_cap_so_nhan,
duong_thang_va_mat_phang_trong_khong_gian_quan_he_song_song, gia_tri_luong_giac,
gioi_han_ham_so, goc_luong_giac_va_gia_tri_luong_giac, ham_so_luong_giac,
ham_so_luong_giac_va_phuong_trinh_luong_giac, ham_so_mu_va_ham_so_logarit,
he_thuc_luong_trong_tam_giac, khao_sat_ham_so, menh_de, menh_de_tap_hop, nguyen_ham,
phuong_phap_toa_do_trong_khong_gian, phuong_trinh_va_he_phuong_trinh_bac_nhat_hai_an,
so_phuc, tich_phan, to_hop_xac_suat, ung_dung_dao_ham,
ung_dung_dao_ham_de_khao_sat_va_ve_do_thi_ham_so, ung_dung_tich_phan, vecto, xac_suat_co_dieu_kien

ĐỊNH NGHĨA:
- is_math_related = true nếu input liên quan đến Toán học (bài toán, câu hỏi về toán, chào hỏi thông thường).
- is_math_related = false nếu input hoàn toàn ngoài phạm vi Toán (y học, lịch sử, vũ khí, code, nấu ăn, v.v.).
- is_problem = false nếu là câu chào, hỏi thăm, không phải bài toán cụ thể.
- is_learning_content = true nếu input yêu cầu TẠO/TRÌNH BÀY nội dung học tập để đưa lên canvas:
  giải bài toán, tổng hợp/note công thức, lý thuyết, cheat sheet, bảng so sánh, ví dụ mẫu...
  = false nếu chỉ là câu HỘI THOẠI/ĐIỀU KHIỂN, không sinh nội dung học mới: "bỏ chữ này đi",
  "note ok chưa", "cảm ơn", "làm lại", "dễ hiểu không", "đóng canvas", lời khen/phản hồi...
  (Câu điều khiển canvas như "bỏ dòng cuối" cũng là false — nó sửa cách trình bày, KHÔNG sinh
  kiến thức mới; để chat xử lý, giữ nội dung canvas cũ.)
CHỈ trả về JSON."""

async def classify_problem(client: genai.Client, problem_text: str) -> dict:
    """Phân loại bài toán → grade, chapter."""
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=f"{CLASSIFY_PROMPT}\n\nBài toán: {problem_text}",
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            )
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"Classifier error: {e}")
        return {"is_math_related": True, "is_problem": True, "is_learning_content": True,
                "grade": None, "chapter": None, "topic": None, "confidence": 0.0}
