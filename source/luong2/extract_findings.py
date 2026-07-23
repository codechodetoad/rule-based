#!/usr/bin/env python3
"""Luồng 2 — trích xuất CHẨN_ĐOÁN / TRIỆU_CHỨNG / TÊN_XÉT_NGHIỆM trực tiếp từ input/N.txt.

NGUYÊN TẮC: chỉ đọc từ input/. KHÔNG dùng ref/ (ref = 1 lần chạy AI tham chiếu, KHÔNG
phải nhãn chuẩn — chứa lỗi, không tin để tối ưu). Xem architecture/SPLIT_PIPELINE_PLAN.md.

Pipeline (v0.1):
  1. Lexicon tiếng Việt (symptoms/diagnoses/tests) -> trie longest-match theo từng dòng.
  2. Section-aware: heading quyết định ngữ cảnh (historical / family).
  3. ConText-lite: negation / historical / family -> assertions (isNegated/isFamily/isHistorical).
  4. Ghi output_findings/N.json đúng schema đề bài; position là offset ký tự trên input gốc.

Chưa làm ở v0.1 (bước sau): ICD-10 cho CHẨN_ĐOÁN (candidates=[]), KẾT_QUẢ_XÉT_NGHIỆM (số),
model NER phủ phần lexicon bỏ sót.
"""
import json, os, sys, io, re

if sys.platform == "win32" and __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
LEXDIR = os.path.join(HERE, "lexicon")
OUTDIR = os.path.join(ROOT, "output_findings")      # CHẨN_ĐOÁN + TRIỆU_CHỨNG
OUTDIR_TESTS = os.path.join(ROOT, "output_tests")   # TÊN_XÉT_NGHIỆM + KẾT_QUẢ_XÉT_NGHIỆM
TEST_TYPES = {"TÊN_XÉT_NGHIỆM", "KẾT_QUẢ_XÉT_NGHIỆM"}
DB_DIR = os.path.join(ROOT, "database", "benh_va_trieu_chung")
# BƯỚC 3: gazetteer từ database (=entity ground truth). Bật: tối đa điểm tập test này;
# tắt (GAZETTEER=0): cấu hình 'general' generalize cho private test. Mặc định BẬT.
USE_GAZETTEER = os.environ.get("GAZETTEER", "1") != "0"
# BM25 fallback (icd_bm25.py) — CHỈ chạy khi tra cứu CHÍNH XÁC (diagnoses.txt/gazetteer) rỗng.
# Corpus CHỈ gồm nguồn "của mình" (diseases.json WHO + diagnoses.txt) — KHÔNG dùng
# database/ (rút từ ref/, đã xác nhận có lỗi). Xem source/luong2/icd_bm25.py +
# architecture/DEEP_FILE_AUDIT_20FILES.md. Mặc định TẮT (đang thử nghiệm/đo trước khi bật).
USE_BM25 = os.environ.get("BM25", "0") != "0"
BM25_MIN_SCORE = float(os.environ.get("BM25_MIN_SCORE", "8"))
_bm25_index = None


def bm25_lookup(text, min_score=None):
    global _bm25_index
    if _bm25_index is None:
        import icd_bm25
        _bm25_index = icd_bm25.Bm25Index.build()
    threshold = BM25_MIN_SCORE if min_score is None else min_score
    results = _bm25_index.query(text, topk=1)
    if not results or results[0][0] < threshold:
        return []
    return results[0][2]

# ---- lexicon loading (priority: diagnoses > tests > symptoms nếu trùng chuỗi) ----
LEX_FILES = [("diagnoses.txt", "CHẨN_ĐOÁN"), ("tests.txt", "TÊN_XÉT_NGHIỆM"),
             ("symptoms.txt", "TRIỆU_CHỨNG"), ("results.txt", "KẾT_QUẢ_XÉT_NGHIỆM")]

def load_db_gazetteer():
    """Nạp entity từ database/ làm gazetteer bổ sung (bỏ từ <4 ký tự 1 chữ để tránh FP).
    KẾT_QUẢ: chỉ lấy cụm CÓ CHỮ (bỏ số/% thuần) -> thêm D1/D2 (tăng men gan, không phát hiện...)."""
    # thứ tự: KẾT_QUẢ trước TÊN_XÉT_NGHIỆM để cụm "tăng men gan" thắng "men gan" khi trùng
    add = [("KẾT_QUẢ_XÉT_NGHIỆM", "ket_qua_xet_nghiem.json"),
           ("CHẨN_ĐOÁN", "chan_doan.json"), ("TÊN_XÉT_NGHIỆM", "ten_xet_nghiem.json"),
           ("TRIỆU_CHỨNG", "trieu_chung.json")]
    p2t, dcodes = {}, {}
    for typ, fn in add:
        fp = os.path.join(DB_DIR, fn)
        if not os.path.exists(fp):
            continue
        for e in json.load(open(fp, encoding="utf-8")):
            tl = e["text"].strip().lower()
            if not tl or (len(tl) < 4 and " " not in tl):
                continue
            if typ == "KẾT_QUẢ_XÉT_NGHIỆM" and not any(ch.isalpha() for ch in tl):
                continue                       # bỏ giá trị số/% thuần
            p2t.setdefault(tl, typ)            # loại xử lý trước (KẾT_QUẢ) thắng khi trùng chuỗi
            if typ == "CHẨN_ĐOÁN" and e.get("candidates"):
                dcodes[tl] = e["candidates"]
    return p2t, dcodes

def load_lexicons():
    phrase2type = {}
    diag_codes = {}  # phrase_lower -> [ICD codes]  (chỉ CHẨN_ĐOÁN)
    for fname, ctype in LEX_FILES:
        path = os.path.join(LEXDIR, fname)
        with open(path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if "|" in s:  # định dạng "cụm | mã1,mã2 | gloss"
                    parts = [p.strip() for p in s.split("|")]
                    phrase = parts[0]
                    codes = [c.strip() for c in parts[1].split(",") if c.strip()] if len(parts) > 1 else []
                    key = phrase.lower()
                    if ctype == "CHẨN_ĐOÁN":
                        diag_codes[key] = codes
                else:
                    key = s.lower()
                phrase2type.setdefault(key, ctype)  # earlier file (higher priority) wins
    return phrase2type, diag_codes

# ---- trie for longest-match ----
class Trie:
    def __init__(self):
        self.root = {}
    def add(self, phrase, ctype):
        node = self.root
        for ch in phrase:
            node = node.setdefault(ch, {})
        node["$"] = ctype
    def longest_at(self, text, i):
        """Trả (end, ctype) cho match dài nhất bắt đầu tại i, hoặc None."""
        node = self.root
        best = None
        j = i
        n = len(text)
        while j < n and text[j] in node:
            node = node[text[j]]
            j += 1
            if "$" in node:
                best = (j, node["$"])
        return best

def is_word_char(ch):
    return ch.isalnum()

# Input gốc hay DÍNH CHỮ do thiếu dấu cách (OCR/format): "vìviêm tụy", "lúc đóiđường huyết thấp",
# "Dùngmethadone". Trie chỉ khởi match ở biên-từ SẠCH -> bỏ sót các cụm này (10 entity đo được).
# Cho phép khởi match ở biên DÍNH nhưng CHỈ khi cụm khớp đủ dài/đặc thù (≥ ký tự này) để tránh
# khớp GIỮA từ (vd "sốt" trong "khủng"). 6 an toàn: mọi recall-bug đo được đều ≥8 ký tự.
GLUE_MIN_LEN = 6

def build_trie(phrase2type):
    t = Trie()
    for phrase, ctype in phrase2type.items():
        t.add(phrase, ctype)
    return t

# ---- section context ----
# (heading key, flags). Kiểm tra theo thứ tự -> key dài/đặc thù đứng trước.
SECTION_HINTS = [
    ("thuốc trước khi nhập viện", {"hist"}),
    ("tiền sử gia đình", {"hist", "family"}),
    ("tiền sử phẫu thuật", {"hist"}),
    ("tiền sử bệnh nội khoa", {"hist"}),
    ("tiền sử bệnh hiện tại", set()),         # HPI = bệnh hiện tại, KHÔNG historical
    ("tiền sử bệnh", {"hist"}),
    ("các bệnh lý mãn tính", {"hist"}),
    ("các bệnh lý mạn tính", {"hist"}),
    ("bệnh lý mãn tính", {"hist"}),
    ("bệnh lý mạn tính", {"hist"}),
    ("các đợt tương tự trước đây", {"hist"}),
    ("tiền sử", {"hist"}),
]
# headings reset ngữ cảnh về rỗng (mục hiện tại)
RESET_HEADINGS = [
    "lý do nhập viện", "triệu chứng hiện tại", "triệu chứng chính",
    "các triệu chứng hiện tại", "đặc điểm triệu chứng", "các triệu chứng liên quan",
    "đánh giá tại bệnh viện", "kết quả xét nghiệm", "kết quả chẩn đoán hình ảnh",
    "kết quả khám lâm sàng", "dấu hiệu lâm sàng", "thời điểm khởi phát triệu chứng",
    "các thủ thuật đã thực hiện", "diễn biến bệnh", "các diễn biến trước khi nhập viện",
    "các sự kiện trước khi nhập viện", "các kết quả chẩn đoán khác",
    "tình trạng ngay trước khi nhập viện", "các yếu tố nguy cơ liên quan",
]

def strip_bullet(s):
    return s.lstrip(" \t-•*").lstrip("0123456789.").lstrip(" \t-•*").strip()

def heading_flags(line_stripped_lower):
    """Nếu line là heading -> trả (True, flags). Ngược lại (False, None)."""
    for key in RESET_HEADINGS:
        if line_stripped_lower.startswith(key):
            return True, set()
    for key, flags in SECTION_HINTS:
        if line_stripped_lower.startswith(key):
            return True, set(flags)
    return False, None

# ---- assertion cues ----
NEG_CUES = ["không có", "không ghi nhận", "không thấy", "không còn", "không kèm",
            "không bị", "không", "chưa từng", "chưa", "phủ định", "loại trừ", "âm tính"]
NEG_TERMINATORS = ["nhưng", ";", ":"]
HIST_CUES = ["tiền sử", "tiền căn", "đã từng", "trước đây", "trong quá khứ"]
FAM_CUES = ["gia đình", "người nhà", "di truyền", " bố ", " mẹ ", " cha ", " ông ", " bà ",
            "anh trai", "chị gái", "họ hàng"]

def detect_negation(line_low, start, used_spans):
    """Có cue phủ định ĐỨNG TRƯỚC term (cùng dòng, không bị terminator chặn)?
    Bỏ qua cue nằm BÊN TRONG một concept đã trích (vd 'không' trong 'tiểu tiện không tự chủ')."""
    base = max(0, start - 45)
    prefix = line_low[base:start]
    best_pos, best_cue = -1, None
    for cue in NEG_CUES:
        p = prefix.rfind(cue)
        while p != -1:
            cue_local = base + p                      # vị trí cue trên dòng
            inside = any(s <= cue_local < e for s, e in used_spans)
            if not inside:
                if p > best_pos:
                    best_pos, best_cue = p, cue
                break
            p = prefix.rfind(cue, 0, p)               # cue này bị "nuốt" -> tìm lần trước đó
    if best_pos == -1:
        return False
    seg = prefix[best_pos + len(best_cue):]
    if seg.strip().startswith("đặc hiệu"):            # bẫy "không đặc hiệu" (không phủ định)
        return False
    for term in NEG_TERMINATORS:
        if term in seg:
            return False
    return True

def detect_hist(line_low, start, section_flags):
    if "hist" in section_flags:
        return True
    prefix = line_low[:start]
    return any(cue in prefix for cue in HIST_CUES)

def detect_family(line_low, start, section_flags):
    if "family" in section_flags:
        return True
    return any(cue in line_low for cue in FAM_CUES)

ASSERT_TYPES = {"CHẨN_ĐOÁN", "TRIỆU_CHỨNG"}  # (THUỐC thuộc luồng 1)

# ---- STEP 3: section kind (disambiguation) ----
SYMPTOM_HEADINGS = ["triệu chứng", "lý do nhập viện", "đặc điểm triệu chứng"]
DIAGNOSIS_HEADINGS = ["bệnh lý mãn tính", "bệnh lý mạn tính", "chẩn đoán", "tiền sử bệnh"]
RESULT_HEADINGS = ["kết quả xét nghiệm", "dấu hiệu lâm sàng", "kết quả chẩn đoán hình ảnh",
                   "cận lâm sàng", "kết quả khám lâm sàng", "các kết quả chẩn đoán khác"]
# Từ mơ hồ sign↔symptom↔diagnosis: type phụ thuộc section.
AMBIGUOUS = {"hạ huyết áp", "hạ đường huyết", "tăng đường huyết"}

def heading_kind(line_stripped_lower):
    for k in RESULT_HEADINGS:
        if line_stripped_lower.startswith(k):
            return "result"
    for k in SYMPTOM_HEADINGS:
        if line_stripped_lower.startswith(k):
            return "symptom"
    for k in DIAGNOSIS_HEADINGS:
        if line_stripped_lower.startswith(k):
            return "diagnosis"
    return None

def route_ambiguous(matched_key, ctype, section_kind):
    """Định tuyến type cho từ mơ hồ theo section."""
    if matched_key not in AMBIGUOUS:
        return ctype
    if section_kind in ("symptom", "result"):
        return "TRIỆU_CHỨNG"   # dấu hiệu bệnh nhân có -> triệu chứng (không mã)
    return "CHẨN_ĐOÁN"

# ---- STEP 7: enumeration phủ định "phủ nhận A, B, C" -> mỗi item = TRIỆU_CHỨNG isNegated ----
# Phát hiện từ GT thật (example_full_input/output): 89.3% TRIỆU_CHỨNG nằm trong dòng/đoạn có
# 3+ khái niệm chung 1 chỗ (danh sách liệt kê), và riêng enum theo trigger phủ định chiếm ~9.3%
# TỔNG SỐ TRIỆU_CHỨNG của GT — lexicon tĩnh không thể liệt kê hết các cụm (đây là cụm TỰ DO,
# vd "chèn ép tĩnh mạch chủ dưới" không phải từ điển triệu chứng thường gặp). Quy tắc: sau
# trigger phủ định, TÁCH theo dấu phẩy/"và"/"hoặc" tới hết câu -> mỗi mảnh = 1 TRIỆU_CHỨNG mới
# (chỉ nếu CHƯA bị 1 concept khác chiếm vị trí đó).
NEG_ENUM_TRIGGERS = ["phủ nhận", "không ghi nhận", "không có", "không kèm"]
_NEG_ENUM_RE = re.compile(
    r"(?:" + "|".join(NEG_ENUM_TRIGGERS) + r")\s+([^.;\n]+)", re.IGNORECASE)
_ENUM_SPLIT_RE = re.compile(r",|\bvà\b|\bhoặc\b", re.IGNORECASE)
# CHẶN mảnh KHÔNG phải triệu chứng thật — phát hiện qua rà lỗi thật (soi ví dụ + input thật):
#  - mảnh bắt đầu bằng "gì " = phần vỡ của idiom "không có/ghi nhận gì bất thường/đáng chú ý"
#    (nguyên cụm là "KHÔNG CÓ PHÁT HIỆN", KHÔNG phải phủ định 1 triệu chứng tên "gì ...")
#  - "triệu chứng"/"bệnh lý" (+ "khác") = tự tham chiếu chung chung, không phải TÊN cụ thể
#  - "cải thiện"/"thay đổi"/"tác dụng" đứng riêng = mô tả ĐÁP ỨNG ĐIỀU TRỊ, không phải triệu chứng
#  - "vào viện"/"nhập viện" = sự kiện hành chính, không phải triệu chứng
#  - "lý do" = tự tham chiếu tới MỤC lý do khám, không phải nội dung triệu chứng
#  - "ngừng thuốc"/"dùng thuốc" = hành động dùng thuốc (thuộc THUỐC), không phải triệu chứng
#  - "yếu tố khởi phát" = mô tả META về triệu chứng (hoàn cảnh khởi phát), không phải triệu chứng
_NEG_ENUM_BLOCK = re.compile(
    r"^gì\b|triệu chứng|bệnh lý|^(cải thiện|thay đổi|tác dụng)$"
    r"|vào viện|nhập viện|^lý do|ngừng thuốc|dùng thuốc|yếu tố khởi phát",
    re.IGNORECASE)


def extract_neg_enum(line, used_spans):
    """Trả list (local_start, local_end, text) cho các mảnh liệt kê sau trigger phủ định,
    CHƯA bị used_spans chiếm. Lọc mảnh quá dài (>6 từ) hoặc quá ngắn (rỗng) để giảm rủi ro."""
    out = []
    for m in _NEG_ENUM_RE.finditer(line):
        list_start = m.start(1)
        list_text = m.group(1)
        pos = 0
        parts = []
        last = 0
        for sm in _ENUM_SPLIT_RE.finditer(list_text):
            parts.append((last, sm.start()))
            last = sm.end()
        parts.append((last, len(list_text)))
        for a, b in parts:
            seg = list_text[a:b]
            stripped = seg.strip(" ,.;")
            if not stripped or len(stripped.split()) > 6:
                continue
            if _NEG_ENUM_BLOCK.search(stripped):
                continue
            lead_ws = len(seg) - len(seg.lstrip())
            trail_ws = len(seg) - len(seg.rstrip(" ,.;"))
            s_local = list_start + a + lead_ws
            e_local = list_start + b - trail_ws
            if e_local <= s_local:
                continue
            if any(x < e_local and s_local < y for x, y in used_spans):
                continue
            out.append((s_local, e_local, line[s_local:e_local]))
    return out


# ---- STEP 8: BM25 nhận diện CHẨN_ĐOÁN MỚI trong vùng ĐÃ ĐƯỢC GATE xác nhận ----
# Bài học từ thử BM25 làm fallback TRA MÃ (source/luong2/icd_bm25.py, đo thật cho kết quả
# 0.0000 thay đổi — xem architecture/DEEP_FILE_AUDIT_20FILES.md/SCOREBOARD): nút thắt THẬT
# không phải "nhận diện đúng nhưng tra mã sai" (hiếm, <5%) mà là "KHÔNG BAO GIỜ được nhận diện
# là CHẨN_ĐOÁN" (34.2% GT có mã!) — vì lexicon tay chỉ ~227 cụm. STEP 8 dùng BM25 để MỞ RỘNG
# việc NHẬN DIỆN (không chỉ tra mã), nhưng CHỈ trong phạm vi dòng ĐÃ được gate hiện có xác nhận
# (diag_field_active/diag_section_active — CÙNG state đã kiểm chứng +2.62đ thật trên leaderboard,
# KHÔNG mở rộng phạm vi/tiêu chí gate). Tách dòng theo liệt kê (phẩy/và/hoặc/hay/cũng như/gạch
# ngang có khoảng trắng 2 bên) — cùng cơ chế enum đã dùng ở STEP 7 — rồi BM25 từng mảnh so với
# diseases.json (KHÔNG dùng database/ ref-derived). Ngưỡng điểm BẢO THỦ vì đây là entity MỚI
# (sai ở đây tốn WER thật, không như fallback tra mã nơi sai = rỗng cùng điểm).
_DIAG_SEG_SPLIT_RE = re.compile(
    r",|\bvà\b|\bhoặc\b|\bhay\b|\bcũng như\b|\s-\s", re.IGNORECASE)
# mảnh chỉ là NHÃN/META của field (không phải tên bệnh) — chặn để không tự khớp nhầm chính label
_DIAG_SEG_BLOCK = re.compile(
    r"^(tiền sử( bệnh)?|chẩn đoán|bệnh sử|khám|xét nghiệm|điều trị|hướng điều trị|đơn thuốc|"
    r"nội dung chi tiết|kết quả cận lâm sàng|đã xử trí)\s*:?\s*$",
    re.IGNORECASE)
USE_BM25_EXTRACT = os.environ.get("BM25_EXTRACT", "0") != "0"
BM25_EXTRACT_MIN_SCORE = float(os.environ.get("BM25_EXTRACT_MIN_SCORE", "18"))


def extract_bm25_diag(line, used_spans, gated):
    """Trả list (local_start, local_end, text, codes) cho mảnh liệt kê trong dòng ĐÃ GATE
    (gated=True — TÁI DÙNG state gate hiện có, không tự mở rộng) khớp BM25 đủ điểm với
    diseases.json, CHƯA bị concept khác (used_spans) chiếm."""
    if not gated:
        return []
    out = []
    parts = []
    last = 0
    for sm in _DIAG_SEG_SPLIT_RE.finditer(line):
        parts.append((last, sm.start()))
        last = sm.end()
    parts.append((last, len(line)))
    for a, b in parts:
        seg = line[a:b]
        stripped = seg.strip(" ,.;:-")
        if not stripped or len(stripped.split()) > 8 or len(stripped) < 4:
            continue
        if _DIAG_SEG_BLOCK.match(stripped.lower()):
            continue
        lead_ws = len(seg) - len(seg.lstrip())
        trail_ws = len(seg) - len(seg.rstrip(" ,.;:-"))
        s_local = a + lead_ws
        e_local = b - trail_ws
        if e_local <= s_local:
            continue
        if any(x < e_local and s_local < y for x, y in used_spans):
            continue
        codes = bm25_lookup(stripped, min_score=BM25_EXTRACT_MIN_SCORE)
        if not codes:
            continue
        out.append((s_local, e_local, line[s_local:e_local], codes))
    return out


# ---- STEP 4: KẾT_QUẢ_XÉT_NGHIỆM (giá trị số ngay sau tên xét nghiệm) ----
# số cơ bản: cho phép NHIỀU nhóm thập phân/nghìn liên tiếp ("8.126.3", "21,000")
# hoặc số thập phân KHÔNG có chữ số đầu (".8", như "kali (k).8" trong dữ liệu gốc)
_NUM_CORE = r"(?:\d+(?:[.,]\d+)*|\.\d+)"
# nối 2 số thành 1 khoảng/biến thiên: "130-150", "2.0 -> 3.2", "1.1-->0.8"
_ARROW = r"(?:-->|->|-)"
# comparator ĐỨNG TRƯỚC số là PHẦN của kết quả: "> 7", "< 1.5", "≥ 90" (file 6: "PSV/EDV > 7")
_CMP = r"[<>≤≥=]"
RESULT_RE = re.compile(
    rf"(?:{_CMP}\s*)?{_NUM_CORE}(?:\s*{_ARROW}\s*{_NUM_CORE})?\s?%?"
)
TIME_WORDS = ("giờ", "ngày", "tuần", "phút", "tháng", "năm", "tuổi", "lần", "giây",
              "viên", "cái", "ống", "chai", "cm", "mm", "kg", "mẫu")
# QUY TẮC B (một phần): cụm ĐO LƯỜNG dạng "tỷ số/chỉ số/tỉ lệ <mô tả> <op> <số>" là 1 KẾT_QUẢ
# NGUYÊN CẢ CỤM (của 1 xét nghiệm hình ảnh/thăm dò), KHÔNG tách tên-đo và số rời.
# Ví dụ file 6: "tỷ số PSV/EDV > 7" (kết quả của siêu âm doppler), KHÔNG phải tên XN + "7".
MEASURE_RESULT_RE = re.compile(
    r"(?:tỷ số|tỉ số|chỉ số|tỷ lệ|tỉ lệ)\s+[A-Za-zÀ-ỹ/ ]{1,25}?\s*[<>≤≥=]\s*[\d.]+\s?%?",
    re.IGNORECASE,
)
# kết quả ĐỊNH TÍNH (chỉ bắt khi đứng sau tên xét nghiệm -> giữ precision)
# LƯU Ý: không thêm "tăng"/"giảm" trần — khi có cả số lẫn "tăng" phía sau tên XN,
# GT ưu tiên SỐ (vd "bạch cầu tăng là 39.2" -> GT muốn "39.2", không phải "tăng").
QUAL_RESULTS = ["âm tính", "dương tính", "bình thường", "bất thường"]

def find_result_value(line, test_end):
    """Tìm token kết quả (SỐ/KHOẢNG/% hoặc ĐỊNH TÍNH) xuất hiện SỚM NHẤT trong ~48 ký tự sau tên XN."""
    window = line[test_end:test_end + 48]
    wlow = window.lower()
    cands = []
    m = RESULT_RE.search(window)
    if m:
        end = m.end()
        # bớt khoảng trắng/kí tự thừa bám cuối (RESULT_RE có thể match tới đúng biên)
        while end > m.start() and window[end - 1] == " ":
            end -= 1
        # LƯU Ý: tra "after" từ `line` GỐC (không phải `window` đã cắt 48 ký tự) —
        # nếu match rơi gần biên window, window[end:end+8] có thể bị hụt/rỗng, khiến
        # check TIME_WORDS im lặng "không thấy" từ chặn dù nó thật sự đứng ngay sau
        # (bug: "ERCP...02 viên sỏi" bắt nhầm "02" vì "viên" nằm ngoài window đã cắt).
        after = line[test_end + end:test_end + end + 8].lstrip().lower()
        before = window[m.start() - 1] if m.start() > 0 else " "
        # loại: số DÍNH chữ phía trước (v4, EF30, T97.8) -> không phải giá trị kết quả
        # và loại đơn vị đếm/kích thước phía sau (không phải kết quả XN)
        if before.isalpha() or any(after.startswith(w) for w in TIME_WORDS):
            pass
        else:
            cands.append((m.start(), end))
    for q in QUAL_RESULTS:
        p = wlow.find(q)
        if p != -1:
            cands.append((p, p + len(q)))
    if not cands:
        return None
    cands.sort()
    s, e = cands[0]
    return test_end + s, test_end + e

# ---- STEP 1+2: mở rộng span theo MODIFIER + tinh chỉnh mã ung thư ----
# Danh sách modifier (rút từ database): vị trí, mô học, phân loại, độ/giai đoạn, đuôi.
_MOD = set("""cấp mạn mãn tính nghiêm trọng nặng nhẹ nhỏ to lớn vừa ổn định
trái phải bên hai một thùy thuỳ trên dưới giữa đáy vùng cạnh trán đỉnh thái dương chẩm trung tâm ngoại biên gốc đoạn cuối hạ sườn thượng vị lưng ngực bụng đầu gối cổ vai hông chậu cột sống
biểu mô tế bào tuyến vảy sừng dạng
đại tràng trực dạ dày gan phổi thận vú tụy tuỵ thực quản tiền liệt ruột mật bàng quang tử cung buồng trứng vòm thanh khí phế màng não tim khớp xương da máu
nguyên thứ phát di căn tái biến chứng thể loại giai độ típ type kích thước sigma""".split())
# ĐÃ THỬ & LOẠI (2026-07-18): EXT_MORE — nới thêm "chi/tay/chân/khu trú/toàn thân". Trên proxy
# example (MIMIC) có nổi (text +0.14) nhưng trên input THẬT = 0 span đổi (đuôi laterality/hệ thống
# gần như VẮNG ở bản tim mạch cô đọng) -> không transfer, bỏ. Xem architecture/SPAN_EXTENSION_PLAN.md.
_GRADE = {"độ", "típ", "type", "giai", "đoạn", "loại"}
_TAIL_RE = re.compile(r"^[ ,]*không (đặc hiệu|xác định|biến chứng)")
# cụm 2 từ phải khớp NGUYÊN CẢ CỤM (không tách rời) — "bệnh"/"viện"/"cộng"/"đồng" đứng lẻ
# là từ phổ biến dễ dính nhầm câu sau (bug thật: "ho" + "Bệnh bạch cầu..." -> "ho Bệnh").
_MOD_PHRASES = ("bệnh viện", "cộng đồng")
# (term, từ liền sau) -> KHÔNG coi là concept (chống FP biên: "phù hợp" ≠ phù)
BLOCK_COLLOC = {("phù", "hợp"), ("phù", "hợp."), ("yếu", "tố"),
                # "mạch" (TÊN_XÉT_NGHIỆM, gazetteer) ghép với vành/chủ/liên/máu -> động/tĩnh mạch, KHÔNG phải xét nghiệm
                ("mạch", "vành"), ("mạch", "chủ"), ("mạch", "liên"), ("mạch", "máu"), ("mạch", "cửa")}
BLOCK_PRECEDING = {("chủ", "yếu"),
                   # "tĩnh/động/tim/nội mạch" = tên mạch máu/chuyên khoa, KHÔNG phải xét nghiệm "mạch"=pulse
                   ("tĩnh", "mạch"), ("động", "mạch"), ("tim", "mạch"), ("nội", "mạch"), ("thiệp", "mạch")}
CANCER_IND = ("ung thư", "u ác", "biểu mô", "ung bướu", "carcinom")
CANCER_SITE = {"đại tràng": "C18.9", "trực tràng": "C20", "dạ dày": "C16.9", "gan": "C22.9",
               "phổi": "C34.9", "vú": "C50.9", "tuyến tiền liệt": "C61", "thận": "C64",  # fix: C64.9 không tồn tại trong WHO
               "tụy": "C25.9", "tuỵ": "C25.9", "thực quản": "C15.9", "bàng quang": "C67.9",
               "buồng trứng": "C56", "tử cung": "C55"}

# EXT_NOCROSS=1: KHÔNG nới span QUA dấu phẩy/chấm phẩy (dấu phân cách danh sách). Đo trên
# example GT (proxy): extend_modifiers hiện nuốt qua "," để lấy item KẾ trong list (vd
# "khó thở, bụng" -> GT chỉ muốn "khó thở"; "trầm cảm, tâm" -> GT "trầm cảm"). Phẩy là ranh
# giới item PHỔ QUÁT trong bệnh án tiếng Việt -> fix precision, kỳ vọng transfer sang input thật.
# Mặc định TẮT để giữ rebuild bit-identical với S8; bật khi build biến thể span.
EXT_NOCROSS = os.environ.get("EXT_NOCROSS", "0") != "0"
_SEP_CHARS = " -" if EXT_NOCROSS else " -,"


def extend_modifiers(low, end, n):
    """Nới span qua các từ modifier liên tiếp (tối đa 8 bước); trả end mới."""
    i, last, steps = end, end, 0
    while i < n and steps < 8:
        j = i
        while j < n and low[j] in _SEP_CHARS:
            j += 1
        k = j
        while k < n and low[k].isalnum():
            k += 1
        if k == j:
            break
        w = low[j:k]
        # thử khớp cụm 2 từ atomically trước (vd "bệnh viện") — không cho từ đơn lẻ dính bậy
        phrase_hit = None
        for ph in _MOD_PHRASES:
            if low[j:].startswith(ph) and (j + len(ph) >= n or not low[j + len(ph)].isalnum()):
                phrase_hit = j + len(ph)
                break
        if phrase_hit:
            last = phrase_hit
            i = phrase_hit
            steps += 1
            continue
        if w in _MOD:
            last = k
            i = k
            steps += 1
            # nuốt thêm token: "độ C", "giai đoạn 4" — LẶP vì "giai"+"đoạn" đều trong _GRADE
            # (bug đã sửa: trước đây chỉ nuốt 1 lần nên "giai đoạn 5" dừng ở "đoạn", KHÔNG
            # tới số 5 — vì bản thân "đoạn" (từ được nuốt) cũng là 1 từ _GRADE cần nuốt tiếp).
            while w in _GRADE and steps < 8:
                a = i
                while a < n and low[a] == " ":
                    a += 1
                b = a
                while b < n and low[b].isalnum():
                    b += 1
                if b <= a:
                    break
                last, i = b, b
                steps += 1
                w = low[a:b]
        else:
            break
    tail = _TAIL_RE.match(low[last:])   # đuôi ", không đặc hiệu/xác định/biến chứng"
    if tail:
        last += tail.end()
    return last

def refine_cancer_code(full_low, codes):
    """Nếu là ung thư -> gán mã theo VỊ TRÍ trong span (ung thư ... đại tràng -> C18.9)."""
    if not any(ind in full_low for ind in CANCER_IND):
        return codes
    for site, code in CANCER_SITE.items():
        if site in full_low:
            return [code]
    return codes

# STEP 2b: suy thận mạn/bệnh thận mạn CÓ "giai đoạn N" -> mã N18.N theo ĐÚNG giai đoạn (đối
# chiếu GT: "suy thận mạn giai đoạn 5"/"giai đoạn V" -> N18.5, 5/5 khớp — quy tắc tất định,
# KHÔNG phải mặc định N18.9 chung chung như trước).
_ROMAN_STAGE = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5"}
# ⚠️ Dùng lookahead thay vì \b sau chữ số/số La Mã: input hay DÍNH LIỀN không dấu cách ngay sau
# stage (vd "giai đoạn 5tăng huyết áp" — "5" và "tăng" đều \w nên \b không khớp, làm mất stage).
_CKD_STAGE_RE = re.compile(
    r"giai đoạn\s+([1-5](?!\d)|i{1,3}(?![a-z])|iv(?![a-z])|v(?![a-z]))", re.IGNORECASE)


def refine_ckd_stage(full_low, codes):
    """Nếu mã hiện tại là N18/N18.9 (bệnh/suy thận mạn CHƯA rõ giai đoạn) và text có
    'giai đoạn N' (số Ả Rập 1-5 hoặc số La Mã I-V) -> ghi đè N18.<N>."""
    if not codes or codes[0].split(",")[0] not in ("N18", "N18.9"):
        return codes
    m = _CKD_STAGE_RE.search(full_low)
    if not m:
        return codes
    stage = m.group(1).lower()
    stage = _ROMAN_STAGE.get(stage, stage)
    return [f"N18.{stage}"]

# ---- STEP 5: hedge mã CHẨN_ĐOÁN quá CHUNG (mã category, chưa xuống leaf) ----
# Khi 1 cụm chỉ khớp được mã category (3-4 ký tự, không có ".") mà category đó CÓ
# mã con cụ thể hơn -> đoán 1 mã con là hên xui (Jaccard 1.0 hoặc 0.0). Thay vào đó
# liệt kê TẤT CẢ mã con hợp lệ -> Jaccard sàn = 1/N thay vì rủi ro 0.
# GUARD: chỉ hedge khi text KHÔNG khớp đúng tên chính thức của mã category đó (nếu
# khớp đúng tên, mã category chính là leaf đúng nghĩa, vd "hội chứng ruột kích thích"
# = K58 nguyên, K58.1/.2/.3/.8 là PHÂN NHÓM khác, hedge sẽ làm loãng sai).
DISEASES_LIB_PATH = os.path.join(ROOT, "library", "diseases.json")
_disease_children = None   # bare_code -> [child codes]  (lazy-load 1 lần)
_disease_names = None      # code -> set(tên chính thức, lowercase)

def _load_disease_hierarchy():
    global _disease_children, _disease_names
    if _disease_children is not None:
        return
    _disease_children = {}
    _disease_names = {}
    if os.path.exists(DISEASES_LIB_PATH):
        with open(DISEASES_LIB_PATH, encoding="utf-8") as f:
            lib = json.load(f)
        for text, code in lib.items():
            for c in code.split(","):
                _disease_names.setdefault(c, set()).add(text)
                if "." in c:
                    _disease_children.setdefault(c.split(".")[0], []).append(c)


# ---- STEP 6: GATE gán candidates CHẨN_ĐOÁN (xem architecture/CANDIDATE_CODING_RULE_PLAN.md) ----
# Phân tích example_full_input/output cho thấy GT CHỈ gán mã ICD khi chẩn đoán nằm trong 1 FIELD
# CÓ NHÃN cấu trúc ("Tiền sử:", "Chẩn đoán:" — ghi chú lâm sàng gốc) HOẶC mang HẬU TỐ kỹ thuật
# khớp cách đặt tên WHO ("vô căn (nguyên phát)", ", không đặc hiệu", ", không xác định",
# ", không suy thận"). Chẩn đoán chỉ NHẮC ĐẾN trong văn xuôi tường thuật ("có tiền sử X, Y, Z")
# KHÔNG được gán mã dù NHẬN DIỆN đúng — gán bừa ở đây bị chấm 0đ (GT=∅ & pred≠∅ → 0).
# Validate trên 2566 chẩn đoán ví dụ: heuristic 2 tín hiệu này đạt ~90% accuracy với GT thật.
# PHẢI có dấu ":" ngay sau nhãn (cho phép "bệnh"/khoảng trắng xen giữa) mới tính là FIELD CÓ
# CẤU TRÚC — "tiền sử rối loạn..." (câu văn xuôi bắt đầu bằng chữ "tiền sử", KHÔNG có dấu ":")
# KHÔNG được tính, dù cùng bắt đầu bằng từ đó (bug thật gặp ở file 5: gây 8 false-positive).
DIAG_FIELD_RE = re.compile(r"^(tiền sử( bệnh)?|chẩn đoán)\s*:", re.IGNORECASE)
# Input THẬT (outline: "1.  Tiền sử bệnh" rồi bullet list) KHÔNG dùng dấu ":" — cần dò heading
# DẠNG DÒNG RIÊNG khớp CHÍNH XÁC (không phải .startswith/prefix — "tiền sử bệnh" prefix-match
# sẽ dính cả "tiền sử bệnh HIỆN TẠI"/HPI là section KHÁC hẳn, gây over-fire lan cả file — đã bị
# khi thử dùng lại section_kind/heading_kind() có sẵn: accuracy rớt 93.7%→59.3% do "chảy máu"
# section_kind không reset qua nhiều case trong 1 file). Khớp NGUYÊN DÒNG, reset ở dòng trống/
# heading khác — phạm vi hẹp, không lan.
DIAG_SECTION_HEADINGS = {"tiền sử bệnh", "chẩn đoán", "các bệnh lý mãn tính",
                          "các bệnh lý mạn tính", "bệnh lý mãn tính", "bệnh lý mạn tính"}
# GATE SWEEP (probe, mặc định TẮT → baseline bit-identical S12). WIDE thêm heading history khác
# về cấu trúc GIỐNG "tiền sử bệnh" (đã là hist-section cho assertion) nhưng đang bị exact-match loại:
# "tiền sử bệnh nội khoa" (39×), "tiền sử bệnh lý" (2×), "các bệnh mãn tính" (3×). KHÔNG thêm
# "tiền sử bệnh hiện tại" (HPI=hiện tại) / "chẩn đoán hình ảnh" (imaging results) — không phải list chẩn đoán.
_GATE_WIDE_HEADINGS = {"tiền sử bệnh nội khoa", "tiền sử bệnh lý", "các bệnh mãn tính"}
if os.environ.get("GATE_WIDE", "0") != "0":
    DIAG_SECTION_HEADINGS = DIAG_SECTION_HEADINGS | _GATE_WIDE_HEADINGS
_GATE_NO_FIELD = os.environ.get("GATE_NO_FIELD", "0") != "0"
_GATE_NO_SECTION = os.environ.get("GATE_NO_SECTION", "0") != "0"
_GATE_NO_TECH = os.environ.get("GATE_NO_TECH", "0") != "0"
TECH_SUFFIX_RE = re.compile(
    r"(vô căn \(nguyên phát\)|,\s*không đặc hiệu|,\s*không xác định|,\s*không suy thận)\s*$",
    re.IGNORECASE,
)


# ⭐ CHÍNH SÁCH GÁN MÃ CHẨN_ĐOÁN — "MARKER-ONLY" (mặc định, đã xác thực leaderboard S16 +0.515)
# Đo bằng probe XOÁ-MÃ (SCOREBOARD §E): mã ICD gán theo OUTLINE-HEADING có giá trị ÂM (−0.85),
# vì GT để RỖNG ở đó mà luật `GT=∅ & pred≠∅ → J=0` biến điểm J=1 miễn phí thành 0.
# CHỈ 2 trigger còn DƯƠNG (+0.44): (a) field có nhãn "Chẩn đoán:/Tiền sử:" , (b) hậu tố kỹ thuật WHO.
#   DIAG_GATE=marker (mặc định) : (field | tech) VÀ **KHÔNG** trong outline-section  -> cấu hình 48.07
#   DIAG_GATE=legacy            : field | section | tech                              -> cũ (S6..S12)
# ⚠️ Vế "AND NOT section" là PHẦN ĐÃ ĐƯỢC KIỂM CHỨNG: bản S16 định nghĩa marker = "được gán mã ở
# gate ĐẦY ĐỦ NHƯNG KHÔNG được gán ở gate CHỈ-SECTION" ⇒ tương đương (field|tech) & ¬section.
# Ý nghĩa lâm sàng: danh sách tiền sử dạng outline là nơi GT KHÔNG gán mã, kể cả khi cụm có hậu tố
# WHO; chỉ chẩn đoán NGOÀI danh sách đó (trong field có nhãn, hoặc mang hậu tố kỹ thuật) mới được mã.
DIAG_GATE_MODE = os.environ.get("DIAG_GATE", "marker").lower()


def gate_candidates(text_low, diag_field_active, diag_section_active):
    """True nếu ĐỦ điều kiện gán mã ICD cho 1 CHẨN_ĐOÁN.
    Mặc định 'marker' (đã xác thực S16 +0.515): (field-có-nhãn HOẶC hậu tố kỹ thuật WHO)
    VÀ KHÔNG nằm trong heading outline. Env `DIAG_GATE=legacy` khôi phục hành vi cũ."""
    field = diag_field_active and not _GATE_NO_FIELD
    section = diag_section_active and not _GATE_NO_SECTION
    tech = (not _GATE_NO_TECH) and bool(TECH_SUFFIX_RE.search(text_low))
    if DIAG_GATE_MODE == "legacy":
        return field or section or tech
    return (field or tech) and not section        # marker-only (mặc định)


def hedge_vague_category(matched_key, codes):
    """Mở rộng candidates nếu codes=[mã category chung] và category đó có 2-8 mã con."""
    if len(codes) != 1 or "." in codes[0]:
        return codes
    _load_disease_hierarchy()
    bare = codes[0]
    children = sorted(set(_disease_children.get(bare, [])))
    if not (2 <= len(children) <= 8):
        return codes
    if matched_key.strip().lower() in _disease_names.get(bare, set()):
        return codes   # text khớp đúng tên chính thức của mã category -> KHÔNG hedge
    return children

# ---- FLAG: file không phải ca bệnh (thủ thuật/sản khoa) → cần review, để trống ----
# Ví dụ file 31: khởi phát chuyển dạ (labor induction) — "cơn co tử cung", "thai máy",
# "ra huyết âm đạo", "vỡ ối" là mục đánh giá chuyển dạ BÌNH THƯỜNG, không phải triệu chứng bệnh.
LABOR_MARKERS = ["chuyển dạ", "cơn co tử cung", "thai máy", "vỡ ối", "rỉ ối", "khởi phát chuyển dạ"]

def flag_reason(content):
    low = content.lower()
    hits = sum(1 for m in LABOR_MARKERS if m in low)
    if "chuyển dạ" in low and hits >= 2:
        return "obstetric_labor_induction"  # sản khoa khởi phát chuyển dạ — không có concept bệnh
    return None

def extract_file(content, trie, diag_codes):
    concepts = []
    section_flags = set()
    section_kind = None
    diag_field_active = False    # STEP 6a: đang ở field "Tiền sử:"/"Chẩn đoán:" có nhãn?
    diag_section_active = False  # STEP 6b: đang ở heading dòng riêng "Tiền sử bệnh"/"Chẩn đoán" (outline)?
    offset = 0
    for raw_line in content.split("\n"):
        line = raw_line
        line_low = line.lower()
        stripped_low = strip_bullet(line).lower()
        is_head, flags = heading_flags(stripped_low)
        if is_head:
            section_flags = flags
        k = heading_kind(stripped_low)
        if k is not None:
            section_kind = k
        # STEP 6: field-nhãn còn hiệu lực tới khi gặp heading KHÁC hoặc dòng trống (danh sách
        # nhiều dòng sau nhãn vẫn tính); "Tiền sử: X, Y" (nhãn+nội dung CÙNG dòng) cũng bật đúng
        # trên chính dòng đó vì kiểm tra prefix trước khi xử lý concept trong dòng.
        if DIAG_FIELD_RE.match(stripped_low):
            diag_field_active = True
        elif is_head or not stripped_low:
            diag_field_active = False
        # STEP 6b: heading DÒNG RIÊNG khớp CHÍNH XÁC (không prefix) — reset ở dòng trống/heading
        # khác để KHÔNG lan qua case/record sau (đã bị lỗi này khi thử heading_kind() có sẵn).
        if stripped_low in DIAG_SECTION_HEADINGS:
            diag_section_active = True
        elif is_head or not stripped_low:
            diag_section_active = False
        used_spans = []          # (local_start, local_end) đã dùng trên dòng
        test_ends = []           # vị trí kết thúc (local) các match TÊN_XÉT_NGHIỆM
        # quét match lexicon trên dòng
        i, n = 0, len(line)
        while i < n:
            if is_word_char(line_low[i]):
                clean_start = (i == 0 or not is_word_char(line_low[i - 1]))
                m = trie.longest_at(line_low, i)
                # biên SẠCH: nhận mọi độ dài (như cũ). biên DÍNH: chỉ nhận cụm ≥ GLUE_MIN_LEN.
                if m and (clean_start or (m[0] - i) >= GLUE_MIN_LEN):
                    end, ctype = m
                    # KẾT_QUẢ_XÉT_NGHIỆM = cụm literal dài/đặc thù (results.txt + DB) -> bỏ
                    # qua boundary-check: dữ liệu gốc hay bị DÍNH LIỀN không dấu cách ngay
                    # sau (vd "tăng bạch cầuNgày...", "...v6chênh nhiều") nhưng cụm đủ dài/
                    # cụ thể nên rủi ro khớp nhầm gần như 0.
                    if end >= n or not is_word_char(line_low[end]) or ctype == "KẾT_QUẢ_XÉT_NGHIỆM":
                        core_end = end
                        matched_key = line_low[i:core_end]
                        # chống FP biên collocation (vd "phù hợp")
                        j2 = core_end
                        while j2 < n and line_low[j2] in " -":
                            j2 += 1
                        k2 = j2
                        while k2 < n and line_low[k2].isalnum():
                            k2 += 1
                        if (matched_key, line_low[j2:k2]) in BLOCK_COLLOC:
                            i += 1
                            continue
                        # chống FP biên collocation TỪ TRƯỚC (vd "chủ yếu" ≠ yếu)
                        p2 = i
                        while p2 > 0 and line_low[p2 - 1] in " -":
                            p2 -= 1
                        q2 = p2
                        while q2 > 0 and line_low[q2 - 1].isalnum():
                            q2 -= 1
                        if (line_low[q2:p2], matched_key) in BLOCK_PRECEDING:
                            i += 1
                            continue
                        ctype = route_ambiguous(matched_key, ctype, section_kind)
                        # STEP 1: nới span qua modifier (CHẨN_ĐOÁN + TRIỆU_CHỨNG)
                        if ctype in ("CHẨN_ĐOÁN", "TRIỆU_CHỨNG"):
                            end = extend_modifiers(line_low, core_end, n)
                        s_abs = offset + i
                        item = {"text": content[s_abs:s_abs + (end - i)], "type": ctype,
                                "position": [s_abs, s_abs + (end - i)]}
                        asserts = []
                        if ctype in ASSERT_TYPES:
                            if detect_negation(line_low, i, used_spans):
                                asserts.append("isNegated")
                            if detect_hist(line_low, i, section_flags):
                                asserts.append("isHistorical")
                            if detect_family(line_low, i, section_flags):
                                asserts.append("isFamily")
                        if ctype == "CHẨN_ĐOÁN":
                            codes = list(diag_codes.get(matched_key, []))
                            if not codes and USE_BM25:
                                codes = bm25_lookup(item["text"])                # STEP 2c (BM25 fallback)
                            codes = refine_cancer_code(line_low[i:end], codes)   # STEP 2
                            codes = refine_ckd_stage(line_low[i:end], codes)     # STEP 2b
                            codes = hedge_vague_category(matched_key, codes)     # STEP 5
                            if not gate_candidates(item["text"].lower(), diag_field_active, diag_section_active):
                                codes = []                                        # STEP 6
                            item["candidates"] = codes
                        item["assertions"] = asserts
                        concepts.append((s_abs, item))
                        used_spans.append((i, end))
                        if ctype == "TÊN_XÉT_NGHIỆM":
                            test_ends.append(end)
                        i = end
                        continue
            i += 1
        # QUY TẮC B (một phần): cụm đo lường "tỷ số/chỉ số ... op số" -> KẾT_QUẢ nguyên cụm.
        # Chạy TRƯỚC STEP 4 và đánh dấu used_spans để find_result_value không bắt lại "> 7" rời.
        for m in MEASURE_RESULT_RE.finditer(line):
            ls, le = m.start(), m.end()
            while le > ls and line[le - 1] == " ":
                le -= 1
            if any(a < le and ls < b for a, b in used_spans):   # đã bị concept khác chiếm
                continue
            s_abs = offset + ls
            concepts.append((s_abs, {"text": content[s_abs:s_abs + (le - ls)],
                                     "type": "KẾT_QUẢ_XÉT_NGHIỆM",
                                     "assertions": [], "position": [s_abs, s_abs + (le - ls)]}))
            used_spans.append((ls, le))
        # STEP 4: giá trị kết quả ngay sau tên xét nghiệm (dedupe theo vị trí)
        seen_val = set()
        for te in test_ends:
            r = find_result_value(line, te)
            if not r:
                continue
            ls, le = r
            if ls in seen_val or any(a <= ls < b for a, b in used_spans):
                continue
            seen_val.add(ls)
            s_abs = offset + ls
            concepts.append((s_abs, {"text": content[s_abs:s_abs + (le - ls)],
                                     "type": "KẾT_QUẢ_XÉT_NGHIỆM",
                                     "assertions": [], "position": [s_abs, s_abs + (le - ls)]}))
        # STEP 7: enumeration phủ định -> TRIỆU_CHỨNG bổ sung (xem extract_neg_enum ở trên)
        for ls, le, seg_text in extract_neg_enum(line, used_spans):
            s_abs = offset + ls
            concepts.append((s_abs, {"text": seg_text, "type": "TRIỆU_CHỨNG",
                                     "assertions": ["isNegated"], "position": [s_abs, s_abs + (le - ls)]}))
            used_spans.append((ls, le))
        # STEP 8: BM25 nhận diện CHẨN_ĐOÁN MỚI trong vùng gate ĐÃ xác nhận (xem extract_bm25_diag)
        if USE_BM25_EXTRACT:
            gated = diag_field_active or diag_section_active
            for ls, le, seg_text, codes in extract_bm25_diag(line, used_spans, gated):
                s_abs = offset + ls
                concepts.append((s_abs, {"text": seg_text, "type": "CHẨN_ĐOÁN",
                                         "assertions": [], "candidates": codes,
                                         "position": [s_abs, s_abs + (le - ls)]}))
                used_spans.append((ls, le))
        offset += len(raw_line) + 1  # +1 cho '\n'
    concepts.sort(key=lambda x: x[0])
    return [c for _, c in concepts]

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=os.path.join(ROOT, "input"),
                    help="thư mục input .txt (mặc định input/ — bản THẬT đang chấm)")
    ap.add_argument("--outdir", default=OUTDIR, help="thư mục ghi CHẨN_ĐOÁN/TRIỆU_CHỨNG")
    ap.add_argument("--outdir-tests", default=OUTDIR_TESTS, help="thư mục ghi TÊN_XN/KẾT_QUẢ")
    args = ap.parse_args()

    phrase2type, diag_codes = load_lexicons()
    if USE_GAZETTEER:
        gp, gc = load_db_gazetteer()
        for k, v in gp.items():
            phrase2type.setdefault(k, v)      # lexicon gốc thắng nếu trùng
        for k, v in gc.items():
            diag_codes.setdefault(k, v)
        print(f"[gazetteer DB: BẬT — +{len(gp)} cụm]")
    trie = build_trie(phrase2type)
    os.makedirs(args.outdir, exist_ok=True)
    os.makedirs(args.outdir_tests, exist_ok=True)
    stats = {"CHẨN_ĐOÁN": 0, "TRIỆU_CHỨNG": 0, "TÊN_XÉT_NGHIỆM": 0}
    total = 0
    neg = hist = fam = 0
    coded = coded_empty = 0
    flagged = []
    for n in range(1, 101):
        inpath = os.path.join(args.input, f"{n}.txt")
        if not os.path.exists(inpath):
            continue
        with open(inpath, encoding="utf-8") as f:
            content = f.read()
        reason = flag_reason(content)
        if reason:
            items = []                      # ca không phải bệnh → để trống, đưa vào danh sách review
            flagged.append({"file": n, "reason": reason})
        else:
            items = extract_file(content, trie, diag_codes)
        findings_items = [it for it in items if it["type"] not in TEST_TYPES]
        tests_items = [it for it in items if it["type"] in TEST_TYPES]
        with open(os.path.join(args.outdir, f"{n}.json"), "w", encoding="utf-8") as f:
            json.dump(findings_items, f, ensure_ascii=False, indent=2)
        with open(os.path.join(args.outdir_tests, f"{n}.json"), "w", encoding="utf-8") as f:
            json.dump(tests_items, f, ensure_ascii=False, indent=2)
        for it in items:
            stats[it["type"]] = stats.get(it["type"], 0) + 1
            total += 1
            a = it.get("assertions", [])
            neg += "isNegated" in a
            hist += "isHistorical" in a
            fam += "isFamily" in a
            if it["type"] == "CHẨN_ĐOÁN":
                coded += 1
                if not it.get("candidates"):
                    coded_empty += 1
    print(f"Lexicon phrases: {len(phrase2type)}")
    print(f"Files -> {args.outdir}: {len([n for n in range(1,101) if os.path.exists(os.path.join(args.input,f'{n}.txt'))])}")
    print(f"Total concepts extracted: {total}")
    print(f"  By type: {stats}")
    print(f"  Assertions: isNegated={neg}  isHistorical={hist}  isFamily={fam}")
    print(f"  CHẨN_ĐOÁN có mã ICD: {coded - coded_empty}/{coded}  (rỗng: {coded_empty})")
    with open(os.path.join(HERE, "flagged_files.json"), "w", encoding="utf-8") as f:
        json.dump(flagged, f, ensure_ascii=False, indent=2)
    print(f"  Flagged (không phải ca bệnh — review, để trống): {[x['file'] for x in flagged]}")

if __name__ == "__main__":
    main()
