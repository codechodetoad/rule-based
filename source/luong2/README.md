# Luồng 2 — Extractor CHẨN_ĐOÁN / TRIỆU_CHỨNG / TÊN_XÉT_NGHIỆM / KẾT_QUẢ_XÉT_NGHIỆM

Trích xuất khái niệm **trực tiếp từ `input/N.txt`** (KHÔNG dùng `ref/`).
Xem thiết kế tổng thể: [../../architecture/SPLIT_PIPELINE_PLAN.md](../../architecture/SPLIT_PIPELINE_PLAN.md).

## Chạy
```bash
python source/luong2/extract_findings.py
```
Ghi ra **2 thư mục riêng** (cùng 1 lần chạy, cùng logic trích xuất):
- `output_findings/N.json` — **chỉ** `CHẨN_ĐOÁN` + `TRIỆU_CHỨNG`
- `output_tests/N.json` — **chỉ** `TÊN_XÉT_NGHIỆM` + `KẾT_QUẢ_XÉT_NGHIỆM`

## Thành phần
- `lexicon/symptoms.txt`, `diagnoses.txt`, `tests.txt` — từ điển tiếng Việt (1 cụm/dòng, `#` = comment). **Mở rộng dần** từ HPO/UMLS T184 (dịch) + dữ liệu input.
- `extract_findings.py`:
  - **Trie longest-match** theo từng dòng, có kiểm tra biên từ (word boundary) → không bắt nhầm chuỗi con.
  - **Section-aware**: heading (`Tiền sử`, `Bệnh lý mãn tính`, `Thuốc trước khi nhập viện`...) đặt ngữ cảnh; lưu ý `Tiền sử bệnh **hiện tại**` = HPI ⇒ KHÔNG historical.
  - **ConText-lite** cho assertions: `isNegated` (cue "không/không có/phủ định/âm tính", có xử lý bẫy "không đặc hiệu"), `isHistorical` (section + cue "tiền sử/đã từng/trước đây"), `isFamily` (cue "gia đình/người nhà/bố/mẹ...").
  - Ghi schema đề bài: `text`, `type`, `candidates` (rỗng cho CHẨN_ĐOÁN — chờ bước ICD), `assertions`, `position` (offset ký tự trên input gốc, đã verify `content[s:e]==text`).

## Trạng thái (v0.3)
Trích 4 loại từ 100 file input → ~1667 concept, **0 lỗi position**.
Recall so với ref (THƯỚC ĐO tham chiếu, ref KHÔNG phải chuẩn): **CHẨN_ĐOÁN 71% · THUỐC 72%(luồng1) · TRIỆU_CHỨNG 68% · TÊN_XN 64% · KẾT_QUẢ 29%**.
Lexicon chẩn đoán: **220 cụm có mã WHO ICD-10** (verify NLM API: 219/219 mã WHO hợp lệ).
- ✅ **ICD-10 (WHO) cho CHẨN_ĐOÁN**: 294/294 có mã (0 rỗng). Mã chuẩn **WHO 3–4 ký tự** khớp ví dụ chính thức (`K21.0,K21.9`), verify bằng NLM API (`verify_icd.py`) — 118/125 khớp họ mã 3 ký tự, 7 còn lại là mã WHO hợp lệ mà ICD-10-CM tách khác.
- ✅ **Lexicon mở rộng** (390 cụm) từ dữ liệu input.
- ✅ **Disambiguation type theo section**: từ mơ hồ (vd `hạ huyết áp`) → TRIỆU_CHỨNG trong section triệu chứng/dấu hiệu, CHẨN_ĐOÁN trong section bệnh lý.
- ✅ **KẾT_QUẢ_XÉT_NGHIỆM**: bắt giá trị số + định tính (âm/dương tính, bình thường/bất thường) ngay sau tên xét nghiệm.
- ✅ **Mở rộng span theo modifier** (`extend_modifiers`): nới span lõi qua vị trí/mô học/phân loại/đuôi → `Ung thư biểu mô tuyến đại tràng`, `xẹp phổi thùy dưới phải`, `Viêm phổi bệnh viện`, `... , không đặc hiệu`. Span khớp tuyệt đối: CHẨN_ĐOÁN 61%→**74%**, TRIỆU_CHỨNG 68%→**73%**.
- ✅ **Tinh chỉnh mã ung thư theo vị trí** (`refine_cancer_code`): `ung thư ... đại tràng`→C18.9, `... vú`→C50.9, `... tuyến tiền liệt`→C61 (thay vì C80 chung).
- ⏳ **Chưa làm:** model NER phủ phần lexicon bỏ sót; chuẩn hóa spacing; KẾT_QUẢ dạng phức (khoảng giá trị, đơn vị dính).

## Flag file không phải ca bệnh
Một số file không mô tả bệnh mà là **thủ thuật/sản khoa** (vd file 31: *khởi phát chuyển dạ* — "cơn co tử cung", "thai máy", "vỡ ối" là mục đánh giá chuyển dạ bình thường, không phải triệu chứng bệnh). Các file này được **flag + để output trống**, ghi vào `flagged_files.json` để review tay. Phát hiện theo ngữ cảnh file (marker chuyển dạ ≥2), nên **không ảnh hưởng ca phụ khoa thật** (vd file 49 "ra huyết âm đạo" là triệu chứng thật vẫn giữ).

## Verify mã ICD
```bash
python source/luong2/verify_icd.py   # query NLM API, soát mã theo họ 3 ký tự (chỉ dev)
```

## Giới hạn đã biết
- Recall phụ thuộc độ phủ lexicon → cần bổ sung (dịch HPO/UMLS T184) hoặc thêm tầng NER model.
- KẾT_QUẢ dựa vào việc tên xét nghiệm đứng trước số → bỏ lọt giá trị đứng rời; vài FP volume ("3L").
- Chỉ khớp đúng chính tả/khoảng trắng → bỏ lọt biến thể (`dạ dày- thực quản` vs `dạ dày - thực quản`).
