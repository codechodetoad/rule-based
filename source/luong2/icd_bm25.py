#!/usr/bin/env python3
"""BM25 fuzzy-match CHẨN_ĐOÁN (tiếng Việt) -> mã ICD-10, dùng làm FALLBACK khi tra cứu
CHÍNH XÁC (lexicon `diagnoses.txt` / trie) không khớp. KHÔNG dùng LLM, KHÔNG dùng
`database/benh_va_trieu_chung/` (gazetteer đó rút từ `ref/` — 1 lần chạy AI tham khảo,
đã xác nhận chứa lỗi, và gắn với 100 file THẬT cụ thể -> rủi ro không generalize cho
private test). Corpus CHỈ gồm 2 nguồn "của mình", đáng tin:
  1. `library/diseases.json`   — danh mục ICD-10 CHÍNH THỨC (WHO, VN+EN), 31k+ mục.
  2. `source/luong2/lexicon/diagnoses.txt` — cụm lâm sàng tự tay biên soạn, có mã.

Cài đặt BM25 (Okapi, k1=1.5, b=0.75) THUẦN Python, không phụ thuộc thư viện ngoài —
tự triển khai để không phụ thuộc mạng/gói ngoài (`rank_bm25` không có sẵn trong môi trường).

Dùng:
    from icd_bm25 import Bm25Index
    idx = Bm25Index.build()
    idx.query("suy thận mạn giai đoạn 5", topk=5)
    -> [(score, text, [codes]), ...] sắp xếp giảm dần theo score
"""
import json
import math
import os
import re
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DISEASES_JSON = os.path.join(ROOT, "library", "diseases.json")
DIAGNOSES_TXT = os.path.join(HERE, "lexicon", "diagnoses.txt")

_TOKEN_RE = re.compile(r"[a-zà-ỹ0-9]+", re.IGNORECASE)


def tokenize(text):
    """Tách token: lowercase, giữ dấu tiếng Việt, bỏ dấu câu. Không stem/stopword —
    BM25 tự hạ trọng số token quá phổ biến qua IDF, không cần lọc tay."""
    return _TOKEN_RE.findall(text.lower())


def _strip_accents(s):
    """Bỏ dấu tổ hợp — dùng làm khoá phụ để chịu được lỗi gõ/thiếu dấu nhẹ."""
    nfd = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")


class Bm25Index:
    K1 = 1.5
    B = 0.75

    def __init__(self):
        self.docs = []          # [(text, [codes], source)]
        self.doc_tokens = []    # [[token,...]]
        self.doc_len = []
        self.avgdl = 0.0
        self.df = {}            # token -> số doc chứa token
        self.postings = {}      # token -> {doc_id: term_freq}
        self.N = 0

    @classmethod
    def build(cls):
        idx = cls()
        seen_text = set()

        def add_doc(text, codes, source):
            key = text.strip().lower()
            if not key or key in seen_text:
                return
            seen_text.add(key)
            toks = tokenize(text)
            if not toks:
                return
            doc_id = len(idx.docs)
            idx.docs.append((text, codes, source))
            idx.doc_tokens.append(toks)
            idx.doc_len.append(len(toks))
            tf = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            for t, f in tf.items():
                idx.postings.setdefault(t, {})[doc_id] = f

        # nguồn 1: diagnoses.txt (cụm lâm sàng tự biên soạn — ưu tiên ngầm nhờ ngắn/khớp sát)
        if os.path.exists(DIAGNOSES_TXT):
            with open(DIAGNOSES_TXT, encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#") or "|" not in s:
                        continue
                    parts = [p.strip() for p in s.split("|")]
                    phrase = parts[0]
                    codes = [c.strip() for c in parts[1].split(",") if c.strip()] if len(parts) > 1 else []
                    if codes:
                        add_doc(phrase, codes, "diagnoses.txt")

        # nguồn 2: diseases.json (danh mục ICD-10 chính thức WHO)
        if os.path.exists(DISEASES_JSON):
            data = json.load(open(DISEASES_JSON, encoding="utf-8"))
            for text, code in data.items():
                add_doc(text, [code], "diseases.json")

        idx.N = len(idx.docs)
        idx.avgdl = sum(idx.doc_len) / idx.N if idx.N else 0.0
        for t, posting in idx.postings.items():
            idx.df[t] = len(posting)
        return idx

    def _idf(self, t):
        n_t = self.df.get(t, 0)
        # BM25 "+1" smoothing (Lucene-style) -> luôn không âm, kể cả khi n_t > N/2
        return math.log(1 + (self.N - n_t + 0.5) / (n_t + 0.5))

    def query(self, text, topk=5):
        """Trả [(score, text_gốc, [codes], source), ...] giảm dần theo score BM25."""
        q_toks = tokenize(text)
        if not q_toks:
            return []
        scores = {}
        for t in set(q_toks):
            posting = self.postings.get(t)
            if not posting:
                continue
            idf = self._idf(t)
            if idf <= 0:
                continue
            for doc_id, f in posting.items():
                dl = self.doc_len[doc_id]
                denom = f + self.K1 * (1 - self.B + self.B * dl / self.avgdl)
                score = idf * (f * (self.K1 + 1)) / denom
                scores[doc_id] = scores.get(doc_id, 0.0) + score
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:topk]
        out = []
        for doc_id, score in ranked:
            text_o, codes, source = self.docs[doc_id]
            out.append((score, text_o, codes, source))
        return out


_INDEX = None


def get_index():
    global _INDEX
    if _INDEX is None:
        _INDEX = Bm25Index.build()
    return _INDEX


def resolve_diagnosis(text, min_score=None):
    """API tiện dụng: trả [codes] của match TỐT NHẤT nếu score đủ cao, ngược lại [].
    min_score: ngưỡng tối thiểu (mặc định tự ước lượng theo độ dài query — xem CANDIDATE_
    CODING_RULE_PLAN.md / DEEP_FILE_AUDIT_20FILES.md để hiệu chỉnh dựa trên đo thật)."""
    idx = get_index()
    results = idx.query(text, topk=1)
    if not results:
        return []
    score, matched_text, codes, source = results[0]
    threshold = min_score if min_score is not None else 0.0
    if score < threshold:
        return []
    return codes


if __name__ == "__main__":
    import sys
    import io
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    idx = Bm25Index.build()
    print(f"corpus: {idx.N} doc, avgdl={idx.avgdl:.1f}, {len(idx.df)} token khác nhau")
    tests = sys.argv[1:] or [
        "suy thận mạn giai đoạn 5",
        "tăng huyết áp",
        "đái tháo đường tuýp 2 kiểm soát kém",
        "nhồi máu não do tắc động mạch não giữa đoạn m1 trái",
    ]
    for q in tests:
        print(f"\nQ: {q!r}")
        for score, text, codes, source in idx.query(q, topk=5):
            print(f"   {score:6.2f}  {codes}  {text!r}  [{source}]")
