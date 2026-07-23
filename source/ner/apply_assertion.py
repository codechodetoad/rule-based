#!/usr/bin/env python3
"""P1 — gán ASSERTION vào 1 output dir bằng model_assert (dự đoán, ngưỡng CAO cho precision).

CHỈ đổi trường `assertions` của entity CHẨN_ĐOÁN/THUỐC/TRIỆU_CHỨNG — KHÔNG đụng text/type/position/
candidates (probe assertion cô lập: anchor text & cand phải bất động). isFamily thiếu dữ liệu train
(2 mẫu) -> mặc định BỎ (chỉ bật nếu --family-thresh đặt thấp).

Ngưỡng mặc định 0.6 (cao hơn 0.5) vì sai assertion biến ∅-vs-∅=1 thành 0 -> ưu tiên precision.

Chạy: python source/ner/apply_assertion.py --src output_v5_final --dst output_v7_final --thresh 0.6
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
ELIGIBLE = {"CHẨN_ĐOÁN", "THUỐC", "TRIỆU_CHỨNG"}
LABELS = ["isNegated", "isHistorical", "isFamily"]   # overridden from model meta if present
PRE, POST = 300, 80


def make_window(content, s, e):
    a = max(0, s - PRE)
    b = min(len(content), e + POST)
    return content[a:s] + " « " + content[s:e] + " » " + content[e:b]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="output_v5_final")
    ap.add_argument("--dst", default="output_v7_final")
    ap.add_argument("--model", default=os.path.join(HERE, "model_assert"))
    ap.add_argument("--thresh", type=float, default=0.6)
    ap.add_argument("--family-thresh", type=float, default=1.01,  # >1 => tắt isFamily mặc định
                    help="ngưỡng riêng cho isFamily (mặc định TẮT vì chỉ 2 mẫu train)")
    ap.add_argument("--replace", action="store_true",
                    help="THAY toàn bộ assertion bằng dự đoán classifier (mặc định: chỉ điền chỗ trống)")
    ap.add_argument("--neg-thresh", type=float, default=None, help="ngưỡng riêng isNegated")
    ap.add_argument("--hist-thresh", type=float, default=None, help="ngưỡng riêng isHistorical")
    args = ap.parse_args()

    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification

    global LABELS
    mp = os.path.join(args.model, "assert_meta.json")
    if os.path.exists(mp):
        LABELS = json.load(open(mp, encoding="utf-8"))["labels"]
    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForSequenceClassification.from_pretrained(args.model)
    model.eval()
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(dev)

    dst = os.path.join(ROOT, args.dst)
    os.makedirs(dst, exist_ok=True)
    _per = {"isNegated": args.neg_thresh, "isHistorical": args.hist_thresh, "isFamily": args.family_thresh}
    thr = [(_per[l] if _per.get(l) is not None else args.thresh) for l in LABELS]
    print(f"thresholds: {dict(zip(LABELS, thr))}")
    n_set = 0
    total_elig = 0
    for n in range(1, 101):
        ip = os.path.join(ROOT, "input", f"{n}.txt")
        content = open(ip, encoding="utf-8").read() if os.path.exists(ip) else ""
        arr = json.load(open(os.path.join(ROOT, args.src, f"{n}.json"), encoding="utf-8"))
        # thu thập entity đủ điều kiện
        idxs, windows = [], []
        for i, e in enumerate(arr):
            if e.get("type") not in ELIGIBLE:
                continue
            p = e.get("position")
            if not (isinstance(p, list) and len(p) == 2 and content[p[0]:p[1]] == e.get("text")):
                continue
            idxs.append(i)
            windows.append(make_window(content, p[0], p[1]))
        total_elig += len(idxs)
        # batch predict
        for b in range(0, len(windows), 32):
            chunk = windows[b:b + 32]
            enc = tok(chunk, truncation=True, max_length=256, padding=True, return_tensors="pt").to(dev)
            with torch.no_grad():
                probs = torch.sigmoid(model(**enc).logits).cpu().numpy()
            for j, pr in enumerate(probs):
                labs = [LABELS[k] for k in range(len(LABELS)) if pr[k] >= thr[k]]
                e = arr[idxs[b + j]]
                if args.replace:
                    if labs != (e.get("assertions") or []):
                        e["assertions"] = labs
                        n_set += 1
                elif not e.get("assertions") and labs:   # MERGE: chỉ điền chỗ trống
                    e["assertions"] = labs
                    n_set += 1
        json.dump(arr, open(os.path.join(dst, f"{n}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    print(f"eligible entities: {total_elig} | assertions changed on: {n_set} | -> {dst}/")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
