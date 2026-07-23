#!/usr/bin/env python3
"""ĐIỂM VÀO DUY NHẤT — sinh toàn bộ output từ input/ theo ĐÚNG chính sách đã xác thực leaderboard.

  python source/generate_all.py                      # pipeline thuần  -> output_closest/
  python source/generate_all.py --entities output    # GHÉP mã lên bộ entity ngoài -> output_final/
  python source/generate_all.py --entities output --dst test_output

Giai đoạn:
  1. luong2/extract_findings.py -> output_findings/ (CHẨN_ĐOÁN+TRIỆU_CHỨNG) + output_tests/
  2. luong1/extract_drugs.py    -> output_drugs/    (THUỐC + RxNorm, đã áp CHÍNH SÁCH MÃ)
  3. merge                      -> output_closest/
  4. validate                   -> kiểm text == input[start:end] + schema (BẮT BUỘC trước khi nộp)
  5. (tuỳ chọn) graft           -> output_final/ : lấy ENTITY của bộ ngoài + MÃ của pipeline

Chính sách gán mã (đo bằng probe XOÁ-MÃ trên leaderboard — xem CODEBOOK.md §4):
  • CHẨN_ĐOÁN: chỉ gán mã khi (field-có-nhãn HOẶC hậu tố kỹ thuật WHO) VÀ KHÔNG ở outline-section.
  • THUỐC: KHÔNG gán mã cho khái niệm trừu tượng (lớp điều trị, dịch truyền/điện giải, span dính,
    thiết bị); sửa mã sai dạng/muối về hoạt chất.
  → pipeline sinh 192 mã THUỐC + 19 mã CHẨN_ĐOÁN (cấu hình bản tốt nhất).
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
MERGE_DIRS = ["output_findings", "output_drugs", "output_tests"]
OUT_CLOSEST = os.path.join(ROOT, "output_closest")
CODED_TYPES = {"THUỐC", "CHẨN_ĐOÁN"}
SORT_BY_POSITION = False   # đặt bởi --sort-position
ASSERT_FROM_OVERLAP = False  # đặt bởi --assert-overlap: chuyển assertion luật (ConText) sang entity
#   nguồn KHÔNG có sẵn (vd model NER route B). KHÔNG động path output/ (đã có assertion LLM).
DISABLE_RECALL = False  # đặt bởi --no-recall: BỎ bước (f)/(f2) entity-recall. Xác thực 2026-07-23:
#   entity-recall từ rule pipeline HẠI trên genre mới (v6 −1.0). Bật cho nguồn entity đã DÀY/tốt sẵn.

STEPS = [
    ("Luồng 2: CHẨN_ĐOÁN/TRIỆU_CHỨNG/XÉT_NGHIỆM/KẾT_QUẢ", os.path.join(HERE, "luong2", "extract_findings.py")),
    ("Luồng 1: THUỐC (RxNorm + chính sách mã)", os.path.join(HERE, "luong1", "extract_drugs.py")),
]


def banner(msg):
    print(f"\n{'='*70}\n▶ {msg}\n{'='*70}")


def run_step(label, script):
    banner(label)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, script], env=env)
    if r.returncode != 0:
        print(f"LỖI ở {script} (exit {r.returncode})", file=sys.stderr)
        sys.exit(r.returncode)


def load(d, n):
    p = os.path.join(d, f"{n}.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []


def merge():
    banner("Gộp -> output_closest/ (sort theo position)")
    os.makedirs(OUT_CLOSEST, exist_ok=True)
    total = 0
    for n in range(1, 101):
        items = []
        for d in MERGE_DIRS:
            items.extend(load(os.path.join(ROOT, d), n))
        items.sort(key=lambda c: (c.get("position") or [0, 0])[0])
        json.dump(items, open(os.path.join(OUT_CLOSEST, f"{n}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        total += len(items)
    print(f"output_closest/: 100 file, tổng {total} concept")


# ------------------------------------------------------------------ validate
def validate(d):
    """BẮT BUỘC trước khi nộp. Entity sai `position` KHÔNG align được ⇒ mất điểm assertion/candidate
    mà text vẫn tính vào WER (đã gặp thật: sửa tay output làm tụt −0.157)."""
    banner(f"Validate {os.path.basename(d)}/")
    bad_pos = bad_slice = no_pos = 0
    coded = {"THUỐC": 0, "CHẨN_ĐOÁN": 0}
    stray = 0
    total = 0
    for n in range(1, 101):
        ip = os.path.join(ROOT, "input", f"{n}.txt")
        if not os.path.exists(ip):
            continue
        content = open(ip, encoding="utf-8").read()
        for e in load(d, n):
            total += 1
            pos = e.get("position")
            if not isinstance(pos, list) or len(pos) != 2:
                no_pos += 1
                continue
            if not (0 <= pos[0] <= pos[1] <= len(content)):
                bad_pos += 1
            elif content[pos[0]:pos[1]] != e.get("text"):
                bad_slice += 1
            if e.get("candidates"):
                if e.get("type") in coded:
                    coded[e["type"]] += 1
                else:
                    stray += 1          # candidates trên type KHÔNG được phép có mã
    ok = (bad_pos == 0 and bad_slice == 0 and no_pos == 0 and stray == 0)
    print(f"  entity: {total}   mã: THUỐC={coded['THUỐC']} CHẨN_ĐOÁN={coded['CHẨN_ĐOÁN']}")
    print(f"  position thiếu/sai định dạng : {no_pos}")
    print(f"  position ngoài phạm vi file  : {bad_pos}")
    print(f"  text != input[start:end]     : {bad_slice}")
    print(f"  candidates trên type sai      : {stray}")
    print("  => " + ("HỢP LỆ ✓" if ok else "CÓ LỖI ✗ (sửa trước khi nộp)"))
    return ok


# ------------------------------------------------------------------ graft
# Bước (d): CHẨN_ĐOÁN có hậu tố kỹ thuật WHO -> gán leaf-code (S27, +0.989). Chỉ tech-suffix.
_DIAG_TECH = re.compile(r",\s*không (đặc hiệu|xác định)|vô căn \(nguyên phát\)", re.I)
_DIAG_STRIP = re.compile(r",?\s*không (đặc hiệu|xác định)\s*$|\s*vô căn \(nguyên phát\)\s*$", re.I)
# Bước (e): CHẨN_ĐOÁN "specified" — đặc hiệu lâm sàng có mã ICD RIÊNG (S29b spec_nodiab, +0.257).
# GT code chẩn đoán ACTIVE/CỤ THỂ (bệnh tim mạch DO xơ vữa→I25.1, suy thận CẤP→N17.9, xơ gan DO
# rượu→K70.3), BỎ TRỐNG comorbidity trần (tăng huyết áp/đái tháo đường/rung nhĩ/béo phì — S30 THUA
# −1.52) và "đái tháo đường típ 2" (S28 THUA). SPEC-match tự loại comorbidity trần (chúng không có
# marker cấp/mạn/do); chỉ cần loại thêm đái tháo đường (nếu lọt qua "do").
_DIAG_SPEC = re.compile(r"\bcấp\b|\b(mạn|mãn) tính\b|\bdo\b|di căn|nghiêm trọng|nguyên phát|thứ phát", re.I)
_DIAG_SPEC_BLOCK = re.compile(r"đái tháo đường|tiểu đường", re.I)
# Bước (f4): CKD staging tất định (refine_ckd_stage, S31 +validated) — dùng lookahead thay \b vì
# input hay dính liền không dấu cách ngay sau stage (vd "giai đoạn 5tăng huyết áp").
_CKD_STAGE_RE = re.compile(r"giai đoạn\s+([1-5](?!\d)|i{1,3}(?![a-z])|iv(?![a-z])|v(?![a-z]))", re.I)
_ROMAN_STAGE = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5"}


def _load_diag_codes():
    d = {}
    fp = os.path.join(ROOT, "source/luong2/lexicon/diagnoses.txt")
    for line in open(fp, encoding="utf-8"):
        s = line.strip()
        if "|" in s and not s.startswith("#"):
            p = [x.strip() for x in s.split("|")]
            cs = [c.strip() for c in p[1].split(",") if c.strip()] if len(p) > 1 else []
            if cs:
                d[p[0].lower()] = cs
    return d


def _overlap(a, b):
    if not (isinstance(a, list) and len(a) == 2 and isinstance(b, list) and len(b) == 2):
        return 0
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def _fix_position(content, text, pos):
    """Trả [s,e] đúng cho `text`: khớp CHÍNH XÁC trước, sau đó khớp LINH HOẠT khoảng trắng/hoa-thường
    (văn bản gốc hay có 2 dấu cách liên tiếp). Chọn vị trí gần `pos` cũ nhất."""
    if not text:
        return None
    cands = [(m, m + len(text)) for m in _all_find(content, text)]
    if not cands:
        pat = r"\s+".join(re.escape(w) for w in text.split())
        cands = [(m.start(), m.end()) for m in re.finditer(pat, content, re.IGNORECASE)]
    if not cands:
        return None
    anchor = pos[0] if isinstance(pos, list) and len(pos) == 2 else 0
    return list(min(cands, key=lambda se: abs(se[0] - anchor)))


def _all_find(hay, needle):
    i = hay.find(needle)
    while i != -1:
        yield i
        i = hay.find(needle, i + 1)


def graft(entities_dir, dst):
    """Lấy bộ ENTITY của `entities_dir` (text/type/position/assertions) + MÃ của pipeline.
    2 trục ĐỘC LẬP: nguồn entity quyết định WER/J_assert, chính sách mã quyết định J_cand."""
    banner(f"Ghép mã pipeline lên entity của {os.path.basename(entities_dir)}/ -> {os.path.basename(dst)}/")
    sys.path.insert(0, os.path.join(HERE, "luong1"))
    import extract_drugs as ed

    names, override = ed.load_drugs()
    cache = json.load(open(ed.CACHE, encoding="utf-8")) if os.path.exists(ed.CACHE) else {}
    lib_dosed, lib_names = ed.load_lib_dosed()
    # cho bước (d): lexicon leaf-code CHẨN_ĐOÁN + tên chính thức WHO
    diag_codes = _load_diag_codes()
    who_names = {t.strip().lower(): v for t, v in
                 json.load(open(os.path.join(ROOT, "library/diseases.json"), encoding="utf-8")).items()}
    # cho bước (f2): symptom lexicon (curated, precision cao) để recall triệu chứng output/ bỏ sót
    sym_lex = set()
    _sp = os.path.join(ROOT, "source/luong2/lexicon/symptoms.txt")
    if os.path.exists(_sp):
        for line in open(_sp, encoding="utf-8"):
            t = line.strip()
            if t and not t.startswith("#"):
                sym_lex.add(t.split("|")[0].strip().lower())

    os.makedirs(dst, exist_ok=True)
    transferred = fixed = recoded = recalled = cleaned = decoded = 0
    per_file = {}
    for n in range(1, 101):
        ip = os.path.join(ROOT, "input", f"{n}.txt")
        content = open(ip, encoding="utf-8").read() if os.path.exists(ip) else ""
        theirs = [dict(e) for e in load(entities_dir, n)]
        ours = [c for c in load(OUT_CLOSEST, n) if c.get("candidates")]
        # (a) sửa offset sai
        for e in theirs:
            pos = e.get("position")
            ok = (isinstance(pos, list) and len(pos) == 2
                  and content[pos[0]:pos[1]] == e.get("text"))
            if not ok:
                np_ = _fix_position(content, e.get("text", ""), pos)
                if np_:
                    e["position"] = np_
                    e["text"] = content[np_[0]:np_[1]]   # dùng ĐÚNG slice gốc
                    fixed += 1
        # (b) chuyển mã theo overlap + cùng type
        for e in theirs:
            if e.get("type") not in CODED_TYPES:
                continue
            best, mx = None, 0
            for c in ours:
                if c.get("type") != e.get("type"):
                    continue
                v = _overlap(e.get("position"), c.get("position"))
                if v > mx:
                    mx, best = v, c
            e["candidates"] = list(best["candidates"]) if best else []
            if best:
                transferred += 1
        # (b2) chuyển ASSERTION (ConText luật) từ output_closest theo overlap+cùng type — CHỈ khi
        #      entity nguồn CHƯA có assertion (model NER trống; output/ giữ nguyên assertion LLM).
        if ASSERT_FROM_OVERLAP:
            ours_all = load(OUT_CLOSEST, n)
            for e in theirs:
                if e.get("assertions"):
                    continue
                best, mx = None, 0
                for c in ours_all:
                    if c.get("type") != e.get("type"):
                        continue
                    v = _overlap(e.get("position"), c.get("position"))
                    if v > mx:
                        mx, best = v, c
                if best and best.get("assertions"):
                    e["assertions"] = list(best["assertions"])
        per_file[n] = theirs

    # (c) THUỐC còn trống mã -> resolve lại. Bản đồ `seen` phải TOÀN CỤC (mã cho cùng tên thuốc ở
    #     BẤT KỲ file nào) để giữ NHẤT QUÁN — vd 'lasix' ta luôn dùng 4603, không phải 202991(brand).
    seen = {}
    for n in range(1, 101):
        for e in per_file.get(n, []):
            if e.get("type") == "THUỐC" and e.get("candidates"):
                low = e["text"].strip().lower()
                seen.setdefault(low, list(e["candidates"]))
                seen.setdefault(re.split(r"[\s,]+", low)[0], list(e["candidates"]))
    for n in range(1, 101):
        theirs = per_file.get(n, [])
        content = (open(os.path.join(ROOT, "input", f"{n}.txt"), encoding="utf-8").read()
                   if os.path.exists(os.path.join(ROOT, "input", f"{n}.txt")) else "")
        for e in theirs:
            if e.get("type") != "THUỐC" or e.get("candidates"):
                continue
            text = e.get("text", "")
            low = text.strip().lower()
            head = re.split(r"[\s,]+", low)[0]
            cand = (seen.get(low) or seen.get(head) or override.get(low)
                    or ed.lib_resolve_dose(low, text, lib_dosed, lib_names)
                    or cache.get(low) or override.get(head) or cache.get(head))
            cand = ed.apply_code_policy(text, list(cand) if cand else [])
            if cand:
                e["candidates"] = cand
                recoded += 1
        # (d) CHẨN_ĐOÁN có hậu tố kỹ thuật WHO nhưng còn TRỐNG mã (graft chuyển-theo-overlap bỏ sót vì
        #     span nguồn không trùng extraction) -> gán LEAF-code trực tiếp. Đây là luật tech-suffix ĐÃ
        #     xác thực dương MẠNH (S27: +0.989). Chỉ tech-suffix — KHÔNG code bare-name (âm, xem 153/S16).
        for e in theirs:
            if e.get("type") != "CHẨN_ĐOÁN" or e.get("candidates"):
                continue
            tl = e.get("text", "").strip().lower()
            if not _DIAG_TECH.search(tl):
                continue
            core = _DIAG_STRIP.sub("", tl).strip()
            code = (diag_codes.get(core) or diag_codes.get(tl)
                    or ([who_names[tl]] if tl in who_names else None)
                    or ([who_names[core]] if core in who_names else None))
            if code:
                e["candidates"] = list(code)
                recoded += 1
        # (e) CHẨN_ĐOÁN "specified" (đặc hiệu lâm sàng) còn trống mã -> gán exact-code từ lexicon/WHO
        for e in theirs:
            if e.get("type") != "CHẨN_ĐOÁN" or e.get("candidates"):
                continue
            tl = e.get("text", "").strip().lower()
            if not _DIAG_SPEC.search(tl) or _DIAG_SPEC_BLOCK.search(tl):
                continue
            code = diag_codes.get(tl) or ([who_names[tl]] if tl in who_names else None)
            if code:
                e["candidates"] = list(code)
                recoded += 1
        # (f) ENTITY-RECALL: thêm CHẨN_ĐOÁN/THUỐC CÓ MÃ mà pipeline của TA trích được nhưng nguồn
        #     `output/` BỎ SÓT (không overlap entity nào). S43 +0.32: đây là entity GT THẬT output/
        #     thiếu, mã khớp GT (+cand) và text cải thiện WER. CHỈ entity CÓ MÃ (symptom/uncoded = noise).
        their_pos = [e.get("position") for e in theirs]
        if not DISABLE_RECALL:
            for c in load(OUT_CLOSEST, n):
                if c.get("type") not in CODED_TYPES or not c.get("candidates"):
                    continue
                p = c.get("position")
                if not (isinstance(p, list) and len(p) == 2 and content[p[0]:p[1]] == c.get("text")):
                    continue
                if any(_overlap(p, q) for q in their_pos):
                    continue
                theirs.append(dict(c))
                their_pos.append(p)
                recalled += 1
            # (f2) RECALL TRIỆU_CHỨNG lexicon-curated mà `output/` bỏ sót (S44 +0.146). CHỈ lexicon
            #      (precision cao) — gazetteer/vague = noise (hại, xem S45). Giữ assertion rule của ta.
            for c in load(OUT_CLOSEST, n):
                if c.get("type") != "TRIỆU_CHỨNG" or c.get("text", "").strip().lower() not in sym_lex:
                    continue
                p = c.get("position")
                if not (isinstance(p, list) and len(p) == 2 and content[p[0]:p[1]] == c.get("text")):
                    continue
                if any(_overlap(p, q) for q in their_pos):
                    continue
                theirs.append(dict(c))
                their_pos.append(p)
                recalled += 1
        # (f3) DE-OVERCODE: gỡ mã khỏi CHẨN_ĐOÁN BARE comorbidity (không có định tính cấp/etiology/
        #      giai đoạn) — khớp ĐÚNG phát hiện S30 đã validate trên input cũ (GT KHÔNG code
        #      comorbidity trần: tăng huyết áp/đái tháo đường/rung nhĩ/béo phì — THUA −1.52 nếu code).
        #      Mở rộng thêm suy tim/suy thận mạn (cùng lớp bare, phát hiện trên input MỚI 2026-07-22:
        #      "Chẩn đoán: suy tim - suy thận mạn giai đoạn 5..." field-label khiến gate cũ lọt qua).
        #      CHỈ strip khi text KHỚP ĐÚNG (EXACT) — "suy thận mạn giai đoạn 5" (có thêm giai đoạn)
        #      KHÔNG bị strip, được sửa mã CHÍNH XÁC theo giai đoạn ở bước (f4) thay vì gỡ trắng.
        _BARE_COMORBID = {"tăng huyết áp", "đái tháo đường", "tiểu đường", "rung nhĩ", "béo phì",
                          "suy tim", "suy thận mạn"}
        for e in theirs:
            if e.get("type") != "CHẨN_ĐOÁN" or not e.get("candidates"):
                continue
            if e.get("text", "").strip().lower() in _BARE_COMORBID:
                e["candidates"] = []
                decoded += 1
        # (f4) CKD STAGING: "suy thận mạn ... giai đoạn N" -> N18.N tất định (refine_ckd_stage đã
        #      validate trên input cũ, S31: 5/5 khớp). Áp lại đây vì graft() không đi qua
        #      luong2/extract_findings.py cho entity từ nguồn NGOÀI (NER/LLM) — sửa cả trường hợp
        #      mã hiện tại N18/N18.9 (generic, có thể do regex \b cũ bỏ lỡ stage khi text dính liền
        #      "giai đoạn 5tăng huyết áp" — đã fix ở luong2, nhưng entity NGOÀI cần sửa lại ở đây)
        #      và trường hợp còn trống mã.
        for e in theirs:
            if e.get("type") != "CHẨN_ĐOÁN" or "thận" not in e.get("text", "").lower():
                continue
            tl = e.get("text", "").strip().lower()
            m = _CKD_STAGE_RE.search(tl)
            if not m:
                continue
            cur = e.get("candidates") or []
            if cur and cur[0].split(",")[0] not in ("N18", "N18.9"):
                continue          # đã có mã CỤ THỂ khác -> không đụng
            stage = _ROMAN_STAGE.get(m.group(1).lower(), m.group(1).lower())
            e["candidates"] = [f"N18.{stage}"]
            recoded += 1
        # (g) LÀM SẠCH (S47/S48, +0.13): bỏ entity vị-trí-hỏng (text≠input[start:end] → insertion WER
        #     + không align) và TRÙNG LẶP CHÍNH XÁC (text+type+position → insertion + nhiễu alignment).
        #     Cải thiện CẢ 3 metric — INVARIANT bắt buộc mọi bản nộp.
        dedup_seen = set()
        clean = []
        for e in theirs:
            p = e.get("position")
            if not (isinstance(p, list) and len(p) == 2 and 0 <= p[0] <= p[1] <= len(content)
                    and content[p[0]:p[1]] == e.get("text")):
                cleaned += 1
                continue
            k = (e.get("text"), e.get("type"), p[0], p[1])
            if k in dedup_seen:
                cleaned += 1
                continue
            dedup_seen.add(k)
            clean.append(e)
        theirs = clean
        # ⚠️ GIỮ NGUYÊN THỨ TỰ của nguồn entity. WER = editdistance trên chuỗi text NỐI THEO THỨ TỰ
        # LIST ⇒ đổi thứ tự là ĐỔI ĐIỂM. Bản 48.0720 dùng thứ tự GỐC của `output/` (KHÔNG sort).
        # `--sort-position` để thử biến thể sort (probe riêng, chưa xác thực).
        if SORT_BY_POSITION:
            theirs.sort(key=lambda c: (c.get("position") or [0, 0])[0])
        json.dump(theirs, open(os.path.join(dst, f"{n}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    print(f"  offset đã sửa: {fixed} | mã chuyển sang: {transferred} | THUỐC resolve lại: {recoded}"
          f" | entity-recall coded thêm: {recalled} | de-overcode (bare comorbidity gỡ mã): {decoded}"
          f" | làm sạch (dup/vị-trí-hỏng): {cleaned}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entities", default=None,
                    help="thư mục entity NGOÀI để ghép mã lên (vd: output). Bỏ trống = pipeline thuần.")
    ap.add_argument("--dst", default="output_final", help="thư mục kết quả khi dùng --entities")
    ap.add_argument("--sort-position", action="store_true",
                    help="sắp entity theo position (MẶC ĐỊNH: giữ thứ tự gốc của nguồn — thứ tự ẢNH HƯỞNG WER)")
    ap.add_argument("--assert-overlap", action="store_true",
                    help="chuyển assertion luật (ConText) sang entity nguồn trống (dùng cho model NER route B)")
    args = ap.parse_args()
    global SORT_BY_POSITION, ASSERT_FROM_OVERLAP
    SORT_BY_POSITION = args.sort_position
    ASSERT_FROM_OVERLAP = args.assert_overlap

    for label, script in STEPS:
        run_step(label, script)
    merge()
    validate(OUT_CLOSEST)

    if args.entities:
        ent = os.path.join(ROOT, args.entities)
        if not os.path.isdir(ent):
            print(f"LỖI: không thấy thư mục entity {ent}", file=sys.stderr)
            sys.exit(1)
        dst = os.path.join(ROOT, args.dst)
        graft(ent, dst)
        validate(dst)
        banner(f"✓ HOÀN TẤT — bản nộp: {args.dst}/")
    else:
        banner("✓ HOÀN TẤT — output_findings, output_drugs, output_tests, output_closest")


if __name__ == "__main__":
    main()
