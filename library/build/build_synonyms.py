#!/usr/bin/env python3
"""Trích bộ SYNONYM thuốc từ rich_data/RXNCONSO.RRF -> library/drug_synonyms.json
(dict {tên(lowercase) -> rxcui}, CÙNG ĐỊNH DẠNG với drug_forms.json).

KHÔNG merge vào drugs.json/drug_forms.json — file độc lập để soi "còn thiếu gì".

Nguồn synonym = các dòng TTY ∈ {SY, TMSY} trong RXNCONSO (tên gọi thay thế của 1
rxcui: cách viết quốc tế/hóa học/muối, vd aluminium hydroxide, mecillinam, ATP).
Áp normalize_dose để '25 MG'->'25mg' cho khớp format drug_forms. Trùng key nhiều mã
-> nối ',' (string thuần, không mảng).

Chạy: python library/build/build_synonyms.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from normalize_drugs import normalize_dose  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RRF = os.path.join(ROOT, "rich_data", "RXNCONSO.RRF")
OUT = os.path.join(ROOT, "library", "drug_synonyms.json")
SYN_TTYS = {"SY", "TMSY"}


def add_entry(d, text, code):
    key = text.lower()
    if key in d:
        codes = d[key].split(",")
        if code not in codes:
            d[key] = d[key] + "," + code
    else:
        d[key] = code


def main():
    if not os.path.exists(RRF):
        print(f"KHÔNG TÌM THẤY: {RRF}", file=sys.stderr)
        sys.exit(1)

    # thư viện hiện tại: để đối chiếu "còn thiếu gì"
    drugs = json.load(open(os.path.join(ROOT, "library", "drugs.json"), encoding="utf-8"))
    forms = json.load(open(os.path.join(ROOT, "library", "drug_forms.json"), encoding="utf-8"))
    have_keys = set(drugs) | set(forms)
    have_rxcui = set()
    for v in list(drugs.values()) + list(forms.values()):
        have_rxcui.update(v.split(","))

    syn = {}
    raw_rows = 0
    for line in open(RRF, encoding="utf-8"):
        p = line.split("|")
        if len(p) < 15 or p[12] not in SYN_TTYS:
            continue
        rxcui = p[0].strip()
        name = normalize_dose(p[14].strip())
        if not rxcui or not name:
            continue
        raw_rows += 1
        add_entry(syn, name, rxcui)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(syn, f, ensure_ascii=False, indent=2)

    # ---- phân tích: chúng ta đang thiếu gì ----
    key_already = sum(1 for k in syn if k in have_keys)     # synonym đã có sẵn (không mới)
    key_new = len(syn) - key_already                         # cách viết MỚI
    syn_known_drug = 0    # synonym trỏ thuốc ĐÃ CÓ -> chỉ là spelling mới
    syn_missing_drug = 0  # synonym trỏ rxcui CHƯA có -> có thể là thuốc còn THIẾU
    missing_rxcui = {}
    for k, v in syn.items():
        codes = v.split(",")
        if any(c in have_rxcui for c in codes):
            syn_known_drug += 1
        else:
            syn_missing_drug += 1
            for c in codes:
                missing_rxcui.setdefault(c, k)

    print(f"RXNCONSO SY/TMSY: {raw_rows} dòng -> {len(syn)} synonym unique (lowercase)")
    print(f"Ghi -> {OUT}\n")
    print("=== ĐỐI CHIẾU VỚI THƯ VIỆN HIỆN TẠI ===")
    print(f"  synonym KEY đã có sẵn (trùng key drug_forms):        {key_already}")
    print(f"  synonym KEY mới (cách viết ta CHƯA có):              {key_new}")
    print(f"  synonym trỏ rxcui ĐÃ CÓ (chỉ thêm chính tả):         {syn_known_drug}")
    print(f"  synonym trỏ rxcui CHƯA CÓ (thuốc có thể THIẾU):      {syn_missing_drug}")
    print(f"  => số rxcui thuốc CÒN THIẾU (từ synonym):            {len(missing_rxcui)}")
    print("\n  mẫu 25 synonym trỏ thuốc ta CHƯA có (nghi thiếu):")
    for c, k in list(missing_rxcui.items())[:25]:
        print(f"    rxcui {c:>9}  {k}")


if __name__ == "__main__":
    main()
