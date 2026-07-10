# Kịch bản Agent Tư vấn Tutora (Spec State Machine)

> **File này là NGUỒN CHÂN LÝ về hành vi của agent.** Viết kịch bản ở đây →
> Developer code đúng theo → LLM **KHÔNG đọc file này**. LLM chỉ được gọi ở 2 điểm hẹp:
> (1) hiểu ý người dùng (trích intent + slot), (2) diễn đạt câu chữ tiếng Việt.
>
> Học theo cách Hostinger Kodee & các task-oriented dialogue system làm: flow là **state
> machine tường minh trong CODE**, không phải prompt cho LLM tự diễn. Xem "Vì sao" ở cuối file.

---

## 0. Khái niệm nền (đọc trước)

**SLOT** = mẩu thông tin hệ thống cần thu để phục vụ. Slot của luồng tìm gia sư:

| Slot | Ý nghĩa | Bắt buộc để search? | Nguồn |
|---|---|---|---|
| `subject` | Môn học (Toán, Anh, Hóa...) | ✅ | user nói / map từ mục tiêu |
| `grade` | Lớp (1–12) | ✅ | user nói |
| `goal` | Mục tiêu (mất gốc / củng cố / nâng cao / ôn thi / luyện SAT...) | ✅ | user nói |
| `preferences` | Mong muốn gia sư (giới tính, tính cách, hình thức học, giá) | ❌ (tùy chọn) | user nói |

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
| `booking` | Muốn đặt lịch / đăng ký học | "đặt lịch cô ấy", "đăng ký học" | [KB-D](#kb-d-đặt-lịch-booking) |
| `faq` | Hỏi về Tutora (chính sách, cách hoạt động, giá chung) | "Tutora hoàn tiền không", "học phí thế nào" | [KB-E](#kb-e-faq-về-tutora) |
| `chitchat` | Chào hỏi / xác nhận ngắn / cảm thán / trêu / **gõ id kỹ thuật** | "alo", "ok", ":)))", "sao AI thế", "gia sư id tutor-550" | [KB-F](#kb-f-chitchat--ngoài-phạm-vi) |
| `out_of_scope` | Ngoài phạm vi Tutora (không phải tìm gia sư) | "thời tiết", "kể chuyện cười" | [KB-F](#kb-f-chitchat--ngoài-phạm-vi) |

> ⚠️ **Tin nhắn chứa id kỹ thuật (tutor-xxx / uuid) KHÔNG phải nhu cầu thật** — user không
> thấy/không biết id; đó là dev test hoặc trêu. → luôn về `chitchat`, KHÔNG get gia sư. Xem [KB-B](#kb-b-hỏi-chi-tiết-gia-sư).

---

## 2. Bản đồ State (trạng thái hội thoại)

```
                    ┌─────────────┐
    (tin nhắn đầu)  │   GREETING  │
                    └──────┬──────┘
                           │ find_tutor
                           ▼
              ┌────────────────────────┐   thiếu slot
              │   COLLECTING_SLOTS      │◄────────────┐
              │ (thu subject/grade/goal)│             │ hỏi slot còn thiếu
              └────────────┬───────────┘─────────────┘
                           │ đủ subject+grade+goal
                           ▼
                    ┌─────────────┐   rỗng → NO_RESULT
                    │  SEARCHING  │──────────────────►
                    └──────┬──────┘
                           │ có gia sư
                           ▼
                    ┌─────────────┐  tutor_detail/availability → vẫn ở đây
                    │  SUGGESTED  │  change_context → CONFIRMING_CHANGE
                    │ (đã ra card)│  booking → CONFIRMING_BOOKING
                    └──────┬──────┘
                           │ booking (đã confirm)
                           ▼
                    ┌─────────────┐
                    │  → HANDOFF  │  (NestJS nhận cờ, chuyển booking flow)
                    │   BOOKING   │
                    └─────────────┘
```

> Cần: mỗi state = một "màn". Ô trên là khung; điền chi tiết từng màn ở mục 3.

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

### KB-A. Tìm gia sư

**Điều kiện vào:** intent = `find_tutor` (từ GREETING / COLLECTING_SLOTS / SUGGESTED).

**Logic thu slot (thứ tự hỏi):**
1. Thiếu `subject` → hỏi môn. _(Nếu đã có `goal` kiểu SAT/IELTS mà chưa rõ môn → gợi ý map: SAT→Toán/Anh, IELTS→Anh)_
2. Thiếu `grade` → hỏi lớp. _(chỉ hỏi lớp, không hỏi lại môn)_
3. Thiếu `goal` → hỏi mục tiêu (1 câu). _(mất gốc/củng cố/nâng cao/ôn thi)_
4. Đủ 3 slot → **gọi search** (Function: `POST /api/tutors/recommend`).

**Quy tắc quan trọng:**
- ⚠️ Đọc lại toàn bộ hội thoại — KHÔNG hỏi lại slot đã biết. "toán lớp 7" ở 1 câu = có cả subject + grade.
- ⚠️ User giục ("đưa tôi gia sư", "có ai không") → search NGAY với slot hiện có, đừng hỏi thêm.
- ⚠️ Chỉ hỏi tối đa ___ lượt trước khi search. _(Cần điền: gợi ý 2)_

**Kết quả search:**
- Có gia sư → state = SUGGESTED. Giới thiệu tối đa ___ người (Cần điền: 2, khớp số card). Mỗi
  người: tên + 1 lý do hợp nhu cầu. KHÔNG nói tổng số, KHÔNG liệt kê lại giá/rating (đã có card).
- Rỗng → [KB-A-1 No result].

**Câu mẫu (LLM diễn đạt theo):**
> _(Cần điền câu mẫu hỏi môn/lớp/mục tiêu + câu mẫu giới thiệu gia sư)_

**Guardrail:**
- 🚫 KHÔNG nêu tên gia sư nào không có trong kết quả search vừa gọi (chống bịa).
- 🚫 KHÔNG lộ id kỹ thuật (tutor-xxx, uuid) trong câu chữ.

#### KB-A-1. No result (search rỗng)
**Câu mẫu:** _(Cần điền — vd: "Dạ hiện chưa có gia sư ___ phù hợp, anh/chị thử nới tiêu chí giúp em nhé")_
**Nhánh:** gợi ý nới tiêu chí / để lại liên hệ. 🚫 TUYỆT ĐỐI không bịa gia sư để lấp.

---

### KB-B. Hỏi chi tiết gia sư

**Điều kiện vào:** state = SUGGESTED, intent = `tutor_detail` hoặc `availability`.

**Xác định gia sư nào (chỉ theo TÊN, KHÔNG theo id):**
1. User nhắc TÊN → khớp tên trong danh sách đã gợi ý (`shown_tutors`).
2. Không rõ + chỉ có 1 gia sư đã gợi ý → lấy người đó.
3. Không rõ + nhiều người → **HỎI LẠI** "anh/chị muốn xem gia sư nào ạ?" _(KHÔNG lấy đại người đầu — đây là bug hiện tại)_

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

### KB-D. Đặt lịch (Booking)

> ⚠️ **Trạng thái hiện tại: agent KHÔNG tự đặt lịch.** Agent chỉ thu ý định + xác nhận, rồi
> bàn giao (`handoff_to_booking`) cho NestJS xử lý deterministic. Đây là phần Hostinger Kodee
> làm mạnh nhất (booking qua function calling) — **là mục tiêu kế tiếp của Tutora.**

**Điều kiện vào:** state = SUGGESTED, intent = `booking`.

**Luồng (giai đoạn 1 — hiện tại):**
1. Xác nhận "anh/chị muốn đặt lịch với gia sư ___ đúng không ạ?" → State = CONFIRMING_BOOKING.
2. User đồng ý → trả cờ `handoff_to_booking=true` → NestJS chuyển booking flow.

**Luồng (giai đoạn 2 — Cần thiết kế thêm, giống Kodee):**
- Thu slot booking: gia sư nào, ngày, giờ, số buổi.
- Function `create_booking_draft(...)` → BE tính tiền (KHÔNG để LLM tính).
- Xác nhận số tiền deterministic → `confirm_and_generate_qr(...)` → BE sinh QR.
- _(Cần điền chi tiết các bước booking mong muốn ở đây)_ ________________

**Guardrail:** 🚫 LLM KHÔNG tính tiền, KHÔNG tự chốt đặt lịch. Tiền = nhị phân đúng/sai.

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

- **Môn Tutora có:** _(Cần liệt kê — Toán, Anh, Lý, Hóa, Văn, Sinh, Sử, Địa, Tin...)_
- **Lớp hỗ trợ:** _(vd 1–12)_
- **Mục tiêu coi là GOAL (không phải môn riêng, map về môn phổ thông):** SAT, IELTS, TOEIC, thi
  HSG, ôn thi chuyển cấp, thi vào 10, thi chuyên, THPTQG... _(Cần bổ sung)_
- **Thứ Tutora KHÔNG hỗ trợ (nói thật, không bịa):** _(Cần điền — vd môn ngoài danh sách, dạy người lớn đi làm...)_

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
