#!/usr/bin/env python3
"""Build the assertion-classification dataset from a BASE EXTRACTION with assertion labels.

For each assertion-eligible entity (CHẨN_ĐOÁN/THUỐC/TRIỆU_CHỨNG) -> one example:
  {"text": <context window «entity»>, "labels": [isNegated, isHistorical]}
isFamily is EXCLUDED (proven net-negative on the leaderboard). Context window = 300 chars before +
80 after, entity marked with « ».  Splits val = file_id % 5 == 0.

Usage:
  python source/ner/build_assertion_dataset.py --base data/base_extractions/wer591 --out data/silver/assertions
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
ELIGIBLE = {"CHẨN_ĐOÁN", "THUỐC", "TRIỆU_CHỨNG"}
LABELS = ["isNegated", "isHistorical"]
PRE, POST = 300, 80


def window(content, s, e):
    return content[max(0, s - PRE):s] + " « " + content[s:e] + " » " + content[e:min(len(content), e + POST)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--input", default="input")
    ap.add_argument("--out", default="data/silver/assertions")
    args = ap.parse_args()

    base = os.path.join(ROOT, args.base) if not os.path.isabs(args.base) else args.base
    inp = os.path.join(ROOT, args.input)
    out = os.path.join(ROOT, args.out)
    os.makedirs(out, exist_ok=True)

    train, val, pos = [], [], 0
    for n in range(1, 101):
        ip, bp = os.path.join(inp, f"{n}.txt"), os.path.join(base, f"{n}.json")
        if not (os.path.exists(ip) and os.path.exists(bp)):
            continue
        content = open(ip, encoding="utf-8").read()
        for e in json.load(open(bp, encoding="utf-8")):
            if e.get("type") not in ELIGIBLE:
                continue
            p = e.get("position")
            if not (isinstance(p, list) and len(p) == 2 and content[p[0]:p[1]] == e.get("text")):
                continue
            a = set(e.get("assertions") or [])
            vec = [1 if l in a else 0 for l in LABELS]
            if any(vec):
                pos += 1
            rec = {"file": n, "text": window(content, p[0], p[1]), "labels": vec}
            (val if n % 5 == 0 else train).append(rec)

    for name, data in [("train", train), ("val", val)]:
        with open(os.path.join(out, f"{name}.jsonl"), "w", encoding="utf-8") as f:
            for r in data:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"assertion dataset: train={len(train)} val={len(val)}, {pos} positive -> {out}/")


if __name__ == "__main__":
    main()
