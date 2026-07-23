#!/usr/bin/env python3
"""Build the token-classification (entity NER) dataset from a BASE EXTRACTION.

A "base extraction" is a folder of N.json files (list of {text,type,position}) — the silver labels
we distill into the XLM-R entity model. Reads input/N.txt to keep only spans whose position matches
the input text exactly (char-offset preserved). Splits val = file_id % 5 == 0.

Usage:
  python source/ner/build_entity_dataset.py --base data/base_extractions/wer591 --out data/silver/entities
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
TYPES = ["CHẨN_ĐOÁN", "TRIỆU_CHỨNG", "THUỐC", "TÊN_XÉT_NGHIỆM", "KẾT_QUẢ_XÉT_NGHIỆM"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="folder of base-extraction N.json files")
    ap.add_argument("--input", default="input", help="folder of input N.txt files")
    ap.add_argument("--out", default="data/silver/entities")
    args = ap.parse_args()

    base = os.path.join(ROOT, args.base) if not os.path.isabs(args.base) else args.base
    inp = os.path.join(ROOT, args.input)
    out = os.path.join(ROOT, args.out)
    os.makedirs(out, exist_ok=True)

    train, val, dropped = [], [], 0
    for n in range(1, 101):
        ip = os.path.join(inp, f"{n}.txt")
        bp = os.path.join(base, f"{n}.json")
        if not (os.path.exists(ip) and os.path.exists(bp)):
            continue
        content = open(ip, encoding="utf-8").read()
        spans, seen = [], set()
        for e in json.load(open(bp, encoding="utf-8")):
            ty, p = e.get("type"), e.get("position")
            if ty not in TYPES or not (isinstance(p, list) and len(p) == 2
                                       and 0 <= p[0] < p[1] <= len(content)
                                       and content[p[0]:p[1]] == e.get("text")):
                dropped += 1
                continue
            k = (p[0], p[1], ty)
            if k in seen:
                continue
            seen.add(k)
            spans.append([p[0], p[1], ty])
        rec = {"file": n, "text": content, "entities": sorted(spans)}
        (val if n % 5 == 0 else train).append(rec)

    for name, data in [("train", train), ("val", val)]:
        with open(os.path.join(out, f"{name}.jsonl"), "w", encoding="utf-8") as f:
            for r in data:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"entity dataset: train={len(train)} val={len(val)} files, dropped {dropped} bad spans -> {out}/")


if __name__ == "__main__":
    main()
