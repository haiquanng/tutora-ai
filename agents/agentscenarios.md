# Kịch bản Agent Tư vấn Tutora (Spec State Machine)

> **File này là NGUỒN CHÂN LÝ về hành vi của agent.** Viết kịch bản ở đây →
> Developer code đúng theo → LLM **KHÔNG đọc file này**. LLM chỉ được gọi ở 2 điểm hẹp:
> (1) hiểu ý người dùng (trích intent + slot), (2) diễn đạt câu chữ tiếng Việt.
>
> Học theo cách Hostinger Kodee & các task-oriented dialogue system làm: flow là **state
> machine tường minh trong CODE**, không phải prompt cho LLM tự diễn. Xem "Vì sao" ở cuối file.

---

## 0. Khái niệm nền (đọc trước)

**SLOT** = mẩu thông tin hệ thống cần thu để phục vụ. Slot của luồng tìm gia sư
(khớp user flow chính thức: AI collects *Subject · Grade · Area · Gender · Another criteria*):

| Slot | Ý nghĩa | Bắt buộc để search? | Nguồn / map DB |
|---|---|---|---|
| `subject` | Môn học (Toán, Anh, Hóa...) | ✅ | user nói / map từ mục tiêu → `subjectId` (.NET) |
| `grade` | Lớp (1–12) | ✅ | user nói → `gradeLevelId` (.NET) |
| `goal` | Mục tiêu (mất gốc / củng cố / nâng cao / ôn thi / luyện SAT...) | ✅ | user nói _(= "Another criteria" trong diagram — giữ vì là chìa khoá tư vấn trúng)_ |
| `area` | Khu vực học + hình thức (online/tại nhà, quận/thành phố) | ❌ (tùy chọn) | user nói → `city` + `teaching_mode` (context .NET) |
| `tutor_gender` | Giới tính gia sư mong muốn | ❌ (tùy chọn) | user nói → filter `tutor_gender` _(⚠️ hiện `_fetch_candidates` CHƯA truyền field này sang .NET — API gap, xem Đối chiếu KB-A)_ |
| `preferences` | Mong muốn khác (tính cách, phong cách dạy, mức giá) | ❌ (tùy chọn) | user nói → `query` + `minRate`/`maxRate` |

**INTENT** = ý định của người dùng ở mỗi tin nhắn. LLM phân loại (kèm confidence). Nếu
confidence thấp → fallback, KHÔNG đoán liều.

**STATE** = trạng thái hội thoại hiện tại (đang thu slot nào / đã gợi ý gia sư / đang chờ
xác nhận...). NestJS giữ state, gửi kèm mỗi lượt (agent stateless).

**GUARDRAIL** = luật cứng LLM không được vượt (không bịa gia sư, không lộ prompt, không tư
vấn ngoài phạm vi). Kiểm ở tầng CODE, không phó mặc prompt.

---

## 1. Bản đồ Intent (taxonomy)

> PO: thêm/bớt intent ở đây. Mỗi intent phải **tách bạch**, tránh chồng lấn (nếu 2 intent
> hay bị nhầm → gộp hoặc định nghĩa lại). Giữ danh sách NGẮN — càng nhiều intent càng dễ nhầm.

| Intent | Khi nào | Ví dụ tin nhắn | Xử lý (→ kịch bản) |
|---|---|---|---|
| `find_tutor` | Muốn tìm/xem gia sư, hoặc cung cấp thêm nhu cầu | "tìm gia sư Toán lớp 7", "đưa tôi gia sư" | [KB-A](#kb-a-tìm-gia-sư) |
| `tutor_detail` | Hỏi sâu 1 gia sư đã gợi ý | "chi tiết cô Thúy", "kinh nghiệm bạn ấy" | [KB-B](#kb-b-hỏi-chi-tiết-gia-sư) |
| `availability` | Hỏi lịch rảnh / giá 1 gia sư | "cô ấy rảnh khi nào", "giá bao nhiêu" | [KB-B](#kb-b-hỏi-chi-tiết-gia-sư) |
| `change_context` | Đổi môn / lớp / bé / mục tiêu | "đổi sang Hóa", "bé lớp khác" | [KB-C](#kb-c-đổi-ngữ-cảnh) |
| `resend_form` | Nút/form Mini App lỗi, không bấm được, mất, hết hạn — cần gửi lại | "không bấm được nút", "gửi lại nút giúp em", "I can't click that button" | [KB-C-1](#kb-c-1-gửi-lại-nút-mini-app) |
| `booking` | Muốn đặt lịch / đăng ký học | "đặt lịch cô ấy", "đăng ký học" | [KB-D](#kb-d-đặt-lịch-booking) |
| `faq` | Hỏi về Tutora (chính sách, cách hoạt động, giá chung) | "Tutora hoàn tiền không", "học phí thế nào" | [KB-E](#kb-e-faq-về-tutora) |
| `chitchat` | Chào hỏi / xác nhận ngắn / cảm thán / trêu / **gõ id kỹ thuật** | "alo", "ok", ":)))", "sao AI thế", "gia sư id tutor-550" | [KB-F](#kb-f-chitchat--ngoài-phạm-vi) |
| `out_of_scope` | Ngoài phạm vi Tutora (không phải tìm gia sư) | "thời tiết", "kể chuyện cười" | [KB-F](#kb-f-chitchat--ngoài-phạm-vi) |

> ⚠️ **Tin nhắn chứa id kỹ thuật (tutor-xxx / uuid) KHÔNG phải nhu cầu thật** — user không
> thấy/không biết id; đó là dev test hoặc trêu. → luôn về `chitchat`, KHÔNG get gia sư. Xem [KB-B](#kb-b-hỏi-chi-tiết-gia-sư).
>
> 📌 Giai đoạn sau (khi agent tiếp quản luồng sau-booking, mục 7) sẽ thêm intent
> `reschedule` (dời lịch) và `cancel` (hủy lớp) — CHƯA thêm bây giờ để giữ taxonomy ngắn.

---

## 2. Bản đồ State (trạng thái hội thoại)

```
              (Follow OA, tin nhắn đầu)
                    ┌─────────────┐   user cũ có booking active
                    │   GREETING  │─────────────────────────────► luồng SAU-BOOKING (mục 7,
                    └──────┬──────┘   (NestJS quyết bằng DB)        NestJS menu xử lý)
                           │ find_tutor (lần đầu / booking mới)
                           ▼
              ┌────────────────────────┐   thiếu slot
              │   COLLECTING_SLOTS      │◄────────────┐
              │ (thu subject/grade/goal │             │ hỏi slot còn thiếu
              │  + area/gender tuỳ chọn)│─────────────┘
              └────────────┬───────────┘
                           │ đủ subject+grade+goal
                           ▼
                    ┌─────────────┐   rỗng → NO_RESULT (nới tiêu chí / waitlist)
                    │  SEARCHING  │──────────────────►
                    └──────┬──────┘
                           │ có gia sư (3 card, 3 tier)
                           ▼
                    ┌─────────────┐  tutor_detail/availability → vẫn ở đây
              ┌────►│  SUGGESTED  │  change_context → CONFIRMING_CHANGE
              │     │ (đã ra card)│  booking → CONFIRMING_BOOKING
              │     └──────┬──────┘
              │            │ PH chê / từ chối gợi ý
              │            ▼
              │     ┌─────────────┐
              └─────│  REFINING   │  hỏi LÝ DO (1 câu) → trích tiêu chí mới
   search lại cùng  │ (hỏi lý do) │  → search lại trong TIER PH đã quan tâm
   tier + tiêu chí  └─────────────┘
                           
                    SUGGESTED ──booking (đã confirm)──►┌─────────────┐
                                                       │  → HANDOFF  │ (NestJS nhận cờ,
                                                       │   BOOKING   │  chuyển booking flow
                                                       └─────────────┘  — chi tiết KB-D)
```

> Cần: mỗi state = một "màn". Ô trên là khung; điền chi tiết từng màn ở mục 3.
> Nhánh "user cũ" (First time? = No trong user flow): PH đã có booking đang chạy nhắn tin →
> NestJS route sang menu quản lý lớp (dời lịch / hủy / xem báo cáo — mục 7), KHÔNG vào
> matching, TRỪ KHI PH nói rõ muốn tìm gia sư mới ("New booking") → vào GREETING như thường.

---

## 3. Chi tiết từng kịch bản

> **Cách điền** (dùng chung cho mọi KB):
> - **Điều kiện vào**: state + intent nào kích hoạt.
> - **Slot cần**: slot nào phải có / thu thêm.
> - **Function gọi**: API/BE nào (deterministic, không phải LLM tính).
> - **Câu mẫu**: 1–2 câu mẫu để LLM diễn đạt theo (KHÔNG phải câu cứng — LLM biến tấu giọng).
> - **Nhánh chuyển**: đi state nào tiếp theo tùy kết quả.
> - **Guardrail**: luật cứng ở màn này.

---

### KB-A. Tìm gia sư (Matching)

**Điều kiện vào:** intent = `find_tutor` (từ GREETING / COLLECTING_SLOTS / SUGGESTED).

**Slot bắt buộc để search:** `subject` + `grade` + `goal` — đúng 3 slot, không hơn.
`preferences` là **tuỳ chọn** (xem mục 0): KHÔNG chặn search nếu thiếu, chỉ dùng để cá nhân
hoá lý do giới thiệu nếu PH có nêu. _(Quyết định chốt — xem "Đối chiếu" cuối mục này.)_

**Logic thu slot (thứ tự hỏi, mỗi slot hỏi tối đa 1 lượt bình thường):**
1. Thiếu `subject` → hỏi môn. Nếu đã có `goal` dạng SAT/IELTS/TOEIC/HSG... mà chưa rõ môn →
   gợi ý map ngay trong câu hỏi (không hỏi trống): SAT → Toán hoặc Tiếng Anh; IELTS/TOEIC →
   Tiếng Anh; thi HSG/chuyển cấp/thi vào 10/THPTQG → hỏi thẳng bé cần môn nào.
2. Thiếu `grade` → hỏi lớp. Chỉ hỏi lớp, KHÔNG hỏi lại môn dù cùng câu.
3. Thiếu `goal` → hỏi mục tiêu (1 câu): mất gốc / củng cố / nâng cao / ôn thi.
4. **1 lượt gộp tuỳ chọn** (theo user flow: Area · Gender · criteria khác): hỏi GỘP trong 1
   câu — khu vực/hình thức học + mong muốn về gia sư (giới tính, tính cách...). PH bỏ qua /
   trả lời "sao cũng được" → search luôn, KHÔNG hỏi tách từng ý. Nếu PH đã nêu từ trước
   (trích được ở các lượt trước) → BỎ QUA lượt này.

> ⚠️ **Trích slot tuỳ chọn CHỦ ĐỘNG ở MỌI lượt, không đợi đến bước 4.** Nếu PH gộp sẵn slot
> tuỳ chọn vào câu đầu tiên hoặc bất kỳ lượt nào trước đó (vd "tìm gia sư Toán lớp 8, ngân sách
> 200k, muốn cô giáo" — có cả `area`/`tutor_gender`/`preferences` ngay trong 1 câu) → LLM phải
> trích HẾT các slot có trong câu đó ở lượt trích đó, KHÔNG chỉ lấy `subject`+`grade` rồi để
> dành phần còn lại chờ tới bước 4 mới hỏi lại (hỏi lại cái đã biết = phạm quy tắc "không hỏi
> lại slot đã biết" ở trên). Bước 4 chỉ hỏi phần THỰC SỰ còn thiếu sau khi đã trích hết những
> gì PH cung cấp. _(Tham khảo UX ChatGPT × Preply: model trích toàn bộ tiêu chí có trong 1 câu
> rồi mới gọi tool tìm kiếm, không hỏi máy móc theo thứ tự cố định.)_
5. Đủ 3 slot bắt buộc → **gọi search** (Function: `POST /api/tutors/recommend`, .NET Ranking Core).

**Quy tắc quan trọng:**
- ⚠️ Đọc lại toàn bộ hội thoại — KHÔNG hỏi lại slot đã biết. "toán lớp 7" ở 1 câu = có cả
  subject + grade cùng lúc (trích cả hai trong 1 lượt trích, không tách làm 2 lượt hỏi).
- ⚠️ User giục ("đưa tôi gia sư", "có ai không", "xem luôn đi") → search NGAY với slot hiện
  có (kể cả thiếu `goal`), đừng hỏi thêm — tôn trọng sự sốt ruột hơn là thu đủ dữ liệu.
- ⚠️ **Tối đa 4 lượt hỏi** (3 slot bắt buộc + 1 lượt gộp tuỳ chọn). Nếu PH trả
  lời né tránh/lạc đề ở đúng slot đang hỏi → được hỏi lại slot đó **thêm tối đa 1 lần**, đổi
  cách hỏi cho cụ thể/trực tiếp hơn (KHÔNG lặp y nguyên câu cũ — phạm guardrail KB-F). Nếu
  lượt 2 vẫn không rõ → KHÔNG hỏi lần 3 cho slot đó; nếu là `goal` thì bỏ qua và search luôn
  (goal không rõ vẫn search được, giới thiệu chung chung); nếu là `subject`/`grade` (bắt
  buộc cứng, không suy diễn được) → chuyển hướng nhẹ nhàng mời PH nhắn hỗ trợ Tutora thay vì
  lặp vô hạn.

**Kết quả search (theo user flow: top 3 chia 3 tier Standard – Pro – Premium):**
- Có gia sư → state = SUGGESTED. Giới thiệu **3 người, mỗi người 1 tier**:
  - **Tier tính DETERMINISTIC ở tầng CODE/BE từ data DB thật** (các field đã có trong kết quả
    recommend: `hourlyRate`/`priceMin`, `averageRating`, `totalReviews`, `completedHours`) —
    Standard/Pro/Premium phân theo giá + rating + kinh nghiệm/tổng giờ dạy. **LLM KHÔNG tự
    xếp tier, KHÔNG bịa nhãn** — nhận tier đã gắn sẵn từ code và chỉ diễn đạt.
  - _(Cần chốt công thức tier với BE: vd Standard = giá ≤ p33 phân vị; Premium = giá ≥ p66 +
    rating ≥ 4.5 + completedHours cao; Pro = còn lại. Công thức nằm ở Ranking Core / code
    agent, KHÔNG nằm trong prompt.)_
  - Mỗi người: tên + tier + 1 lý do ngắn hợp nhu cầu (ưu tiên bám `goal`, rồi `preferences`).
    KHÔNG nói tổng số tìm được, KHÔNG liệt kê lại giá/rating (đã có card riêng bên dưới).
  - Nếu kết quả < 3 người → giới thiệu đúng số người có, KHÔNG cố lấp cho đủ 3 tier.
- Rỗng → [KB-A-1 No result].
- PH bấm card xem profile (Rating/Feedback · Availability · Teaching style — render phía
  NestJS/Zalo) rồi **chê / muốn xem người khác** → [KB-A-2 Refine theo lý do].

**Câu mẫu (LLM diễn đạt theo — biến tấu giọng, không phải câu cứng):**

| Bước | Ví dụ |
|---|---|
| Hỏi môn (trống) | "Dạ anh/chị muốn tìm gia sư môn gì cho bé ạ?" |
| Hỏi môn (đã có goal SAT/IELTS) | "Dạ để luyện SAT, Tutora có gia sư dạy riêng phần Toán và Tiếng Anh. Bé nhà mình cần tập trung môn nào ạ?" |
| Hỏi lớp | "Dạ bé nhà mình đang học lớp mấy ạ?" |
| Hỏi mục tiêu | "Dạ bé học Toán lớp 8 lần này là để củng cố lại kiến thức, nâng cao thêm, hay ôn thi vậy ạ?" |
| Hỏi gộp khu vực + mong muốn (tuỳ chọn) | "Dạ anh/chị muốn bé học online hay gia sư đến nhà ạ? Với anh/chị có mong muốn gì thêm về gia sư không (cô hay thầy, kiên nhẫn, nghiêm khắc...)? Không có thì em tìm luôn ạ!" |
| Hỏi lại lượt 2 (né tránh) | "Dạ để em tìm đúng gia sư, anh/chị cho em xin tên môn học bé cần học nhé (vd Toán, Tiếng Anh, Hoá...)?" |
| Giới thiệu 3 gia sư 3 tier | "Dạ em gửi anh/chị 3 gia sư hợp với bé: cô Nguyễn Thị Hằng (gói Tiêu chuẩn) — dạy Toán mất gốc rất kiên nhẫn; thầy Trần Văn Bình (gói Pro) — chuyên ôn thi chuyển cấp; và cô Lê Thu Trang (gói Premium) — hơn 1000 giờ dạy, được phụ huynh đánh giá rất cao. Anh/chị xem thẻ chi tiết bên dưới giúp em nhé!" |

**Guardrail:**
- 🚫 KHÔNG nêu tên gia sư nào không có trong kết quả search vừa gọi (chống bịa) — chỉ dùng
  đúng danh sách trả về từ function call, không tự thêm bớt.
- 🚫 KHÔNG lộ id kỹ thuật (tutor-xxx, uuid) trong câu chữ.
- 🚫 KHÔNG tự ý search lại khi chưa đủ 3 slot bắt buộc, kể cả khi model "nghĩ" mình đủ hiểu —
  quyết định search là của CODE (gate deterministic), không phải model tự quyết.

#### KB-A-1. No result (search rỗng)
**Câu mẫu:** "Dạ hiện Tutora chưa có gia sư [môn] lớp [lớp] phù hợp hết tiêu chí này ạ. Anh/chị
thử nới bớt yêu cầu giúp em nhé (vd bỏ bớt mong muốn về giới tính/mức giá), hoặc em ghi nhận
lại, có gia sư phù hợp em báo ngay ạ!"

**Nhánh (theo user flow: "Bot refines criteria or adds to waitlist"):**
1. Gợi ý nới tiêu chí — ưu tiên gợi ý bỏ `preferences`/`area`/`tutor_gender` trước (tiêu chí
   mềm), giữ nguyên `subject`/`grade`/`goal` (nhu cầu gốc, không nên đổi khi search rỗng).
2. **Waitlist** — user flow chính thức CHỐT đây là tính năng thật: BE lưu nhu cầu (slot đã
   thu + zalo user id) vào waitlist, khi có gia sư mới khớp → chủ động ZNS/OA báo PH.
   _(⚠️ API waitlist CHƯA tồn tại — BE cần xây: `POST /api/waitlist` nhận
   {subject_id, grade_level_id, goal, preferences, zalo_uid}. Đến khi có API, bot KHÔNG được
   hứa "em báo ngay khi có" — chỉ nói nới tiêu chí, tránh hứa suông.)_

🚫 TUYỆT ĐỐI không bịa gia sư để lấp khoảng trống kết quả rỗng.

#### KB-A-2. PH muốn tìm gia sư nữa sau khi đã SUGGESTED → hỏi rõ 2 trường hợp trước

> Cập nhật 2026-07-13 (spec PO): trước đây bot tự đoán "chê gia sư = hỏi lý do rồi refine
> ngay" ([Luồng cũ](#luồng-cũ-vẫn-áp-dụng-sau-khi-đã-chọn-nhánh-1) bên dưới). Log thật cho
> thấy không phải lúc nào PH muốn tìm gia sư nữa cũng là "chê" — có thể là nhu cầu MỚI hẳn
> (môn khác, giới tính khác, bé thứ 2...). Đoán nhầm → refine sai hướng hoặc mở form dư thừa.
> **Nay bot PHẢI hỏi rõ trước khi quyết định**, không tự suy diễn.

**Điều kiện vào:** state = SUGGESTED (đã có `shown_tutors`), intent = `find_tutor` (PH muốn
tìm/xem gia sư nữa, bất kể lý do — "không ưng", "tìm thêm", "tìm gia sư khác"...) — route qua
`find_tutor`, KHÔNG cần intent riêng; code phân biệt bằng `shown_tutors` không rỗng.

**Luồng (2 lượt tối thiểu):**
0. **Hỏi rõ 1 câu, đúng 2 lựa chọn** trước khi làm gì cả (state = CONFIRMING_REOPEN_CHOICE):
   - **(1) Đổi gia sư khác, GIỮ NGUYÊN môn/lớp** đang tìm (PH chưa ưng gia sư đã gợi ý).
   - **(2) Nhu cầu KHÁC hẳn** (môn khác, giới tính khác, mục tiêu khác, bé khác...).
   PH trả lời không rõ ý (không khớp cả 2 từ khoá) → hỏi lại đúng 2 lựa chọn, KHÔNG đoán bừa.
1. **Chọn (1)** → về [Luồng cũ](#luồng-cũ-vẫn-áp-dụng-sau-khi-đã-chọn-nhánh-1) bên dưới: hỏi
   lý do/yêu cầu thêm rồi search lại NGAY trong chat, loại trừ các gia sư đã gợi ý
   (`shown_tutors`) — **KHÔNG gửi form**.
2. **Chọn (2)** → như KB-C (đổi ngữ cảnh): mở lại Mini App form (điền sẵn dữ liệu cũ) để PH
   nhập nhu cầu mới — **KHÔNG hỏi thêm gì qua chat**, form đã đủ mọi trường.

##### Luồng cũ (vẫn áp dụng SAU KHI đã chọn nhánh 1)

> User flow gốc: *Accept? → No → AI ask reason → AI suggests top 3 matching tutors which are
> related to the previous tier that user has chosen when they tap the tutor card (with
> criteria from the reason)*. Đoạn dưới đây mô tả phần "ask reason → suggest lại" — nay chỉ
> chạy SAU bước 0 ở trên (đã biết chắc PH muốn đổi gia sư, giữ tiêu chí).

1. **Hỏi LÝ DO đúng 1 câu** (state = REFINING): "Dạ anh/chị chưa ưng điểm nào để em tìm
   người hợp hơn ạ — về giá, kinh nghiệm, hay cách dạy ạ?" — KHÔNG hỏi lại slot cũ.
2. LLM trích tiêu chí mới từ lý do (giá cao quá → `max_rate`; muốn cô → `tutor_gender`;
   thiếu kinh nghiệm → ưu tiên `completedHours`...) → nối vào `preferences`/filter.
3. **Search lại, loại trừ các gia sư đã gợi ý** (`shown_tutors`) — tránh trả lại đúng người
   cũ. _(Chưa làm: giữ nguyên tier PH đã quan tâm — xem "Đối chiếu" bên dưới, hiện search lại
   cả 3 tier như KB-A bình thường, tier cũ PH đã tap card CHƯA được track/truyền sang.)_
4. Kết quả → về SUGGESTED (card mới) hoặc KB-A-1 nếu rỗng.

**Chống lặp vô hạn:** tối đa **2 vòng refine liên tiếp**. Sau 2 vòng PH vẫn từ chối →
KHÔNG hỏi lý do lần 3; gợi ý: (a) vào waitlist chờ gia sư mới, hoặc (b) nhắn hỗ trợ Tutora
(người thật) tư vấn kỹ hơn. _(Tương đương handoff classifier của Kodee — biết lúc nào bot
nên dừng.)_ ⚠️ **Chưa implement trong code** — hiện mỗi vòng "đổi gia sư khác" đều hỏi lại từ
bước 0, không đếm số vòng liên tiếp; xem "Đối chiếu" bên dưới.

**Câu mẫu:**
| Bước | Ví dụ |
|---|---|
| Hỏi rõ 2 trường hợp (bước 0, MỚI) | "Dạ anh/chị muốn (1) đổi gia sư khác với tiêu chí đang tìm, hay (2) tìm gia sư cho nhu cầu khác (môn/lớp/giới tính khác...) ạ?" |
| Hỏi lý do (sau khi chọn 1) | "Dạ anh/chị chưa ưng điểm nào ạ — về học phí, kinh nghiệm hay cách dạy — để em tìm người hợp hơn cho bé ạ?" |
| Giới thiệu lại sau refine | "Dạ vậy em gửi thêm cô Phạm Mai Anh — học phí nhẹ hơn mà vẫn chuyên ôn thi lớp 9, anh/chị xem thử giúp em nhé!" |
| Sau 2 vòng vẫn từ chối | "Dạ để em ghi nhận nhu cầu của mình, khi có gia sư mới phù hợp em báo anh/chị ngay ạ. Hoặc anh/chị nhắn hỗ trợ Tutora để được tư vấn kỹ hơn nhé!" |

**Guardrail:**
- 🚫 KHÔNG chê/hạ thấp gia sư PH vừa từ chối (vẫn là gia sư của hệ thống).
- 🚫 Tiêu chí mới trích từ lý do phải áp vào SEARCH THẬT — không được "giả vờ đã lọc" rồi
  đưa lại danh sách cũ đảo thứ tự.
- 🚫 KHÔNG reset slot gốc (subject/grade/goal) trong vòng refine — lý do chỉ THÊM tiêu chí.
- 🚫 KHÔNG tự ý search lại HAY mở form ngay khi PH nói muốn tìm gia sư nữa — PHẢI hỏi rõ bước
  0 trước, trừ khi PH đã tự nêu rõ trong CHÍNH câu đó (vd "cho tôi gia sư môn khác" đã đủ rõ
  là nhu cầu khác → route thẳng KB-C, không hỏi lại — _lưu ý: nhánh này chưa tách riêng trong
  code, hiện luôn hỏi bước 0 kể cả khi câu đã đủ rõ; xem "Đối chiếu"_).

---

#### Đối chiếu với code/test hiện tại (để dev đồng bộ)

- `app/services/agent.py::_handle_find_tutor` đã đúng gate 3 slot (subject/grade/goal),
  KHÔNG chặn theo `preferences` — khớp quyết định chốt ở trên.
- ✅ **Lượt hỏi gộp tuỳ chọn (bước 4) ĐÃ CÓ trong code** (gate "hỏi 1 lần, mềm"): đủ 3 slot
  + chưa có preferences + chưa hỏi lần nào → hỏi gộp 1 câu, đánh dấu `asked_preferences`
  qua `context_patch`; lượt sau search dù PH trả lời gì ("sao cũng được" → search luôn).
  PH giục (rush) → bỏ qua cả câu hỏi goal lẫn lượt gộp, search ngay.
  ⚠️ **NestJS PHẢI persist field mới `asked_preferences`** (schemas: `TutorChatContext` +
  `AgentContextPatch`) giống `goal` — chưa persist thì fallback vẫn chạy được nhờ extract
  bắt câu từ chối, nhưng có nguy cơ hỏi lặp khi PH im lặng/lạc đề.
- **Trích slot tuỳ chọn chủ động — MỚI CÓ MỘT NỬA**: `preferences` đã được trích & tích lũy
  từ mọi lượt, nhưng `_run_search` (`agent.py`) vẫn chỉ truyền `subject_id` vào
  `TutorChatFilters` — `tutor_gender`/`min_rate`/`max_rate` chưa được trích riêng thành
  filter cứng (mới nằm trong text query). Cần fix đồng thời với gap `tutor_gender` không
  truyền sang .NET (xem bên dưới).
- ✅ **`_MAX_CARDS_SHOWN` đã đổi thành 3** phía Python — ⚠️ còn phải đổi ĐỒNG THỜI
  `MAX_CARDS` bên NestJS (agent.handler) và UI card Zalo, không đổi lẻ 1 bên.
- **Tier Standard/Pro/Premium chưa tồn tại** ở mọi tầng: Ranking Core (.NET) trả list phẳng.
  Cần thêm: (a) logic gắn tier deterministic (BE hoặc code agent, từ field giá/rating/giờ dạy
  có sẵn trong response), (b) NestJS render nhãn tier lên card, (c) NestJS track card nào
  được PH tap (cần cho KB-A-2 giữ tier khi refine) — gửi kèm state mỗi lượt.
- **Filter `tutor_gender` có trong schema (`TutorChatFilters`) nhưng `_fetch_candidates`
  KHÔNG truyền sang .NET** (payload thiếu field gender) → slot `tutor_gender` thu được cũng
  vô dụng cho tới khi vá API gap này (cả FastAPI lẫn .NET /recommend).
- **API waitlist chưa có** (KB-A-1) — BE cần xây trước khi bot được phép hứa "em báo khi có".
- ✅ Xung đột triết lý với `tests/agent/test_search_flow.py` ĐÃ GIẢI QUYẾT bằng gate
  "hỏi 1 lần, mềm" (đúng KB-A bước 4): đủ 3 slot cứng → hỏi gộp tuỳ chọn đúng 1 lượt →
  search dù PH có trả lời hay không. Test cũ giữ nguyên assertion (giờ pass tự nhiên),
  docstring đã cập nhật + thêm test cho nhánh từ chối lượt gộp và nhánh đã-hỏi-rồi.
- ✅ **KB-A-2 bước 0 (hỏi rõ 2 trường hợp) ĐÃ CÓ trong code** —
  `app/services/agent.py::_handle_find_tutor` (gate đầu hàm, trước gate thiếu môn/lớp): PH
  muốn tìm gia sư nữa VÀ `shown_tutors` không rỗng → luôn hỏi trước qua 2 field state mới
  `pending_reopen_choice`/`refining_alternate_search` (`TutorChatContext`/`AgentContextPatch`,
  `schemas.py`) — `run_agent` đọc 2 field này ƯU TIÊN trước mọi intent khác (lượt trả lời cho
  câu hỏi bước 0 dễ bị extract đoán nhầm intent). Phân loại câu trả lời (1)/(2) bằng từ khoá
  (`_classify_reopen_choice`) — CHƯA dùng LLM, không rõ ý → hỏi lại chứ không đoán bừa.
  ⚠️ **Gap đã biết** (chưa fix, xem guardrail cuối KB-A-2): (a) chưa route thẳng KB-C khi PH
  đã nêu rõ nhu cầu khác NGAY trong câu đầu — luôn hỏi bước 0; (b) chưa đếm số vòng refine
  liên tiếp (giới hạn "tối đa 2 vòng" chưa implement); (c) refine (nhánh 1) chưa giữ tier PH
  đã quan tâm — search lại cả 3 tier, chỉ loại trừ đúng gia sư đã gợi ý qua
  `_run_search(..., exclude_ids=...)` (lọc phía client, `.NET /recommend` không hỗ trợ exclude
  sẵn). NestJS (`tutora-zalo-bot`): Mini App form submit (`mini-app-search.flow.ts`) PHẢI
  truyền `shownTutorsOverride=[]` cho `AgentMatchingFlow.runTurn` — nếu không, `shown_tutors`
  cũ còn sót sẽ khiến agent hỏi lại bước 0 ngay sau khi PH VỪA quyết định qua form.
- ✅ Bug KB-B đã fix trong code: tên không khớp + nhiều gia sư đã gợi ý → HỎI LẠI
  (`_handle_tutor_query`); chỉ auto-chọn khi đúng 1 người. Tin nhắn chứa id kỹ thuật
  (tutor-xxx/uuid) cũng đã chặn ở tầng code TRƯỚC khi gọi LLM → đáp như chitchat.

---

### KB-B. Hỏi chi tiết gia sư

**Điều kiện vào:** state = SUGGESTED, intent = `tutor_detail` hoặc `availability`.

**Xác định gia sư nào (chỉ theo TÊN, KHÔNG theo id):**
1. User nhắc TÊN → khớp tên trong danh sách đã gợi ý (`shown_tutors`).
2. Không rõ + chỉ có 1 gia sư đã gợi ý → lấy người đó.
3. Không rõ + nhiều người → **HỎI LẠI** "anh/chị muốn xem gia sư nào ạ?" _(KHÔNG lấy đại người đầu — bug này ĐÃ FIX trong `_handle_tutor_query`)_

**Function:** `GET /api/tutors/{id}/full-profile` (chi tiết) hoặc `/schedule` (lịch). `id` lấy
NỘI BỘ từ tên đã khớp ở `shown_tutors` — user KHÔNG bao giờ cung cấp id.

**Câu mẫu:** _(Cần điền)_

**Guardrail:**
- 🚫 Tên không thuộc danh sách đã gợi ý → **HỎI LẠI** "anh/chị muốn xem gia sư nào ạ?".
  **KHÔNG bao giờ trả nhầm sang gia sư khác.**

> ⚠️ **User gõ id kỹ thuật (vd "tutor-550", "cho tôi gia sư có id ...") KHÔNG phải nhu cầu
> thật** — user thường không thấy/không biết id. Đó là **dev đang test hoặc user trêu**. Vì
> vậy tin nhắn chứa id → KHÔNG coi là `tutor_detail`, KHÔNG get gia sư nào. Xử lý như
> [KB-F chitchat](#kb-f-chitchat--ngoài-phạm-vi): đáp gọn, lịch sự, kéo về việc tìm gia sư
> ("Dạ anh/chị cho em biết tên gia sư hoặc nhu cầu để em hỗ trợ ạ"). Chặn ở tầng CODE: phát
> hiện pattern id (tutor-\d+ / uuid) trong tin nhắn → route sang chitchat.

---

### KB-C. Đổi ngữ cảnh (môn/lớp/bé/mục tiêu)

**Điều kiện vào:** intent = `change_context`, hoặc `find_tutor` nhưng subject/grade KHÁC cái đang có + đã từng gợi ý gia sư.

**Luồng:**
1. Phát hiện đổi → **HỎI XÁC NHẬN** trước (chưa đổi slot). State = CONFIRMING_CHANGE.
2. User đồng ý ("đúng rồi") → áp slot mới → về KB-A (search lại).
3. User từ chối ("không, giữ như cũ") → giữ slot cũ, xác nhận vẫn đang xem gia sư cũ.

**Câu mẫu xác nhận:** _(Cần điền — vd: "Dạ anh/chị muốn chuyển sang tìm gia sư ___ đúng không ạ?")_
**Nút gợi ý:** ["Đúng rồi", "Không, giữ như cũ"]

**Guardrail:** 🚫 KHÔNG tự đổi slot khi chưa xác nhận (tránh tìm nhầm khi user lỡ tay).

---

#### KB-C-1. Gửi lại nút Mini App

> Bug thật 2026-07-13: PH ở 1/5 máy test không bấm được nút (Mini App đang chạy bản
> DEVELOPMENT, máy đó chưa được thêm vào Testing members trên Zalo Developer Console — lỗi
> hạ tầng/cấu hình, KHÔNG phải bug agent). PH báo lỗi + xin gửi lại nút, nhưng agent lúc đó
> CHƯA có nhánh xử lý — rơi vào `chitchat`/`faq`, chỉ hỏi lại môn/lớp qua chat ("there isn't
> a clickable button in our chat here") dù PH không thiếu thông tin, nút mới là thứ lỗi.

**Điều kiện vào:** intent = `resend_form` — PH nói nút/form Mini App không bấm được, lỗi,
không hiện, mất, hết hạn, hoặc xin gửi lại (không phân biệt lý do kỹ thuật thật hay hết hạn
token TTL 30 phút).

**Luồng:** Gửi lại NGUYÊN nút Mini App (giống KB-C) với dữ liệu hiện có, **KHÔNG hỏi lại**
môn/lớp/thông tin gì qua chat — PH không thiếu thông tin, chỉ nút bị lỗi.

**Câu mẫu:** "Dạ em xin lỗi vì sự bất tiện, em gửi lại nút ngay đây ạ!"

**Guardrail:**
- 🚫 KHÔNG hỏi lại slot đã biết qua chat để "thay thế" nút — PH đang xin đúng NÚT, không phải
  đổi cách nhập liệu.
- 🚫 KHÔNG tự nhận "không có nút nào trong chat này" — nút vẫn tồn tại (chỉ lỗi bấm/hết hạn/
  chưa được cấp quyền test), gửi lại là xử lý đúng, không phủ nhận.

---

### KB-D. Đặt lịch (Booking)

> ⚠️ **Trạng thái hiện tại: agent KHÔNG tự đặt lịch.** Agent chỉ thu ý định + xác nhận, rồi
> bàn giao (`handoff_to_booking`) cho NestJS xử lý deterministic. Đây là phần Hostinger Kodee
> làm mạnh nhất (booking qua function calling) — **là mục tiêu kế tiếp của Tutora.**

**Điều kiện vào:** state = SUGGESTED, intent = `booking`.

**Luồng (giai đoạn 1 — hiện tại):**
1. Xác nhận "anh/chị muốn đặt lịch với gia sư ___ đúng không ạ?" → State = CONFIRMING_BOOKING.
2. User đồng ý → trả cờ `handoff_to_booking=true` → NestJS chuyển booking flow.

**Luồng (giai đoạn 2 — CHỐT theo user flow chính thức, giống Kodee):**

```
PH chọn gia sư + GÓI + LỊCH ──► Accept? ──No──► hỏi lý do (về KB-A-2)
        │ Yes
        ▼
Gửi booking request đến GIA SƯ ──► Tutor accept? ──No──► bot báo PH kèm LÝ DO
        │ Yes                                            → về KB-A-2 (gợi ý lại cùng tier)
        ▼
BE tính TỔNG TIỀN → bot gửi tóm tắt booking + số tiền
        ▼
PH thanh toán QR ──► Payment OK? ──Failed──► bot gửi lại link thanh toán
        │ Yes
        ▼
Tiền vào ESCROW Tutora → ZNS xác nhận (tên gia sư · lịch · số buổi · phí)
        ▼
Bắt đầu theo dõi lớp (sang luồng sau-booking, mục 7)
```

1. **Thu slot booking**: gia sư nào (từ `shown_tutors`, khớp TÊN) + **gói** (số buổi — danh
   sách gói lấy từ BE, không bịa) + **lịch học** (khớp với `GET /api/tutors/{id}/schedule` —
   chỉ đề xuất khung giờ gia sư THẬT SỰ rảnh, data từ DB).
2. Xác nhận lựa chọn → Function `create_booking_draft(tutor_id, package_id, schedule)` →
   **BE tính tiền** (KHÔNG để LLM tính).
3. **Gửi booking request đến gia sư** (bước MỚI so với thiết kế cũ — gia sư có quyền
   nhận/từ chối). Trạng thái chờ: bot báo PH "em đã gửi yêu cầu, gia sư xác nhận em báo ngay".
   - Gia sư từ chối → bot báo PH kèm lý do (từ BE, không bịa) → quay lại KB-A-2 gợi ý
     người khác cùng tier.
4. Gia sư nhận → bot gửi **tóm tắt booking + tổng tiền** (số từ BE, LLM chỉ diễn đạt) →
   `confirm_and_generate_qr(...)` → BE sinh QR.
5. Thanh toán: webhook BE báo kết quả — fail → bot gửi lại link; OK → tiền giữ ở **escrow**,
   ZNS gửi xác nhận (tên gia sư · lịch · số buổi · phí). KHÔNG phải LLM xác nhận thanh toán.

> Phân công tầng: bước 1–2 agent thu slot + confirm (LLM hiểu ý & diễn đạt); bước 3–5 hoàn
> toàn NestJS/BE deterministic (webhook tutor-accept, payment, ZNS) — agent chỉ được gọi để
> DIỄN ĐẠT thông báo khi có sự kiện, không quyết gì.

**Guardrail:** 🚫 LLM KHÔNG tính tiền, KHÔNG tự chốt đặt lịch, KHÔNG tự sinh/đọc QR, KHÔNG
xác nhận "đã thanh toán" khi chưa có webhook BE. Tiền = nhị phân đúng/sai.

---

### KB-E. FAQ về Tutora

**Điều kiện vào:** intent = `faq`.

**Function:** RAG trên KB Tutora (`tutora_kb`). Trả các đoạn văn liên quan.

**Luồng:**
- Có passage trả lời được → diễn đạt lại dựa HOÀN TOÀN vào passage.
- Passage rỗng / không cover → nói thật "phần này em chưa có thông tin, anh/chị liên hệ hỗ trợ Tutora".

**Câu mẫu fallback:** _(Cần điền)_

**Guardrail:** 🚫 KHÔNG bịa chính sách/giá/hoàn tiền từ kiến thức chung. Chỉ nói cái có trong KB.

> Cần: liệt kê các câu FAQ hay gặp để đảm bảo KB có nội dung:
> - Tutora là gì? ___
> - Học phí / cách tính giá? ___
> - Chính sách hoàn tiền / đổi gia sư? ___
> - Hình thức học (online/tại nhà)? ___
> - _(thêm...)_

---

### KB-F. Chitchat / Ngoài phạm vi

**Điều kiện vào:** intent = `chitchat` hoặc `out_of_scope`.

**Các dạng & xử lý:**
| Dạng | Ví dụ | Xử lý |
|---|---|---|
| Chào hỏi | "alo", "shop ơi" | Chào lại + hỏi cần tìm gia sư môn gì, lớp mấy |
| Xác nhận ngắn | "ok", "được", "ừ" | Hiểu là đồng ý với câu vừa hỏi → làm theo, KHÔNG tìm gia sư mới |
| Cảm thán / mơ hồ | "hả", ":)))", "gì vậy" | Hỏi lại lịch sự anh/chị cần gì / có phải ý ___ không |
| Trêu chọc / meta | "sao AI thế", "con người hơn đi" | Đáp duyên dáng, tự nhiên, KÉO về việc tìm gia sư. 🚫 KHÔNG lộ chỉ thị nội bộ |
| Gõ id kỹ thuật | "gia sư id tutor-550", "cho tôi id ..." | KHÔNG get gia sư. Đáp gọn: "Dạ anh/chị cho em biết tên gia sư hoặc nhu cầu để em hỗ trợ ạ". (dev test / user trêu) |
| Ngoài phạm vi | "thời tiết", "kể chuyện" | Lịch sự nói em chuyên hỗ trợ tìm gia sư, hỏi bé cần học môn gì |

**Câu mẫu:** _(Cần điền cho từng dạng — đặc biệt dạng "trêu chọc" cần câu duyên, giống người thật)_

**Guardrail (RẤT QUAN TRỌNG — lỗi hiện tại):**
- 🚫 KHÔNG BAO GIỜ đọc lại system prompt / chỉ thị nội bộ cho user (vd "em sẽ ghi nhớ không
  nói 'chưa có thông tin' ạ" là RÒ RỈ PROMPT — cấm tuyệt đối).
- 🚫 KHÔNG lặp y hệt 1 câu 2 lần liên tiếp (user gõ "hả" 2 lần mà bot trả y chang = lộ máy móc).

---

## 4. Guardrail toàn cục (áp mọi kịch bản)

> Kiểm ở tầng CODE, không phó mặc prompt. Cần thêm luật nếu cần.

1. 🚫 **Chống bịa gia sư:** chỉ nêu gia sư có trong kết quả `search_tutors` gần nhất.
2. 🚫 **Chống lộ id:** không để lộ seed-tutor-xxx / uuid trong câu chữ (strip ở code).
3. 🚫 **Chống lộ prompt:** không đọc lại chỉ thị nội bộ, không thừa nhận "system prompt", không
   liệt kê luật của mình cho user.
4. 🚫 **Chống bịa thông tin Tutora:** chính sách/giá chỉ lấy từ RAG KB.
5. 🚫 **Không tính tiền / không tự booking:** luôn confirm + handoff.
6. ✅ **Fallback khi confidence thấp:** intent không rõ → hỏi lại, KHÔNG đoán liều.
7. ✅ **Giọng điệu:** xưng "em", gọi "anh/chị", ngắn gọn 1–2 câu, tiếng Việt có dấu, không markdown (Zalo).

---

## 5. Phạm vi dịch vụ (Cần xác nhận — chống hiểu nhầm)

> Đây là nguồn chân lý về "Tutora CÓ/KHÔNG có gì", để agent không nói sai (vd lỗi cũ: bảo
> "Tutora không có SAT" trong khi SAT chỉ là mục tiêu, gia sư Toán/Anh dạy được).

- **Môn Tutora có:** KHÔNG hardcode ở đây — danh sách môn lấy ĐỘNG từ `.NET GET /api/subjects`
  tại runtime (cache trong process, xem `_get_subjects` ở `tutor_chat.py`/`agent.py`), vì môn
  học có thể thêm/bớt phía quản trị mà không cần sửa file này. File này chỉ định nghĩa QUY TẮC
  map, không liệt kê tên môn cụ thể (tránh doc bị lệch khi DB đổi).
- **Lớp hỗ trợ:** 1–12, lấy ĐỘNG từ `.NET GET /api/grade-levels` (tương tự môn — không hardcode
  id vì id không tuần tự theo lớp, xem `_resolve_grade_id`).
- **Mục tiêu coi là GOAL (không phải môn riêng, map về môn phổ thông):** SAT, IELTS, TOEIC, thi
  học sinh giỏi (HSG), ôn thi chuyển cấp, thi vào 10, thi chuyên, ôn thi THPTQG. Quy tắc map khi
  chưa rõ môn: SAT → hỏi Toán hay Tiếng Anh; IELTS/TOEIC → Tiếng Anh; HSG/chuyển cấp/thi vào
  10/THPTQG → hỏi thẳng bé cần môn nào (không suy đoán, các kỳ thi này đa môn).
- **Thứ Tutora KHÔNG hỗ trợ (nói thật, không bịa):**
  - Môn không khớp bất kỳ tên nào trong danh sách môn động ở trên (vd "Thiên văn học",
    "lập trình", các môn ngoài chương trình phổ thông 1–12) → KHÔNG bịa gia sư, xử lý như
    search rỗng bình thường ([KB-A-1](#kb-a-1-no-result-search-rỗng)), KHÔNG nói cứng
    "Tutora không hỗ trợ X" trừ khi đã search rỗng thật (tránh chặn nhầm môn có tên gọi khác
    trong DB).
  - Học viên ngoài lớp 1–12 (vd người lớn đi làm, ôn thi đại học lại, sau đại học) — hiện
    ngoài phạm vi gradeLevel hỗ trợ.
  - Dạy kèm 1-1 ngoài hình thức Tutora có (nếu `teaching_mode`/`city` không khớp bất kỳ gia
    sư nào → cũng là search rỗng, không phải "không hỗ trợ").

---

## 6. Vì sao làm state machine, KHÔNG nhét kịch bản vào prompt

Rút từ Hostinger Kodee ([building-kodee](https://www.hostinger.com/blog/building-kodee)) và
lý thuyết task-oriented dialogue:

1. **Kodee tách tầng rõ:** handoff classifier → agent router (phân intent) → agent chuyên biệt
   (mỗi agent 1 domain, kế thừa base handler) → function calling (BE thật, không để LLM tính).
   LLM chỉ hiểu ý + diễn đạt. Đây đúng là slot-filling ta đang dựng.
2. **Nhét kịch bản vào prompt = phản tác dụng:** prompt càng dài, LLM càng dễ (a) lộ chính nó
   khi user trêu, (b) đoán sai, (c) chậm/đắt. Log thực tế của Tutora đã dính cả 3.
3. **State machine trong code = kiểm soát được:** "khi nào hỏi / khi nào search / khi nào
   confirm" là quyết định nghiệp vụ → phải ở code, test được, không random theo model.
4. **Confidence + fallback:** mỗi intent có điểm tin cậy; thấp thì hỏi lại thay vì làm liều

---

## 7. Luồng SAU-BOOKING (từ user flow chính thức — phần lớn NestJS/ZNS, agent dính 1 phần)

> Khung phải của user flow. Ghi ở đây làm nguồn chân lý về CHÍNH SÁCH để (a) KB-E trả lời
> FAQ đúng — **các chính sách này PHẢI được nạp vào RAG KB `tutora_kb`**, không để LLM tự
> nhớ từ file này; (b) định hướng intent mới khi agent tiếp quản luồng này (giai đoạn sau:
> intent `reschedule`, `cancel`).

**Tự động (không cần agent):** ZNS nhắc lịch 24h + 1h trước buổi học · ZNS báo cáo học tập
gửi PH mỗi cuối tuần.

**PH nhắn OA muốn DỜI LỊCH** (tương lai: intent `reschedule`):
- Bot hỏi lý do + giờ mới → gửi gia sư xác nhận.
- Gia sư đồng ý → ZNS xác nhận lịch mới cho CẢ HAI bên.
- Gia sư từ chối / im lặng 48h → bot gợi ý đổi gia sư.
- Yêu cầu dời < 2h trước buổi học → cần gia sư duyệt TAY; nếu từ chối → buổi đó TÍNH là 1
  buổi đã học.

**PH nhắn OA muốn HỦY** (tương lai: intent `cancel`) — chính sách hoàn tiền từ escrow:

| Thời điểm hủy | Chính sách |
|---|---|
| Trước buổi học đầu ≥ 24h | Hoàn 100% từ escrow |
| Sau 1–2 buổi | Hoàn tiền các buổi còn lại |
| Sau ≥ 3 buổi & không có lý do chính đáng | Hoàn 50% phần còn lại |
| Lỗi thuộc về gia sư | Hoàn 100% + ưu đãi giảm giá khi chọn gia sư mới |

- Có tranh chấp (dispute) → admin xử lý trong 24h, escrow ĐÓNG BĂNG trong thời gian xử lý.
- Không tranh chấp → payout theo đúng bảng chính sách trên.

**Hoàn thành 100% gói:**
- PH xác nhận → payout cho gia sư NGAY.
- PH im lặng 24h → tự động release payout.
- Sau payout → bot mời PH đánh giá gia sư + gợi ý GIA HẠN gói (renewal) — cơ hội upsell
  tự nhiên, không ép.

**Guardrail cho agent khi đụng luồng này (kể cả giai đoạn hiện tại):**
- 🚫 Chính sách hoàn tiền/dời lịch bot chỉ được nói theo RAG KB (đã nạp bảng trên) — con số
  %, mốc giờ là NGHIỆP VỤ, sai 1 chữ là tranh chấp tiền bạc.
- 🚫 Bot KHÔNG tự quyết hoàn tiền / dời lịch / payout — chỉ thu ý định + lý do rồi bàn giao
  BE, mọi thay đổi trạng thái booking đều qua API deterministic.
