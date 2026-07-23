#!/usr/bin/env python3
"""LOCAL SCORER — port CHÍNH XÁC bộ chấm chính thức (theo bản dựng lại chi tiết ở
med_test/Medical-Knowledge-Retrieval/tests/test_pipeline_eval.py + docs/Evaluation Pipeline.md).

Công thức (đã xác nhận từ code, KHÔNG còn đoán):
  text   = mean_record max(0, 1 − WER);  WER = editdistance(join(GT.text), join(pred.text))/#GT_words
  assert = mean_record mean_entity J_set(GT.assertions, pred.assertions)   (align = char-overlap)
  cand   = Σ J_set(GT.cand,pred.cand)·(len(GT.cand)+1) / Σ (len(GT.cand)+1)  (weight = SỐ MÃ +1)
  final  = 0.3·text + 0.3·assert + 0.4·cand
  align (assert+cand): entity GT ghép pred có CHAR-OVERLAP lớn nhất; lệch type -> J=0.

⚠️ CHẤM CẦN GROUND TRUTH. Ta KHÔNG có GT thật. Mặc định chấm vs ref/ = CHỈ đo GIỐNG-ref
(ref là 1 lần chạy Opus, CÓ LỖI — KHÔNG phải leaderboard). Dùng để: (1) xem phân rã per-file,
(2) A/B TƯƠNG ĐỐI giữa 2 output, (3) kiểm ref có xấp xỉ được leaderboard không.

Chạy:
  python source/eval/local_scorer.py                              # output_closest_llm vs ref
  python source/eval/local_scorer.py --pred variant_drugfix       # đổi output cần chấm
  python source/eval/local_scorer.py --pred A --gt B --worst 10   # A vs B, in 10 file tệ nhất
"""
import argparse
import io
import json
import os
import sys

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def load(d, n):
    p = os.path.join(ROOT, d, f"{n}.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []


def set_jaccard(s1, s2):
    if not s1 and not s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    return len(s1 & s2) / len(s1 | s2)


def wer(ref, hyp):
    r = ref.lower().split()
    h = hyp.lower().split()
    if not r:
        return 1.0 if h else 0.0
    prev = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        cur = [i] + [0] * len(h)
        for j in range(1, len(h) + 1):
            cur[j] = prev[j - 1] if r[i - 1] == h[j - 1] else 1 + min(prev[j], cur[j - 1], prev[j - 1])
        prev = cur
    return prev[len(h)] / len(r)


def best_overlap(gt, preds):
    gs, ge = gt["position"]
    best, mx = None, 0
    for p in preds:
        ps, pe = p["position"]
        ov = max(0, min(ge, pe) - max(gs, ps))
        if ov > mx:
            mx, best = ov, p
    return best


def score(pred_dir, gt_dir):
    text_scores, assert_scores = [], []
    cand_num = cand_den = 0.0
    per_file = []
    for n in range(1, 101):
        gt = load(gt_dir, n)
        pred = load(pred_dir, n)
        # text: WER trên chuỗi nối text các entity (theo thứ tự list)
        ref_t = " ".join(e.get("text", "") for e in gt)
        hyp_t = " ".join(e.get("text", "") for e in pred)
        w = wer(ref_t, hyp_t)
        t_sc = max(0.0, 1.0 - w)
        text_scores.append(t_sc)
        # assert + cand: lặp theo entity GT, align char-overlap
        rec_assert = []
        f_cnum = f_cden = 0.0
        for g in gt:
            bm = best_overlap(g, pred)
            g_ass = set(g.get("assertions") or [])
            p_ass = set(bm.get("assertions") or []) if bm else set()
            if bm and bm["type"] != g["type"]:
                p_ass = {"__type_mismatch__"}
            rec_assert.append(set_jaccard(g_ass, p_ass))
            g_c = set(g.get("candidates") or [])
            p_c = set(bm.get("candidates") or []) if bm else set()
            if bm and bm["type"] != g["type"]:
                p_c = {"__type_mismatch__"}
            weight = len(g_c) + 1
            cj = set_jaccard(g_c, p_c)
            cand_num += cj * weight
            cand_den += weight
            f_cnum += cj * weight
            f_cden += weight
        a_sc = (sum(rec_assert) / len(rec_assert)) if rec_assert else 1.0
        assert_scores.append(a_sc)
        per_file.append((n, t_sc, a_sc, (f_cnum / f_cden if f_cden else 1.0)))
    text = sum(text_scores) / len(text_scores)
    asrt = sum(assert_scores) / len(assert_scores)
    cand = cand_num / cand_den if cand_den else 0.0
    final = 0.3 * text + 0.3 * asrt + 0.4 * cand
    return text, asrt, cand, final, per_file


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default="output_closest_llm")
    ap.add_argument("--gt", default="ref")
    ap.add_argument("--worst", type=int, default=0, help="in N file 'tệ nhất' (theo final per-file)")
    args = ap.parse_args()

    text, asrt, cand, final, per_file = score(args.pred, args.gt)
    print(f"PRED = {args.pred}   vs   GT = {args.gt}")
    print(f"  Text (1−WER):     {text*100:7.4f}")
    print(f"  Assertions:       {asrt*100:7.4f}")
    print(f"  Candidates:       {cand*100:7.4f}")
    print(f"  FINAL:            {final*100:7.4f}  = 0.3·{text*100:.2f} + 0.3·{asrt*100:.2f} + 0.4·{cand*100:.2f}")
    if args.gt == "ref":
        print("  ⚠️ vs ref (KHÔNG phải GT thật) — chỉ đo giống-ref, KHÔNG bằng leaderboard.")
    if args.worst:
        print(f"\n  {args.worst} file điểm thấp nhất (per-file final):")
        for n, t, a, c in sorted(per_file, key=lambda x: 0.3 * x[1] + 0.3 * x[2] + 0.4 * x[3])[:args.worst]:
            print(f"    f{n:<3} text={t*100:5.1f} assert={a*100:5.1f} cand={c*100:5.1f}")


if __name__ == "__main__":
    main()
