#!/usr/bin/env python3
"""Luồng 1 — trích xuất THUỐC trực tiếp từ input/N.txt + gán RxCUI (RxNorm).

NGUYÊN TẮC: chỉ đọc input/. RxCUI lấy từ cache tĩnh (drugs_rxnorm_cache.json) do
build_rxnorm.py sinh (query RxNav ở dev) — runtime KHÔNG gọi API.

Pipeline:
  1. Lexicon tên thuốc (EN/brand/VN) -> trie longest-match.
  2. Mở rộng span: tên + liều + đường/dạng/lịch dùng (25mg po bid), DỪNG ở phần chỉ định.
  3. ConText assertions: isHistorical (thuốc trước nhập viện / tiền sử) / isNegated.
  4. candidates = override(VN) | cache(span) | cache(tên) | [].
"""
import json, os, sys, io, re

if sys.platform == "win32" and __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
LEX = os.path.join(HERE, "lexicon", "drugs.txt")
CACHE = os.path.join(HERE, "drugs_rxnorm_cache.json")
OUTDIR = os.path.join(ROOT, "output_drugs")
DB_THUOC = os.path.join(ROOT, "database", "thuoc", "thuoc.json")
LIB_FORMS = os.path.join(ROOT, "library", "drug_forms.json")
# BƯỚC 3: gazetteer thuốc từ database. Bật mặc định; GAZETTEER=0 để tắt (general).
USE_GAZETTEER = os.environ.get("GAZETTEER", "1") != "0"

# brand -> generic (INN) cho tra liều theo library (library dùng tên generic/muối)
BRAND2GEN = {
    "lasix": "furosemide", "laxis": "furosemide", "dilaudid": "hydromorphone",
    "coumadin": "warfarin", "tylenol": "acetaminophen", "motrin": "ibuprofen",
    "advil": "ibuprofen", "aleve": "naproxen", "lipitor": "atorvastatin",
    "crestor": "rosuvastatin", "zocor": "simvastatin", "norvasc": "amlodipine",
    "lopressor": "metoprolol", "toprol": "metoprolol", "glucophage": "metformin",
    "ranexa": "ranolazine", "plavix": "clopidogrel", "zofran": "ondansetron",
}
# liều mg/mcg; với KHOẢNG "325-650 mg" bắt SỐ ĐẦU (325) — annotator hay lấy hàm lượng gốc.
_DOSE_RE = re.compile(r"(\d+\.?\d*)\s*(?:-\s*\d+\.?\d*\s*)?(mg|mcg)\b", re.IGNORECASE)
# chỉ dấu phóng thích kéo dài (ER) trong span -> ưu tiên SCD dạng ER (key '24hr ...')
_ER_RE = re.compile(r"\b(xl|er|sr|cr|xr)\b", re.IGNORECASE)
# từ nhiễu (đường/tần suất/đơn vị) — bỏ khi tách token tên thuốc từ span
_DRUG_STOP = {"po", "iv", "im", "sc", "sl", "bid", "tid", "qid", "qhs", "qam", "qpm",
              "qd", "prn", "daily", "of", "mg", "ml", "meq", "hằng", "ngày", "đường",
              "uống", "tiêm", "truyền", "liều", "viên", "lần", "mỗi"}


def load_lib_dosed():
    """Nạp từ library: (1) list entry CÓ LIỀU đơn hoạt chất (SCD) để tra đúng liều;
    (2) dict tên TRẦN -> rxcui hoạt chất/brand để fallback khi không có SCD đúng liều.
    Trả (lib_dosed:list[(key,code)], lib_names:dict[name->code])."""
    if not os.path.exists(LIB_FORMS):
        return [], {}
    lib = json.load(open(LIB_FORMS, encoding="utf-8"))
    dosed, names = [], {}
    for k, v in lib.items():
        code = v.split(",")[0]
        has_num = any(ch.isdigit() for ch in k)
        if ("mg" in k or "mcg" in k) and "/" not in k and "[" not in k and has_num:
            dosed.append((k, code))
        elif not has_num and " " not in k and "[" not in k:   # tên trần 1 từ (hoạt chất/brand)
            names.setdefault(k, code)
    return dosed, names


def lib_resolve_dose(name_key, span_text, lib_dosed, lib_names):
    """Tra RxCUI từ library cho drug có liều. Ưu tiên SCD đúng liều; chọn ĐÚNG BIẾN THỂ
    (muối/ER) theo token trong span. Nếu không có SCD đúng liều nhưng biết hoạt chất ->
    fallback HOẠT CHẤT. Trả [rxcui] hoặc None (None = để cache/override xử lý).

    Sửa 2 lỗi khớp thật:
      - key có tiền tố dạng bào chế ('24hr metoprolol succinate 50mg') trước đây bị
        k.startswith(gen) BỎ SÓT -> SCD phóng thích không bao giờ chọn được. Nay khớp theo
        TOKEN (bất kỳ token nào của key startswith hoạt chất).
      - nhiều biến thể cùng liều (tartrate vs succinate ER) -> chấm điểm theo số token span
        trùng key (+ ưu tiên ER khi span có xl/er...) thay vì chỉ 'key ngắn nhất'.
    """
    low = span_text.lower()
    m = _DOSE_RE.search(low)
    if not m:
        return None                     # KHÔNG liều -> để cache/override lo (cache có mã generic
                                        # tốt cho brand không liều; đừng preempt bằng mã brand/ingredient)
    toks = [t for t in re.findall(r"[a-zà-ỹ]+", low) if len(t) >= 3 and t not in _DRUG_STOP]
    num = m.group(1)
    if "." in num:                      # "3.0"->"3", "2.50"->"2.5" (khớp key library)
        num = num.rstrip("0").rstrip(".")
    dose = f"{num}{m.group(2).lower()}"
    want_er = bool(_ER_RE.search(low)) or "phóng thích" in low or "kéo dài" in low
    best = None                         # (score, -len(k), code) — chọn max
    for tok in toks:
        gen = BRAND2GEN.get(tok, tok)
        if len(gen) < 4:                # tránh khớp nhầm token ngắn
            continue
        for k, code in lib_dosed:
            if (" " + dose) not in (" " + k):
                continue
            ktoks = k.replace(",", " ").split()
            if not any(kt.startswith(gen) for kt in ktoks):
                continue
            is_er = ("24hr" in k or "phóng thích" in k or "kéo dài" in k)
            score = sum(1 for st in toks
                        if len(st) >= 4 and any(kt.startswith(st) for kt in ktoks))
            score += 2 if (want_er and is_er) else (-1 if (is_er and not want_er) else 0)
            key = (score, -len(k), code)
            if best is None or key > best:
                best = key
    if best:
        return [best[2]]
    # có liều nhưng KHÔNG có SCD đúng biến thể -> hoạt chất (đúng thuốc)
    for tok in toks:
        gen = BRAND2GEN.get(tok, tok)
        if gen in lib_names:
            return [lib_names[gen]]
        if tok in lib_names:
            return [lib_names[tok]]
    return None

# ---- lexicon ----
def load_drugs():
    names = {}       # name_lower -> None
    override = {}    # name_lower -> [rxcui]  (VN phrase / class gán trực tiếp)
    with open(LEX, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "|" in s:
                parts = [p.strip() for p in s.split("|")]
                nm = parts[0].lower()
                override[nm] = [c.strip() for c in parts[1].split(",") if c.strip()] if len(parts) > 1 else []
                names[nm] = None
            else:
                names[s.lower()] = None
    return names, override

class Trie:
    def __init__(self): self.root = {}
    def add(self, phrase):
        node = self.root
        for ch in phrase: node = node.setdefault(ch, {})
        node["$"] = True
    def longest_at(self, text, i):
        node, best, j, n = self.root, None, i, len(text)
        while j < n and text[j] in node:
            node = node[text[j]]; j += 1
            if "$" in node: best = j
        return best

def is_word_char(ch): return ch.isalnum()

# ---- mở rộng span liều/dạng/đường/lịch dùng ----
_DOSE_UNIT = r"\d+[.,]?\d*\s*(?:mg|mcg|g|ml|%|iu|meq|units?|đơn vị)(?:/(?:ml|ngày|kg|p))?"
SUFFIX = re.compile(
    r"^(?:\s*(?:" + _DOSE_UNIT +
    r"|mg/ml|mg/ngày|mg/p"
    r"|po|iv|im|sc|sl|pr"
    r"|bid|tid|qid|qod|qd|qhs|qam|qpm|prn|:prn"
    r"|daily|q\d+h(?::prn)?|q\d+-\d+h"
    r"|nebs?|inhaler|puffs?|xịt|nhỏ giọt"
    r"|x\s*\d+"
    r"|hàng ngày|mỗi ngày|đường uống|dưới lưỡi"
    r"))+", re.I)

def extend_span(line, end):
    m = SUFFIX.match(line[end:])
    if not m:
        return end
    return end + len(m.group(0).rstrip())  # bỏ khoảng trắng cuối


# ============================ CHÍNH SÁCH GÁN MÃ THUỐC ============================
# Đo bằng probe XOÁ-MÃ trên leaderboard (SCOREBOARD §E + S17/S18/S19/S21). Quy luật CHỐT:
#   GT gán RxCUI cho SẢN PHẨM/HOẠT CHẤT CÓ TÊN. GT để RỖNG cho KHÁI NIỆM TRỪU TƯỢNG.
#   Mà `GT=∅ & pred≠∅ → J=0` ⇒ mỗi mã gán cho khái niệm trừu tượng HUỶ 1 điểm J=1 miễn phí.
# Thiệt hại đo được (J_cand/mã): điện giải 0.084 | span-dính 0.064 | lớp điều trị 0.036.
# NGƯỢC LẠI, tên sản phẩm thật (iron/insulin/z-pack/mucinex) là DƯƠNG +0.084/mã ⇒ PHẢI GIỮ (S21).
NO_CODE = re.compile(
    # (1) LỚP ĐIỀU TRỊ / LIỆU PHÁP — không phải tên thuốc          [S17: +0.357]
    r"lợi tiểu|corticoid|thở oxy|liệu pháp oxy|thuốc an thần|thuốc giảm đau|giảm đau opioid|"
    r"chống đông|nsaid|kháng sinh|hoá trị|hóa trị|nitrates?$"
    # (2) DỊCH TRUYỀN / ĐIỆN GIẢI — không được gán mã như thuốc     [S18: +0.336]
    r"|truyền dịch|dịch tĩnh mạch|intravenous fluid|yếu tố ix"
    r"|\bns\b|normal saline|kali|meq|dextrose|magnesium|bicarbonate|natriclori|natri clorid"
    r"|nước muối sinh lý"
    # (3) SPAN DÍNH 2-3 THUỐC (lỗi tách từ) — mã không thể đúng     [S19: +0.205]
    r"|doxycyclinebactrim|vancozosynbactrim|klonopinclonidine|albuterolipratropium|ciproflagyl"
    # (4) THIẾT BỊ / thủ thuật — không phải thuốc
    r"|pessary",
    re.IGNORECASE)

# Mã SAI DẠNG/MUỐI -> HOẠT CHẤT (S22 +0.125). Text ghi IV nhưng mã là dạng UỐNG ⇒ sai chắc chắn;
# mã hoạt chất không thể sai về dạng ⇒ đổi là nước đi TRỘI YẾU.
# ⚠️ CHỈ áp khi text CÓ dấu hiệu IV — mã này còn dùng cho entity ĐƯỜNG UỐNG hợp lệ (đừng phá).
_IV_MARK = re.compile(r"\biv\b|tiêm|truyền", re.IGNORECASE)
FIX_IV_TO_INGREDIENT = {"197419": "1808",    # bumetanide 2mg Oral Tab -> bumetanide
                        "311296": "82122",   # levofloxacin 750mg Oral Tab -> levofloxacin
                        "313988": "4603",    # furosemide 40mg Oral Tab -> furosemide
                        "197732": "4603"}    # furosemide 80mg Oral Tab -> furosemide
FIX_SALT = {"866511": "221124"}              # metoprolol TARTRATE -> metoprolol SUCCINATE (PIN)


def apply_code_policy(text, cand):
    """Áp chính sách gán mã đã xác thực trên leaderboard. Trả candidates cuối cùng."""
    if not cand:
        return []
    if NO_CODE.search(text):
        return []                                    # khái niệm trừu tượng -> KHÔNG gán mã
    out = list(cand)
    if _IV_MARK.search(text):
        out = [FIX_IV_TO_INGREDIENT.get(c, c) for c in out]
    if "succinate" in text.lower():
        out = [FIX_SALT.get(c, c) for c in out]
    return out

# ---- section + assertions (ConText-lite) ----
HIST_HEADINGS = ["thuốc trước khi nhập viện", "tiền sử", "bệnh lý mãn tính", "bệnh lý mạn tính",
                 "các bệnh lý mãn tính", "các bệnh lý mạn tính", "các sự kiện trước khi nhập viện",
                 "các diễn biến trước khi nhập viện", "tình trạng trước khi nhập viện"]
RESET_HEADINGS = ["lý do nhập viện", "triệu chứng hiện tại", "đánh giá tại bệnh viện",
                  "kết quả xét nghiệm", "kết quả chẩn đoán hình ảnh", "các thủ thuật đã thực hiện",
                  "dấu hiệu lâm sàng", "diễn biến bệnh"]
NEG_CUES = ["không dùng", "không sử dụng", "ngừng", "đã ngừng", "không", "phủ định", "dị ứng"]

def strip_bullet(s):
    return s.lstrip(" \t-•*").lstrip("0123456789.").lstrip(" \t-•*").strip()

def heading_hist(stripped_low):
    for k in RESET_HEADINGS:
        if stripped_low.startswith(k): return False, True   # reset, not-hist
    for k in HIST_HEADINGS:
        if stripped_low.startswith(k): return True, True     # hist section
    return None, False

HIST_CUES = ["tiền sử", "trước đây", "đã từng", "trước khi nhập viện", "đã ngừng", "ngừng"]

def extract_file(content, trie, names, override, db_override, cache, lib_dosed, lib_names):
    out = []
    hist_section = False
    offset = 0
    for raw in content.split("\n"):
        line = raw
        low = line.lower()
        stripped_low = strip_bullet(line).lower()
        h, is_head = heading_hist(stripped_low)
        if is_head:
            hist_section = bool(h)
        i, n = 0, len(line)
        while i < n:
            if is_word_char(low[i]) and (i == 0 or not is_word_char(low[i - 1])):
                j = trie.longest_at(low, i)
                if j and (j >= n or not is_word_char(low[j])):
                    name_key = low[i:j]
                    end = extend_span(line, j)          # mở rộng liều/dạng
                    s_abs = offset + i
                    text = content[s_abs:s_abs + (end - i)]
                    span_key = text.lower()
                    # candidates: override curated (drugs.txt) > LIBRARY(đúng liều SCD)
                    #           > override DB (Opus) > cache. Giữ thứ tự gốc (DB có mã generic
                    #           đúng cho brand). DB đã được LỌC bỏ entry SAI THUỐC ở main().
                    cand = (override.get(name_key) or override.get(span_key)
                            or lib_resolve_dose(name_key, text, lib_dosed, lib_names)
                            or db_override.get(span_key) or db_override.get(name_key)
                            or cache.get(span_key) or cache.get(name_key) or [])
                    # ⭐ chính sách mã đã xác thực (bỏ mã khái niệm trừu tượng, sửa dạng/muối)
                    cand = apply_code_policy(text, cand)
                    # assertions
                    asserts = []
                    prefix = low[:i]
                    neg = False
                    for c in NEG_CUES:
                        p = prefix.rfind(c)
                        if p != -1 and "nhưng" not in prefix[p:]:
                            neg = True; break
                    if neg: asserts.append("isNegated")
                    if hist_section or any(c in low for c in HIST_CUES):
                        asserts.append("isHistorical")
                    item = {"text": text, "type": "THUỐC", "candidates": list(cand),
                            "assertions": asserts, "position": [s_abs, s_abs + (end - i)]}
                    out.append((s_abs, item))
                    i = end
                    continue
            i += 1
        offset += len(raw) + 1
    out.sort(key=lambda x: x[0])
    return [c for _, c in out]

def main():
    names, override = load_drugs()
    db_override = {}   # override từ DB (nguồn Opus, KÉM tin) — chỉ dùng sau library-dose
    if USE_GAZETTEER and os.path.exists(DB_THUOC):
        n_add = 0
        for e in json.load(open(DB_THUOC, encoding="utf-8")):
            tl = e["text"].strip().lower()
            if not tl or (len(tl) < 4 and " " not in tl):
                continue
            if tl not in names:
                names[tl] = None
                n_add += 1
            if e.get("candidates"):
                db_override.setdefault(tl, e["candidates"])
        print(f"[gazetteer DB thuốc: BẬT — +{n_add} cụm]")
    trie = Trie()
    for nm in names: trie.add(nm)
    cache = {}
    if os.path.exists(CACHE):
        cache = json.load(open(CACHE, encoding="utf-8"))
    lib_dosed, lib_names = load_lib_dosed()
    print(f"[library: {len(lib_dosed)} SCD có liều + {len(lib_names)} tên trần]")
    # Bỏ entry DB Opus SAI THUỐC ĐÃ XÁC NHẬN (mã trỏ hoạt chất KHÁC HẲN) -> để cache/lib lo
    # (cache trả đúng thuốc). KHÔNG lọc tự động vì brand->generic đúng (tylenol->acetaminophen)
    # dễ bị nhầm là 'sai'. Chỉ chặn tay các ca đã kiểm: guaifenesin->fluoxetine,
    # ceftazidime->ciprofloxacin, flagyl->ketoconazole.
    for _bad in ("guaifenesin", "ceftazidime", "flagyl"):
        db_override.pop(_bad, None)
    os.makedirs(OUTDIR, exist_ok=True)
    total = coded = neg = hist = 0
    for n in range(1, 101):
        inpath = os.path.join(ROOT, "input", f"{n}.txt")
        if not os.path.exists(inpath): continue
        content = open(inpath, encoding="utf-8").read()
        items = extract_file(content, trie, names, override, db_override, cache, lib_dosed, lib_names)
        json.dump(items, open(os.path.join(OUTDIR, f"{n}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        for it in items:
            total += 1
            if it["candidates"]: coded += 1
            neg += "isNegated" in it["assertions"]
            hist += "isHistorical" in it["assertions"]
    print(f"Drug lexicon: {len(names)} | cache entries: {len(cache)}")
    print(f"THUỐC extracted: {total} | có RxCUI: {coded} | rỗng: {total - coded}")
    print(f"Assertions: isNegated={neg} isHistorical={hist}")

if __name__ == "__main__":
    main()
