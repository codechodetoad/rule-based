# Luồng 1 — Extractor THUỐC → RxNorm

Trích xuất thuốc **trực tiếp từ `input/N.txt`** + gán mã **RxCUI (RxNorm)**.
Thiết kế tổng thể: [../../architecture/SPLIT_PIPELINE_PLAN.md](../../architecture/SPLIT_PIPELINE_PLAN.md) · chi tiết RxNorm: [../../architecture/RXNORM_LINKING_PLAN.md](../../architecture/RXNORM_LINKING_PLAN.md).

## Chạy
```bash
python source/luong1/build_rxnorm.py    # (DEV, 1 lần) query RxNav -> drugs_rxnorm_cache.json
python source/luong1/extract_drugs.py   # đọc input/ + cache -> output_drugs/N.json
```
> Runtime của `extract_drugs.py` **chỉ đọc cache tĩnh** (offline). `build_rxnorm.py` gọi RxNav chỉ ở dev-time; RxNav API miễn phí, không key.

## Thành phần
- `lexicon/drugs.txt` — tên thuốc (generic EN + brand + cụm tiếng Việt). Cụm VN/class gán RxCUI trực tiếp (`cụm | RxCUI | note`, vd `thở oxy | 7806`, `cotrimoxazol | 197454`).
- `extract_drugs.py`:
  - **Trie longest-match** tên thuốc + kiểm tra biên từ.
  - **Mở rộng span** liều/đường/dạng/lịch dùng (`metoprolol 25mg po bid`, `aspirin 325mg x 1`, `lasix 40mg daily`) — **DỪNG** ở phần chỉ định (`cho viêm...`, `(uống hôm nay)`).
  - **ConText assertions**: `isHistorical` (mục "Thuốc trước khi nhập viện"/"Tiền sử"/"trước khi nhập viện"), `isNegated` ("không dùng", "ngừng").
  - `candidates` = override(VN) › cache(span) › cache(tên) › [].
- `build_rxnorm.py` — resolve RxCUI: span có liều → `approximateTerm`; tên trần → `findRxcuiByString` (ingredient) → fallback `approximateTerm`. Cache lại.
- `drugs_rxnorm_cache.json` — cache tĩnh (ship kèm để chạy offline).

## Trạng thái (v0.1)
- **183 THUỐC** từ 100 input · **183/183 có RxCUI** · **0 lỗi position**.
- Assertions: isNegated 20 · isHistorical 99.
- Mã khớp GT nhiều ca: `doxycycline→3640`, `atenolol→1202`, `thở oxy→7806`, `clopidogrel→32968`, `lasix→furosemide`.
- Sửa FP: bỏ `oxy` đứng một mình (bắt nhầm "độ bão hòa **oxy**" = SpO2); vá `laxis`/`cotrimoxazol`.

## Giới hạn đã biết
- **Độ chi tiết mã (granularity):** `approximateTerm` trả mã RXNORM top (thường là SCDC "ingredient+strength", vd `metoprolol 25mg`→331561) — chưa luôn đúng SCD đầy đủ như GT (866924). Cần thêm bước chọn TTY (SCD/SBD) để khớp chính xác hơn.
- Recall phụ thuộc lexicon tên thuốc → cần bổ sung khi gặp thuốc mới (đặc biệt brand VN, thuốc phối hợp dính token như `albuterolipratropium`).
- `isHistorical` theo section — vài ca thuốc dùng tại ED (không tiền sử) có thể bị gán nhầm do section "trước khi nhập viện".
