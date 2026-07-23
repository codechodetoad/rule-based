#!/usr/bin/env python3
"""Phase 2 (RAG linking) — retrieval nhúng cho CHẨN_ĐOÁN/THUỐC còn TRỐNG mã sau khi resolver
lexicon/exact-match đã chạy (graft() trong generate_all.py). Nhúng Qwer3-Embedding-4B qua
ViettelAI, so cosine với thư viện đã cache (build_library_embeddings.py).

⚠️ Kỷ luật GATE giống hệt policy đã validate (CODEBOOK §4): GT=∅ & pred≠∅ → J=0, nên gán mã SAI
HUỶ 1 điểm miễn phí. Script này CHỈ gán khi:
  1. Entity KHÔNG khớp pattern trừu tượng đã biết (NO_CODE cho THUỐC, bare-comorbidity cho CHẨN_ĐOÁN).
  2. Cosine similarity top-1 VƯỢT ngưỡng cao (mặc định 0.82) — ưu tiên ĐỘ CHÍNH XÁC hơn ĐỘ PHỦ.
  3. Margin rõ ràng so với top-2 (tránh trường hợp mơ hồ, nhiều mã gần bằng nhau).
CHỈ ĐIỀN vào chỗ TRỐNG — không bao giờ ghi đè mã đã có.

Chạy: python source/eval/rag_link.py --src output_v3_final --dst output_v5_final
"""
import argparse
import json
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
EMB_DIR = os.path.join(ROOT, "library", "embeddings")
MODEL = "Qwen3-Embedding-4B"
BASE_URL = "https://viettelai.vn/model-as-a-service"

SIM_THRESH = 0.82
MARGIN_THRESH = 0.03

# thuốc trừu tượng — dùng lại NO_CODE đã validate (luong1/extract_drugs.py)
sys.path.insert(0, os.path.join(HERE, "..", "luong1"))
import extract_drugs as ed  # noqa: E402

_BARE_COMORBID = {"tăng huyết áp", "đái tháo đường", "tiểu đường", "rung nhĩ", "béo phì",
                  "suy tim", "suy thận mạn"}

_DOSE_TAIL = re.compile(
    r"\s+\d[\d.,]*\s*(mg|mcg|g|ml|iu|đơn vị|viên|ống|lần|mg/ml|mg/kg).*$", re.I)

# Gate bổ sung (tìm thấy qua dry-run kiểm tra thủ công): text NGẮN/rời rạc match "chuẩn" (sim cao)
# nhưng SAI vì quá mơ hồ (vd 'nhiễm' 1 từ -> alkalosis; 'sỏi' 1 từ -> sỏi thận đoán bừa) — sim/margin
# KHÔNG phân biệt được các ca này, cần chặn cứng theo ĐỘ DÀI. Và tiền tố "TS "/"tiền sử" (history-of
# marker) khớp nhầm sang mã BỆNH CẤP TÍNH thay vì trạng thái tiền sử (vd 'TS thay khớp háng' ->
# trật khớp háng SAI) — chặn riêng.
MIN_QUERY_LEN = 8
_HISTORY_PREFIX = re.compile(r"^(ts|tiền sử)\s", re.I)
# "đái tháo đường típ 2" cụ thể đã bị TEST và THUA trên input cũ (CODEBOOK S28, -0.384) dù đặc
# hiệu lâm sàng — GT không code phrasing này dù có mã ICD riêng (E11). Chặn khớp RAG cho đúng case
# (KHÔNG chặn toàn bộ "đái tháo đường" — biến chứng cụ thể khác vẫn có thể hợp lệ theo S31).
_DIAB_TYPE2 = re.compile(r"(đái tháo đường|tiểu đường)\s*(típ|type|tuýp)\s*2\b", re.I)


def clean_drug_text(t):
    t = t.strip()
    m = _DOSE_TAIL.search(t)
    if m:
        t = t[:m.start()]
    return t.strip().lower()


def load_key():
    p = os.path.join(ROOT, ".env")
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line.startswith("VIETTELAI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.getenv("VIETTELAI_API_KEY")


def embed(client, texts):
    if not texts:
        return np.zeros((0, 2560), dtype=np.float32)
    out = []
    for i in range(0, len(texts), 500):
        r = client.embeddings.create(input=texts[i:i + 500], model=MODEL, encoding_format="float")
        out.extend(e.embedding for e in r.data)
    return np.array(out, dtype=np.float32)


def normalize(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.clip(n, 1e-8, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="output_v3_final")
    ap.add_argument("--dst", default="output_v5_final")
    ap.add_argument("--sim-thresh", type=float, default=SIM_THRESH)
    ap.add_argument("--margin-thresh", type=float, default=MARGIN_THRESH)
    ap.add_argument("--drug-sim-thresh", type=float, default=0.87,
                    help="THUỐC: ngưỡng sim (KHÔNG dùng margin — nhiều dạng cùng hoạt chất)")
    ap.add_argument("--hedge", action="store_true",
                    help="CHẨN_ĐOÁN mơ hồ subtype (sim cao, margin thấp) -> gán CẢ top-2 (hedge)")
    ap.add_argument("--hedge-sim-thresh", type=float, default=0.86,
                    help="ngưỡng sim tối thiểu để hedge (cao hơn để chắc là bệnh THẬT)")
    ap.add_argument("--dry-run", action="store_true", help="chỉ in ứng viên, không ghi file")
    args = ap.parse_args()

    diseases = np.load(os.path.join(EMB_DIR, "diseases.npz"), allow_pickle=True)
    drugs = np.load(os.path.join(EMB_DIR, "drug_synonyms.npz"), allow_pickle=True)
    dis_vecs = normalize(diseases["vecs"]); dis_names = diseases["names"]; dis_codes = diseases["codes"]
    drg_vecs = normalize(drugs["vecs"]); drg_names = drugs["names"]; drg_codes = drugs["codes"]
    print(f"library: diseases={len(dis_names)}  drug_synonyms={len(drg_names)}")

    # thu thập entity còn trống mã, đủ điều kiện thử RAG
    targets = []   # (n, entity_idx, type, query_text_for_embed, raw_text)
    all_data = {}
    for n in range(1, 101):
        p = os.path.join(ROOT, args.src, f"{n}.json")
        arr = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []
        all_data[n] = arr
        for i, e in enumerate(arr):
            if e.get("candidates"):
                continue
            ty = e.get("type")
            text = e.get("text", "").strip()
            if not text:
                continue
            if ty == "THUỐC":
                if ed.NO_CODE.search(text.lower()):
                    continue
                q = clean_drug_text(text)
                if len(q) < 3:
                    continue
                targets.append((n, i, ty, q, text))
            elif ty == "CHẨN_ĐOÁN":
                tl = text.strip().lower()
                # span cụt mất ký tự đầu (đã gặp: "tăng huyết áp" -> "ăng huyết áp") vẫn khớp
                # bare comorbidity nếu thêm lại 1 ký tự đầu bất kỳ -> coi như bare, chặn luôn.
                truncated_bare = any((c + tl) in _BARE_COMORBID for c in "tđsr")
                if (tl in _BARE_COMORBID or len(tl) < MIN_QUERY_LEN or _HISTORY_PREFIX.match(tl)
                        or _DIAB_TYPE2.search(tl) or truncated_bare):
                    continue
                targets.append((n, i, ty, tl, text))

    print(f"entities còn trống mã, đủ điều kiện thử RAG: {len(targets)}")
    if not targets:
        return

    import openai
    client = openai.OpenAI(api_key=load_key(), base_url=BASE_URL)
    qvecs = normalize(embed(client, [t[3] for t in targets]))

    assigned = 0
    log_lines = []
    for (n, i, ty, q, raw), qv in zip(targets, qvecs):
        if ty == "THUỐC":
            sims = drg_vecs @ qv
            names, codes = drg_names, drg_codes
        else:
            sims = dis_vecs @ qv
            names, codes = dis_names, dis_codes
        top_idx = np.argsort(-sims)[:3]
        top_sims = sims[top_idx]
        best_sim = float(top_sims[0])
        margin = float(top_sims[0] - top_sims[1]) if len(top_sims) > 1 else 1.0
        hedged = False
        if ty == "THUỐC":
            # THUỐC: nhiều entry library = CÙNG hoạt chất khác liều/dạng -> margin THẤP KHÔNG phải
            # mơ hồ (là "nhiều dạng của đúng thuốc"). Bỏ margin gate, chỉ cần sim đủ cao.
            # Thuốc là mã AN TOÀN (concrete product, GT hầu như luôn code -> code chỉ giúp/hoà).
            ok = best_sim >= args.drug_sim_thresh
            chosen = [codes[top_idx[0]]]
        else:
            confident = best_sim >= args.sim_thresh and margin >= args.margin_thresh
            # HEDGE (gamble 2026-07-23): match sim CAO nhưng margin THẤP = mơ hồ SUBTYPE (top-2 sát
            # nhau). Thay vì BỎ (skip) hoặc gамble top-1 (0 nếu sai subtype), gán CẢ top-2 -> nếu GT
            # là 1 trong 2 thì J=1/2 thay vì 0. Chỉ khi sim rất cao (bệnh THẬT, không phải trừu tượng).
            ambiguous = (args.hedge and best_sim >= args.hedge_sim_thresh
                         and margin < args.margin_thresh and not confident)
            ok = confident or ambiguous
            if ambiguous:
                # bỏ trùng mã, giữ thứ tự (top-1, top-2); tách mã ghép "A,B" thành từng phần tử
                seen_c, chosen = set(), []
                for j in top_idx[:2]:
                    for c in str(codes[j]).split(","):
                        c = c.strip()
                        if c and c not in seen_c:
                            seen_c.add(c); chosen.append(c)
                hedged = True
            else:
                chosen = [codes[top_idx[0]]]
        log_lines.append(
            f"{'HEDGE ' if hedged else ('ASSIGN' if ok else 'skip  ')}  {ty:10s} {raw!r:45s} -> "
            f"{names[top_idx[0]]!r} code={chosen if ok else codes[top_idx[0]]}  "
            f"sim={best_sim:.3f} margin={margin:.3f}")
        if ok:
            code = ed.apply_code_policy(raw.lower(), chosen) if ty == "THUỐC" else chosen
            if code:
                all_data[n][i]["candidates"] = list(code)
                assigned += 1

    log_path = os.path.join(ROOT, "scratch_rag_link_log.txt")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))
    print(f"assigned: {assigned}/{len(targets)}  (log -> {log_path})")

    if args.dry_run:
        return
    dst = os.path.join(ROOT, args.dst)
    os.makedirs(dst, exist_ok=True)
    for n in range(1, 101):
        json.dump(all_data[n], open(os.path.join(dst, f"{n}.json"), "w", encoding="utf-8"),
                   ensure_ascii=False, indent=2)
    print(f"-> {dst}/")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
