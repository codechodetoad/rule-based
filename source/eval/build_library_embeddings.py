#!/usr/bin/env python3
"""Phase 2 (RAG linking) — nhúng (embed) TOÀN BỘ thư viện ICD-10/RxNorm 1 lần, cache ra đĩa.

Nguồn: library/diseases.json (31401, tên->mã ICD) + library/drug_synonyms.json (36157, tên->RxCUI).
Bỏ library/drugs.json (48030 tên hoạt chất hoá học thô, phần lớn không xuất hiện trong văn bản
tự nhiên — drug_synonyms.json là danh mục TÊN THƯỜNG DÙNG, khớp thực tế tốt hơn).

Model nhúng: Qwen3-Embedding-4B qua ViettelAI (OpenAI-compatible), batch 1000 (~28-36 mục/giây).
Ghi tăng dần ra .npz (resumable — chạy lại bỏ qua phần đã xong).

Chạy: python source/eval/build_library_embeddings.py
"""
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT_DIR = os.path.join(ROOT, "library", "embeddings")
BATCH = 1000
MODEL = "Qwen3-Embedding-4B"
BASE_URL = "https://viettelai.vn/model-as-a-service"


def load_key():
    p = os.path.join(ROOT, ".env")
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line.startswith("VIETTELAI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.getenv("VIETTELAI_API_KEY")


def embed_dict(client, src_json, out_npz):
    d = json.load(open(os.path.join(ROOT, "library", src_json), encoding="utf-8"))
    names = list(d.keys())
    codes = [d[n] for n in names]

    done_vecs = []
    start = 0
    if os.path.exists(out_npz):
        cached = np.load(out_npz, allow_pickle=True)
        if list(cached["names"]) == names[:len(cached["names"])]:
            done_vecs = list(cached["vecs"])
            start = len(done_vecs)
            print(f"  {src_json}: resume từ {start}/{len(names)}")

    vecs = done_vecs
    t0 = time.time()
    for i in range(start, len(names), BATCH):
        batch = names[i:i + BATCH]
        for attempt in range(4):
            try:
                r = client.embeddings.create(input=batch, model=MODEL, encoding_format="float")
                vecs.extend(e.embedding for e in r.data)
                break
            except Exception as e:
                print(f"    batch {i}: attempt {attempt+1} failed: {type(e).__name__}: {str(e)[:150]}")
                time.sleep(3)
        else:
            raise RuntimeError(f"batch {i} failed after retries")
        elapsed = time.time() - t0
        done = i + len(batch)
        print(f"  {src_json}: {done}/{len(names)}  ({elapsed:.0f}s, {done/max(elapsed,0.01):.1f}/s)")
        np.savez(out_npz, vecs=np.array(vecs, dtype=np.float32),
                  names=np.array(names[:len(vecs)], dtype=object),
                  codes=np.array(codes[:len(vecs)], dtype=object))
    print(f"  {src_json}: DONE -> {out_npz}  ({len(vecs)} vectors)")


def main():
    import openai
    os.makedirs(OUT_DIR, exist_ok=True)
    client = openai.OpenAI(api_key=load_key(), base_url=BASE_URL)
    embed_dict(client, "diseases.json", os.path.join(OUT_DIR, "diseases.npz"))
    embed_dict(client, "drug_synonyms.json", os.path.join(OUT_DIR, "drug_synonyms.npz"))
    print("ALL DONE")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
