#!/usr/bin/env python3
"""Chuẩn hóa liều (25 MG -> 25mg) + dịch dạng bào chế sang tiếng Việt.

Xem architecture/DRUG_VN_NORMALIZE_PLAN.md (đã chốt PHƯƠNG ÁN B). Sinh 2 file:
  library/drugs.json       = tên trần (IN/PIN/BN) + "tên+liều" (BỎ FORM) -> RxCUI mặc định
  library/drug_forms.json  = "tên+liều+dạng_VN" -> RxCUI đúng form (lớp phân biệt)

Input: cache _cache/rxnorm_{IN,PIN,BN,SCD,SBD}.json (do build_rxnorm_lib.py tải).
Chạy: python library/build/normalize_drugs.py
Sau đó: python library/build/add_vn_overrides.py  (merge brand VN đã verify)
"""
import json
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CACHE_DIR = os.path.join(os.path.dirname(__file__), "_cache")
OUT_DRUGS = os.path.join(ROOT, "library", "drugs.json")
OUT_FORMS = os.path.join(ROOT, "library", "drug_forms.json")
RRF = os.path.join(ROOT, "rich_data", "RXNCONSO.RRF")  # tùy chọn: bổ sung MIN/SCDF/SBDF

# ---------- 1. chuẩn hóa liều: "25 MG" -> "25mg", "40 MG/ML" -> "40mg/ml" ----------
UNIT_MAP = {
    "MG": "mg", "MCG": "mcg", "ML": "ml", "UNT": "unt", "MEQ": "meq",
    "MMOL": "mmol", "BAU": "bau", "ACTUAT": "actuat", "HR": "hr",
    "IU": "iu", "CELLS": "cells",
}
_UNITS_ALT = "|".join(UNIT_MAP.keys())
DOSE_RE = re.compile(
    rf'(\d+\.?\d*)\s*({_UNITS_ALT})\b(?:\s*/\s*({_UNITS_ALT}))?',
    re.IGNORECASE,
)
PCT_RE = re.compile(r'(\d+\.?\d*)\s*%')


def normalize_dose(text):
    def repl(m):
        num = m.group(1)
        unit = UNIT_MAP.get(m.group(2).upper(), m.group(2).lower())
        out = f"{num}{unit}"
        if m.group(3):
            unit2 = UNIT_MAP.get(m.group(3).upper(), m.group(3).lower())
            out += f"/{unit2}"
        return out
    text = DOSE_RE.sub(repl, text)
    text = PCT_RE.sub(lambda m: f"{m.group(1)}%", text)
    return text


# ---------- 2. dạng bào chế EN (tail) -> VN ----------
FORM_VN = {
    # uống - viên
    "Extended Release Oral Tablet": "viên nén phóng thích kéo dài",
    "Delayed Release Oral Tablet": "viên nén bao tan trong ruột",
    "Disintegrating Oral Tablet": "viên nén phân rã nhanh",
    "Effervescent Oral Tablet": "viên sủi",
    "Chewable Tablet": "viên nhai",
    "Sublingual Tablet": "viên ngậm dưới lưỡi",
    "Oral Tablet": "viên nén",
    "Extended Release Oral Capsule": "viên nang phóng thích kéo dài",
    "Delayed Release Oral Capsule": "viên nang bao tan trong ruột",
    "Oral Capsule": "viên nang",
    "Oral Lozenge": "viên ngậm",
    "Oral Pellet": "hạt uống",
    # uống - lỏng/bột
    "Powder for Oral Solution": "bột pha dung dịch uống",
    "Powder for Oral Suspension": "bột pha hỗn dịch uống",
    "Granules for Oral Suspension": "cốm pha hỗn dịch uống",
    "Tablet for Oral Suspension": "viên nén pha hỗn dịch uống",
    "Oral Solution": "dung dịch uống",
    "Oral Suspension": "hỗn dịch uống",
    "Oral Powder": "bột uống",
    "Oral Granules": "cốm uống",
    "Oral Gel": "gel uống",
    # tiêm
    "Injectable Solution": "dung dịch tiêm",
    "Injectable Suspension": "hỗn dịch tiêm",
    "Injection": "dung dịch tiêm",
    "Prefilled Syringe": "bơm tiêm đóng sẵn",
    "Auto-Injector": "bút tiêm tự động",
    "Pen Injector": "bút tiêm",
    "Cartridge": "ống nạp thuốc tiêm",
    # bôi ngoài da
    "Topical Cream": "kem bôi",
    "Topical Ointment": "thuốc mỡ bôi",
    "Topical Gel": "gel bôi",
    "Topical Lotion": "dung dịch bôi",
    "Topical Solution": "dung dịch bôi",
    "Topical Spray": "xịt ngoài da",
    "Topical Foam": "bọt bôi",
    "Topical Powder": "bột bôi",
    "Topical Oil": "dầu bôi",
    "Medicated Patch": "miếng dán thuốc",
    "Medicated Pad": "miếng thấm thuốc",
    "Medicated Shampoo": "dầu gội thuốc",
    "Medicated Liquid Soap": "xà phòng lỏng thuốc",
    "Transdermal System": "miếng dán qua da",
    # trực tràng / âm đạo
    "Rectal Cream": "kem đặt trực tràng",
    "Rectal Suppository": "viên đặt hậu môn",
    # mắt / tai / mũi
    "Ophthalmic Solution": "dung dịch nhỏ mắt",
    "Ophthalmic Suspension": "hỗn dịch nhỏ mắt",
    "Ophthalmic Ointment": "thuốc mỡ tra mắt",
    "Otic Solution": "dung dịch nhỏ tai",
    "Nasal Spray": "xịt mũi",
    "Metered Dose Nasal Spray": "xịt mũi định liều",
    "Mucosal Spray": "xịt niêm mạc",
    # hít
    "Inhalation Solution": "dung dịch khí dung",
    "Inhalation Powder": "bột hít",
    "Gas for Inhalation": "khí hít",
    "Dry Powder Inhaler": "bình hít bột khô",
    "Metered Dose Inhaler": "bình hít định liều",
    # khác
    "Drug Implant": "que cấy thuốc",
    "Mouthwash": "nước súc miệng",
    "Toothpaste": "kem đánh răng",
    # bổ sung (đo từ dữ liệu thực SCD/SBD)
    "Oral Paste": "dạng sệt uống",
    "Paste": "dạng sệt",
    "Medicated Bar Soap": "xà phòng bánh thuốc",
    "Vaginal Cream": "kem đặt âm đạo",
    "Vaginal Gel": "gel đặt âm đạo",
    "Vaginal Ointment": "thuốc mỡ đặt âm đạo",
    "Vaginal Insert": "viên đặt âm đạo",
    "Vaginal System": "vòng đặt âm đạo",
    "Buccal Film": "phim dán niêm mạc má",
    "Buccal Tablet": "viên ngậm má",
    "Sustained Release Buccal Tablet": "viên ngậm má phóng thích kéo dài",
    "Sublingual Film": "phim ngậm dưới lưỡi",
    "Oral Film": "phim tan trong miệng",
    "Oral Wafer": "màng uống",
    "Ophthalmic Gel": "gel tra mắt",
    "Ophthalmic Irrigation Solution": "dung dịch rửa mắt",
    "Otic Suspension": "hỗn dịch nhỏ tai",
    "Otic Gel": "gel nhỏ tai",
    "Nasal Solution": "dung dịch xịt mũi",
    "Powder for Nasal Solution": "bột pha dung dịch xịt mũi",
    "Nasal Gel": "gel mũi",
    "Nasal Powder": "bột xịt mũi",
    "Nasal Inhalant": "dạng hít mũi",
    "Inhalation Spray": "xịt hít",
    "Inhalation Suspension": "hỗn dịch khí dung",
    "Enema": "thuốc thụt",
    "Douche": "dung dịch thụt rửa",
    "Irrigation Solution": "dung dịch rửa",
    "Intraperitoneal Solution": "dung dịch ổ bụng",
    "Intravesical Solution": "dung dịch bàng quang",
    "Powder for Intravesical Solution": "bột pha dung dịch bàng quang",
    "Powder for Intravesical Suspension": "bột pha hỗn dịch bàng quang",
    "Intratracheal Suspension": "hỗn dịch khí quản",
    "Extended Release Suspension": "hỗn dịch phóng thích kéo dài",
    "Powder Spray": "bột xịt",
    "Oral Spray": "xịt uống",
    "Oral Foam": "bọt uống",
    "Oral Cream": "kem uống",
    "Rectal Ointment": "thuốc mỡ đặt trực tràng",
    "Rectal Gel": "gel đặt trực tràng",
    "Rectal Spray": "xịt trực tràng",
    "Rectal Foam": "bọt đặt trực tràng",
    "Urethral Suppository": "viên đặt niệu đạo",
    "Intrauterine System": "vòng đặt tử cung",
    "Chewing Gum": "kẹo cao su thuốc",
    "Topical Suspension": "hỗn dịch bôi",
    "Medicated Tape": "băng dán thuốc",
    "Jet Injector": "bơm tiêm áp lực",
    "Oral Ointment": "thuốc mỡ uống",
    "Otic Ointment": "thuốc mỡ nhỏ tai",
    "Ophthalmic Cream": "kem tra mắt",
    "Ophthalmic Spray": "xịt mắt",
    "Nasal Ointment": "thuốc mỡ mũi",
    "Intravesical Suspension": "hỗn dịch bàng quang",
    "Pyelocalyceal Solution": "dung dịch bể thận-đài thận",
    "Liquefied Gas": "khí hóa lỏng",
    "Sublingual Powder": "bột ngậm dưới lưỡi",
    "Vaginal Foam": "bọt đặt âm đạo",
    "Vaginal Film": "phim đặt âm đạo",
    "Injectable Foam": "bọt tiêm",
    "Oral Flakes": "vảy uống",
    "Rectal Solution": "dung dịch đặt trực tràng",
}
FORM_TAILS_SORTED = sorted(FORM_VN.keys(), key=len, reverse=True)

# thứ tự ưu tiên chọn RxCUI mặc định khi 1 khóa (tên+liều) có nhiều form
FORM_PRIORITY = [
    "Oral Tablet", "Oral Capsule", "Extended Release Oral Tablet",
    "Delayed Release Oral Tablet", "Chewable Tablet", "Disintegrating Oral Tablet",
    "Effervescent Oral Tablet", "Sublingual Tablet",
    "Extended Release Oral Capsule", "Delayed Release Oral Capsule",
    "Oral Solution", "Oral Suspension", "Oral Powder", "Oral Granules",
    "Oral Gel", "Oral Pellet", "Oral Lozenge",
    "Injection", "Injectable Solution", "Injectable Suspension",
    "Prefilled Syringe", "Auto-Injector", "Pen Injector", "Cartridge",
]


def form_rank(tail):
    try:
        return FORM_PRIORITY.index(tail)
    except ValueError:
        return 999


BRACKET_RE = re.compile(r'\s*(\[[^\]]*\])\s*$')


def split_form(raw_text):
    """Tách (base, form_tail_en, bracket) từ tên SCD/SBD. Trả (None, None, bracket) nếu
    không nhận diện được form (fallback: giữ nguyên toàn bộ text)."""
    m = BRACKET_RE.search(raw_text)
    bracket = ""
    core = raw_text
    if m:
        bracket = " " + m.group(1)
        core = raw_text[:m.start()]
    for tail in FORM_TAILS_SORTED:
        if core.endswith(tail):
            base = core[:-len(tail)].strip()
            return base, tail, bracket
    return None, None, bracket


def load_cache(tty):
    fp = os.path.join(CACHE_DIR, f"rxnorm_{tty}.json")
    if not os.path.exists(fp):
        print(f"THIẾU CACHE: {fp} — chạy build_rxnorm_lib.py trước.", file=sys.stderr)
        sys.exit(1)
    with open(fp, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("minConceptGroup", {}).get("minConcept", [])


def add_entry(d, text, code):
    """Thêm text(lowercase)->code vào dict d; nếu text đã tồn tại với code KHÁC,
    nối bằng dấu phẩy (value luôn là string thuần, không mảng)."""
    key = text.lower()
    if key in d:
        codes = d[key].split(",")
        if code not in codes:
            d[key] = d[key] + "," + code
    else:
        d[key] = code


def load_rrf_concepts(ttys):
    """Đọc rich_data/RXNCONSO.RRF, trả list (tty, name, rxcui) cho các TTY cần.
    Trả [] nếu không có file (rich_data tùy chọn)."""
    if not os.path.exists(RRF):
        return []
    want = set(ttys)
    out = []
    with open(RRF, encoding="utf-8") as f:
        for line in f:
            p = line.split("|")
            if len(p) < 15 or p[12] not in want:
                continue
            name = p[14].strip()
            rxcui = p[0].strip()
            if name and rxcui:
                out.append((p[12], name, rxcui))
    return out


def main():
    # ---- tên trần: IN / PIN / BN (thường không có liều, normalize_dose vô hại) ----
    out_drugs = {}
    for tty in ["IN", "PIN", "BN"]:
        for c in load_cache(tty):
            name = normalize_dose((c.get("name") or "").strip())
            rxcui = (c.get("rxcui") or "").strip()
            if not name or not rxcui:
                continue
            add_entry(out_drugs, name, rxcui)
    n_plain = len(out_drugs)

    # ---- thuốc có liều: SCD / SBD -> tách base (bỏ form) + form ----
    dosed = {}   # base_key -> list[(form_tail_en, rxcui)]
    unmapped = []
    for tty in ["SCD", "SBD"]:
        for c in load_cache(tty):
            raw = (c.get("name") or "").strip()
            rxcui = (c.get("rxcui") or "").strip()
            if not raw or not rxcui:
                continue
            base, tail, bracket = split_form(raw)
            if base is None:
                unmapped.append(raw)
                continue
            base_key = (normalize_dose(base) + bracket).strip()
            dosed.setdefault(base_key, []).append((tail, rxcui))

    # drugs.json: mỗi base_key -> 1 entry, RxCUI của form ưu tiên nhất
    for base_key, variants in dosed.items():
        variants.sort(key=lambda v: form_rank(v[0]))
        default_rxcui = variants[0][1]
        add_entry(out_drugs, base_key, default_rxcui)
    n_dosed_default = len(out_drugs) - n_plain

    # drug_forms.json = SUPERSET: toàn bộ drugs.json (tên trần + tên+liều mặc định)
    # + mọi (base_key + form_vn) -> RxCUI đúng form. Dùng làm gazetteer CHÍNH.
    # Bản EN-trần ("...400mg") và bản có dạng bào chế VN ("...400mg viên nén") là 2
    # KEY khác nhau -> cả 2 đều giữ (không còn là "trùng lặp" như format list cũ,
    # chỉ là 2 cách viết hợp lệ trỏ cùng 1 mã, có lợi cho retrieval).
    # 2 form EN khác nhau dịch trùng 1 chuỗi VN (vd Injection/Injectable Solution
    # đều -> "dung dịch tiêm") -> add_entry tự nối mã bằng dấu phẩy, không cần xử lý riêng.
    out_forms = dict(out_drugs)
    n_forms_base = len(out_forms)

    for base_key, variants in dosed.items():
        for tail, rxcui in variants:
            vn = FORM_VN[tail]
            text = f"{base_key} {vn}"
            add_entry(out_forms, text, rxcui)

    # ---- BỔ SUNG từ RRF (rich_data): các nhóm còn thiếu, CHỈ ghi vào drug_forms ----
    #   MIN  = thuốc phối hợp KHÔNG liều  ("acetaminophen / hydrocodone")
    #   SCDF = dạng bào chế generic KHÔNG liều ("acetaminophen Oral Tablet")
    #   SBDF = dạng bào chế + brand KHÔNG liều ("lorazepam Oral Tablet [Ativan]")
    # SCDF/SBDF: dịch form -> VN (như SCD/SBD) + giữ cả bản EN gốc. MIN: thêm thẳng.
    n_min = n_scdf = n_sbdf = n_rrf_unmapped = 0
    for tty, name, rxcui in load_rrf_concepts(["MIN", "SCDF", "SBDF"]):
        if tty == "MIN":
            add_entry(out_forms, normalize_dose(name), rxcui)
            n_min += 1
            continue
        base, tail, bracket = split_form(name)
        if base is None:
            # form không nhận diện -> giữ nguyên bản EN để không mất dữ liệu
            add_entry(out_forms, normalize_dose(name), rxcui)
            n_rrf_unmapped += 1
            continue
        base_key = (normalize_dose(base) + bracket).strip()   # gồm cả [brand] nếu có
        add_entry(out_forms, normalize_dose(name), rxcui)           # bản EN gốc
        add_entry(out_forms, f"{base_key} {FORM_VN[tail]}", rxcui)  # bản VN ([brand] trước form, như bactrim)
        if tty == "SCDF":
            n_scdf += 1
        else:
            n_sbdf += 1

    os.makedirs(os.path.dirname(OUT_DRUGS), exist_ok=True)
    with open(OUT_DRUGS, "w", encoding="utf-8") as f:
        json.dump(out_drugs, f, ensure_ascii=False, indent=2)
    with open(OUT_FORMS, "w", encoding="utf-8") as f:
        json.dump(out_forms, f, ensure_ascii=False, indent=2)

    print(f"drugs.json: {len(out_drugs)} key ({n_plain} tên trần + {n_dosed_default} tên+liều mặc định) -> {OUT_DRUGS}")
    print(f"drug_forms.json (superset): {len(out_forms)} key ({n_forms_base} kế thừa từ drugs.json + {len(out_forms) - n_forms_base} biến thể form/RRF) -> {OUT_FORMS}")
    print(f"  bổ sung RRF: MIN(phối hợp)={n_min}  SCDF={n_scdf}  SBDF(brand-form)={n_sbdf}  form-lạ giữ EN={n_rrf_unmapped}")
    print(f"khóa tên+liều (dosed) duy nhất: {len(dosed)}")
    print(f"SCD/SBD KHÔNG nhận diện được form (fallback bỏ qua): {len(unmapped)}")
    if unmapped:
        uniq_tails = {}
        for u in unmapped[:200]:
            words = u.split()
            tail_guess = " ".join(words[-2:]) if len(words) >= 2 else u
            uniq_tails[tail_guess] = uniq_tails.get(tail_guess, 0) + 1
        print("  mẫu chưa nhận diện (2 từ cuối, top 15):")
        for t, c in sorted(uniq_tails.items(), key=lambda x: -x[1])[:15]:
            print(f"    {c:4}  ...{t}")


if __name__ == "__main__":
    main()
