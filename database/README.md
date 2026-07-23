# Facts Database — tách entity từ ground truth để AUDIT

Sinh bằng [`source/build_facts_db.py`](../source/build_facts_db.py) từ `Opus-output/`.
Mục đích: **tách toàn bộ thực thể (entity) + index/mã** ra để kiểm tra **ground truth có chuẩn không**.

## Cấu trúc — mỗi loại có 2 định dạng: `.csv` (mở Excel) + `.json` (giống output)
```
database/
├── benh_va_trieu_chung/
│   ├── chan_doan.csv / chan_doan.json
│   ├── trieu_chung.csv / trieu_chung.json
│   ├── ten_xet_nghiem.csv / ten_xet_nghiem.json
│   └── ket_qua_xet_nghiem.csv / ket_qua_xet_nghiem.json
└── thuoc/
    └── thuoc.csv / thuoc.json
```

**CSV** (cột): `entity | index_ma | so_lan | so_file | cac_file | KHONG_NHAT_QUAN` (loại không mã bỏ cột index_ma & cờ). BOM UTF-8 để Excel đọc tiếng Việt.

**JSON** (list dict, giống schema output — `text`/`type`/`candidates` + metadata gộp):
```json
{ "text": "tăng huyết áp", "type": "CHẨN_ĐOÁN", "candidates": ["I10"],
  "inconsistent": false, "occurrences": 23, "files": [3,5,11,...] }
```
- **candidates** = hợp các mã (ICD/RxNorm) GT gán cho entity.
- **inconsistent=true** + **candidate_variants** khi GT gán mã KHÁC nhau giữa file (kèm file của từng biến thể) → điểm cần soát.
- Loại không mã (triệu chứng/xét nghiệm/kết quả): chỉ `text/type/occurrences/files`.

## Số lượng entity (unique) trong ground truth
| Loại | Unique | Bất nhất mã |
|---|---|---|
| CHẨN_ĐOÁN | 348 | **4** |
| TRIỆU_CHỨNG | 441 | 0 |
| TÊN_XÉT_NGHIỆM | 198 | 0 |
| KẾT_QUẢ_XÉT_NGHIỆM | 206 | 0 |
| THUỐC | 144 | **1** |

## Phát hiện audit chính

### 1. Entity bị gán MÃ KHÁC NHAU (bất nhất trong GT)
| Entity | Mã trong GT | File | Đánh giá |
|---|---|---|---|
| `Nhồi máu cơ tim` | I25.2 \|\| I21.9 | 43,73,76 | ⚪ hợp lý — cấp (I21.9) vs cũ (I25.2) tùy ngữ cảnh |
| `áp xe` | K65.1 \|\| L02.91 | 17,52,74 | ⚪ hợp lý — ổ bụng vs da tùy vị trí |
| `ung thư biểu mô tuyến` | C18.9 \|\| C24.0 | 47,69 | ⚠️ khác vị trí (đại tràng vs đường mật) — cần đọc lại ngữ cảnh |
| `ung thư biểu mô tế bào thận` | C64.9 \|\| C64.1 | 54,69 | ⚠️ khác bên (không xác định vs phải) |
| `bactrim` | 10829 \|\| 197454 | 13,74,96 | ⚠️ **lỗi thật** — cùng brand nhưng 2 RxCUI khác (10829=sulfamethoxazole đơn, 197454=phối hợp SMX/TMP) |

### 2. Ground truth TRỘN chuẩn mã ICD (WHO vs CM)
Trong `chan_doan.csv` thấy rõ: `I10`, `I35.0`, `K72.90`, `I25.10`, `I48.91`, `H47.10`, `E11.9`... — **lẫn lộn 4 ký tự (WHO) và 5 ký tự (ICD-10-CM)**. Đây là bằng chứng GT **không nhất quán định dạng mã** (đúng như `GROUND_TRUTH_PIPELINE.md` §10 tự nhận: mã là phỏng đoán).

## Kết luận sơ bộ về độ chuẩn của ground truth
- **Nhất quán entity/mã: khá tốt** — chỉ 4/348 bệnh và 1/144 thuốc có mã bất nhất, phần lớn là khác biệt **hợp lý theo ngữ cảnh** (cấp/cũ, vị trí giải phẫu).
- **Định dạng mã: KHÔNG chuẩn** — trộn WHO/CM.
- **Chưa kiểm tính ĐÚNG của mã** (mới kiểm tính nhất quán). Bước tiếp: đối chiếu từng mã với **NLM/RxNav API** để biết mã có tồn tại/đúng bệnh-thuốc không.

## Tạo lại
```bash
python source/build_facts_db.py
```
