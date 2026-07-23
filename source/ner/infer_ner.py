#!/usr/bin/env python3
"""Phase 3 (Route B) — infer XLM-R NER trên input/ -> output_ner/ (span+type, offset ký-tự CHÍNH XÁC).

Cửa sổ trượt (như train) -> giải BIO -> span ký-tự qua offset_mapping -> gộp trùng giữa cửa sổ.
KHÔNG gán assertions/candidates ở đây (Phase 4: ConText luật + RxNorm/ICD retrieval).

Chạy: python source/ner/infer_ner.py                 # -> output_ner/
      python source/ner/infer_ner.py --model source/ner/model --dst output_ner
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


_LEAD = " \t\n\r,.;:)]}"      # ký tự KHÔNG được mở đầu entity (dấu đóng/phân cách lạc)
_TRAIL = " \t\n\r,.;:-–—([{"   # ký tự KHÔNG được kết thúc entity (dấu mở/phân cách lạc)


def trim_punct(content, s, e):
    """Cắt dấu câu/khoảng trắng lạc ở biên + cân bằng ngoặc (giữ offset khớp input)."""
    while s < e and content[s] in _LEAD:
        s += 1
    while e > s and content[e - 1] in _TRAIL:
        e -= 1
    # ngoặc lệch: bỏ ')' đuôi nếu thiếu '(' trong span; bỏ '(' đuôi nếu thiếu ')'
    while e > s:
        span = content[s:e]
        if span.endswith(")") and span.count("(") < span.count(")"):
            e -= 1
        elif span.endswith("(") and span.count(")") < span.count("("):
            e -= 1
        else:
            break
        while e > s and content[e - 1] in _TRAIL:
            e -= 1
    return s, e


def decode_window(labels, offsets, confs):
    """BIO + offset_mapping (toạ độ gốc) -> list (start,end,type,min_conf).
    min_conf = độ tin cậy THẤP NHẤT trong các subword của entity (dùng để lọc precision)."""
    ents, cur = [], None
    for lab, (a, b), cf in zip(labels, offsets, confs):
        if a == b:                       # token đặc biệt
            if cur:
                ents.append(cur); cur = None
            continue
        if lab.startswith("B-"):
            if cur:
                ents.append(cur)
            cur = [a, b, lab[2:], cf]
        elif lab.startswith("I-") and cur and lab[2:] == cur[2]:
            cur[1] = b; cur[3] = min(cur[3], cf)
        else:
            if cur:
                ents.append(cur); cur = None
    if cur:
        ents.append(cur)
    return ents


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.path.join(HERE, "model"))
    ap.add_argument("--dst", default="output_ner")
    ap.add_argument("--src", default="input")
    ap.add_argument("--min-prob", type=float, default=0.0,
                    help="lọc precision: bỏ entity có min-conf < ngưỡng (0=giữ hết)")
    ap.add_argument("--dedup", action="store_true",
                    help="bỏ mention (text,type) lặp lại trong 1 file (giữ lần đầu)")
    args = ap.parse_args()

    import torch
    from transformers import AutoTokenizer, AutoModelForTokenClassification

    meta = json.load(open(os.path.join(args.model, "ner_meta.json"), encoding="utf-8"))
    max_len, stride = meta["max_len"], meta["stride"]
    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForTokenClassification.from_pretrained(args.model)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device).eval()
    id2label = model.config.id2label

    dst = os.path.join(ROOT, args.dst)
    os.makedirs(dst, exist_ok=True)
    total = 0
    for n in range(1, 101):
        ip = os.path.join(ROOT, args.src, f"{n}.txt")
        if not os.path.exists(ip):
            continue
        content = open(ip, encoding="utf-8").read()
        enc = tok(content, truncation=True, max_length=max_len, stride=stride,
                  return_overflowing_tokens=True, return_offsets_mapping=True,
                  return_tensors="pt", padding=True)
        offs = enc.pop("offset_mapping").tolist()
        enc.pop("overflow_to_sample_mapping", None)
        with torch.no_grad():
            probs = model(**{k: v.to(device) for k, v in enc.items()}).logits.softmax(-1)
        conf_t, pred_t = probs.max(-1)
        preds, confs = pred_t.tolist(), conf_t.tolist()
        seen, ents = set(), []
        for win_pred, win_off, win_conf in zip(preds, offs, confs):
            labels = [id2label[i] for i in win_pred]
            for s, e, ty, mc in decode_window(labels, win_off, win_conf):
                if mc < args.min_prob:                # lọc precision theo độ tin cậy
                    continue
                s2, e2 = trim_punct(content, s, e)   # cắt dấu câu/khoảng trắng lạc, cân bằng ngoặc
                if e2 <= s2 or not content[s2:e2].strip():
                    continue
                k = (s2, e2, ty)
                if k in seen:
                    continue
                seen.add(k)
                ents.append({"text": content[s2:e2], "type": ty,
                             "candidates": [], "assertions": [], "position": [s2, e2]})
        # dedup mention lặp trong 1 file (giữ lần đầu theo position)
        if args.dedup:
            seen2, dd = set(), []
            for e in sorted(ents, key=lambda x: x["position"][0]):
                if len(e["text"].strip()) <= 1:
                    continue
                key = (e["text"].strip().lower(), e["type"])
                if key in seen2:
                    continue
                seen2.add(key); dd.append(e)
            ents = dd
        ents.sort(key=lambda x: x["position"][0])
        json.dump(ents, open(os.path.join(dst, f"{n}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        total += len(ents)
    print(f"{args.dst}/: 100 file, {total} entity (span+type)")


if __name__ == "__main__":
    main()
