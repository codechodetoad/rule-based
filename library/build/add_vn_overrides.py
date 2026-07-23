#!/usr/bin/env python3
"""Bổ sung tên thuốc tiếng Việt / brand VN đã verify thủ công vào CẢ 2 file:
library/drugs.json VÀ library/drug_forms.json (dict {tên(lowercase) -> mã}).

DAV (dichvucong.dav.gov.vn) là Angular SPA (ABP framework); không tìm được endpoint
public tĩnh trả danh mục thuốc (cần trace network trình duyệt thật/đăng nhập) ->
bỏ qua crawl tự động (chi phí cao, lợi ích thấp: bệnh án VN thường ghi thuốc bằng
tên INN/Latin trực tiếp, đã có trong RxNorm IN/PIN/BN).

Thay vào đó, merge các entry đã verify (round-trip RxNav) từ
source/luong1/lexicon/drugs.txt — gồm brand VN hay viết sai/retired (laxis, bactrim,
vicodin, toradol, taxol, cefepim...) và cụm tiếng Việt/class (thở oxy, lợi tiểu...).

Chạy: python library/build/add_vn_overrides.py
"""
import json
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LEX = os.path.join(ROOT, "source", "luong1", "lexicon", "drugs.txt")
TARGETS = [
    os.path.join(ROOT, "library", "drugs.json"),
    os.path.join(ROOT, "library", "drug_forms.json"),
]


def load_overrides():
    overrides = []
    with open(LEX, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#") or "|" not in s:
                continue
            parts = [p.strip() for p in s.split("|")]
            name = parts[0]
            codes = [c.strip() for c in parts[1].split(",") if c.strip()] if len(parts) > 1 else []
            if not name or not codes:
                continue
            overrides.append((name, codes[0]))  # 1 override -> 1 mã chính
    return overrides


def apply_to(fp, overrides):
    with open(fp, encoding="utf-8") as f:
        d = json.load(f)

    added = 0
    for name, code in overrides:
        key = name.lower()
        if key in d:
            codes = d[key].split(",")
            if code not in codes:
                d[key] = d[key] + "," + code
                added += 1
        else:
            d[key] = code
            added += 1

    with open(fp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

    print(f"Thêm/cập nhật {added} key override -> tổng {len(d)} key trong {fp}")


def main():
    overrides = load_overrides()
    for fp in TARGETS:
        apply_to(fp, overrides)


if __name__ == "__main__":
    main()
