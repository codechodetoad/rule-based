#!/usr/bin/env python3
"""Thuốc (RxNorm, phần EN) — RxNav 'allconcepts' bulk API -> library/drugs.json

Dùng endpoint public /REST/allconcepts.json?tty=... (KHÔNG cần UMLS license/RRF,
mỗi TTY chỉ 1 request, trả về toàn bộ concept). Lấy 5 TTY:
  IN  = Ingredient (hoạt chất generic, vd "amlodipine")
  PIN = Precise Ingredient (dạng muối/ester cụ thể, vd "amlodipine besylate")
  BN  = Brand Name (tên biệt dược, vd "Lipitor")
  SCD = Semantic Clinical Drug (hoạt chất + liều lượng + dạng bào chế,
        vd "amlodipine 5 MG Oral Tablet")
  SBD = Semantic Branded Drug (biệt dược + liều lượng + dạng bào chế,
        vd "Lipitor 10 MG Oral Tablet")

Output format giống output_drugs (bỏ type/position/assertions -> file nhẹ hơn,
"type" là hằng số THUỐC cho cả file nên không cần lặp lại mỗi entry):
  {"text": "amlodipine", "candidates": ["17767"]}

Chạy: python library/build/build_rxnorm_lib.py
"""
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CACHE_DIR = os.path.join(ROOT, "library", "build", "_cache")
OUT = os.path.join(ROOT, "library", "drugs.json")
BASE = "https://rxnav.nlm.nih.gov/REST/allconcepts.json"
TTYS = ["IN", "PIN", "BN", "SCD", "SBD"]


def fetch_tty(tty):
    cache_fp = os.path.join(CACHE_DIR, f"rxnorm_{tty}.json")
    if os.path.exists(cache_fp):
        with open(cache_fp, encoding="utf-8") as f:
            return json.load(f)
    url = f"{BASE}?tty={tty}"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                data = json.load(resp)
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(cache_fp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            return data
        except Exception as e:
            print(f"  lỗi tải {tty} (thử {attempt+1}/3): {e}", file=sys.stderr)
            time.sleep(2)
    raise RuntimeError(f"Không tải được TTY={tty}")


def main():
    entries = []
    seen = set()  # (name_lower, rxcui)
    per_tty_count = {}

    for tty in TTYS:
        print(f"Tải {tty} ...")
        data = fetch_tty(tty)
        concepts = data.get("minConceptGroup", {}).get("minConcept", [])
        per_tty_count[tty] = len(concepts)
        for c in concepts:
            name = (c.get("name") or "").strip()
            rxcui = (c.get("rxcui") or "").strip()
            if not name or not rxcui:
                continue
            key = (name.lower(), rxcui)
            if key in seen:
                continue
            seen.add(key)
            entries.append({
                "text": name,
                "candidates": [rxcui],
            })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    print()
    for tty, n in per_tty_count.items():
        print(f"  {tty}: {n} concept")
    print(f"Ghi {len(entries)} entry -> {OUT}")


if __name__ == "__main__":
    main()
