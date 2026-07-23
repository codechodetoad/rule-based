#!/usr/bin/env python3
"""Phase 2 (Route B) — fine-tune XLM-RoBERTa token-classifier cho NER y khoa VN+EN.

Vào : source/ner/data/{train,val}.jsonl  (char-span labels, từ build_dataset.py)
Ra  : source/ner/model/  (weights + config + nhãn) — dùng cho infer_ner.py

Điểm mấu chốt: XLM-R chạy TRÊN VĂN BẢN GỐC, offset_mapping cho phép gán span ký-tự -> subword
CHÍNH XÁC, và ánh xạ ngược lúc infer cũng chính xác (không cần word-seg -> position khớp tuyệt đối).
Văn bản dài -> cửa sổ trượt (max_length, stride, overflowing_tokens).

Chạy: python source/ner/train_ner.py            (mặc định xlm-roberta-base, 12 epoch)
      python source/ner/train_ner.py --model xlm-roberta-base --epochs 15 --lr 3e-5
"""
import argparse
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TYPES = ["CHẨN_ĐOÁN", "TRIỆU_CHỨNG", "THUỐC", "TÊN_XÉT_NGHIỆM", "KẾT_QUẢ_XÉT_NGHIỆM"]
LABELS = ["O"] + [f"{p}-{t}" for t in TYPES for p in ("B", "I")]
L2I = {l: i for i, l in enumerate(LABELS)}
I2L = {i: l for l, i in L2I.items()}


def load_jsonl(name, data_dir="data"):
    fp = os.path.join(HERE, data_dir, f"{name}.jsonl")
    return [json.loads(l) for l in open(fp, encoding="utf-8")]


def encode(records, tokenizer, max_len, stride):
    """Mỗi record -> nhiều cửa sổ; gán nhãn BIO theo offset_mapping (toạ độ ký-tự GỐC)."""
    feats = []
    for r in records:
        text, ents = r["text"], sorted(r["entities"])
        enc = tokenizer(text, truncation=True, max_length=max_len, stride=stride,
                        return_overflowing_tokens=True, return_offsets_mapping=True, padding=False)
        for win_ids, win_off in zip(enc["input_ids"], enc["offset_mapping"]):
            labels, prev = [], None
            for (a, b) in win_off:
                if a == b:                       # token đặc biệt / rỗng
                    labels.append(-100); prev = None; continue
                hit = None
                for idx, (s, e, ty) in enumerate(ents):
                    if a < e and s < b:          # subword chồng lấn entity
                        hit = (idx, ty); break
                if hit is None:
                    labels.append(L2I["O"]); prev = None
                else:
                    idx, ty = hit
                    labels.append(L2I[f"I-{ty}"] if prev == idx else L2I[f"B-{ty}"])
                    prev = idx
            feats.append({"input_ids": win_ids,
                          "attention_mask": [1] * len(win_ids),
                          "labels": labels})
    return feats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="xlm-roberta-base")
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--lr", type=float, default=3e-5)
    ap.add_argument("--max-len", type=int, default=512)
    ap.add_argument("--stride", type=int, default=128)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--out", default=os.path.join(HERE, "model"))
    ap.add_argument("--data-dir", default="data", help="thư mục con chứa train.jsonl/val.jsonl")
    args = ap.parse_args()

    import torch
    from datasets import Dataset
    from transformers import (AutoTokenizer, AutoModelForTokenClassification,
                              DataCollatorForTokenClassification, TrainingArguments, Trainer)
    import ner_metrics

    tok = AutoTokenizer.from_pretrained(args.model)
    train = Dataset.from_list(encode(load_jsonl("train", args.data_dir), tok, args.max_len, args.stride))
    val = Dataset.from_list(encode(load_jsonl("val", args.data_dir), tok, args.max_len, args.stride))
    print(f"windows: train={len(train)} val={len(val)}   labels={len(LABELS)}")

    model = AutoModelForTokenClassification.from_pretrained(
        args.model, num_labels=len(LABELS), id2label=I2L, label2id=L2I,
        ignore_mismatched_sizes=True)   # cho phép init từ checkpoint Stage-A (head 18-type khác cỡ)
    collator = DataCollatorForTokenClassification(tok)

    def metrics(p):
        preds = np.argmax(p.predictions, axis=2)
        tp, tl = [], []
        for pr, la in zip(preds, p.label_ids):
            tp.append([I2L[x] for x, y in zip(pr, la) if y != -100])
            tl.append([I2L[y] for x, y in zip(pr, la) if y != -100])
        return ner_metrics.score(tl, tp)

    targs = TrainingArguments(
        output_dir=args.out, num_train_epochs=args.epochs, learning_rate=args.lr,
        per_device_train_batch_size=args.batch, per_device_eval_batch_size=16,
        eval_strategy="epoch", save_strategy="epoch", load_best_model_at_end=True,
        metric_for_best_model="f1", greater_is_better=True, save_total_limit=1,
        fp16=torch.cuda.is_available(), logging_steps=20, report_to="none", seed=42)
    tr = Trainer(model=model, args=targs, train_dataset=train, eval_dataset=val,
                 data_collator=collator, compute_metrics=metrics)
    tr.train()

    # báo cáo entity-level trên val
    pred = tr.predict(val)
    preds = np.argmax(pred.predictions, axis=2)
    tp, tl = [], []
    for pr, la in zip(preds, pred.label_ids):
        tp.append([I2L[x] for x, y in zip(pr, la) if y != -100])
        tl.append([I2L[y] for x, y in zip(pr, la) if y != -100])
    print("\n=== VAL entity-level ===")
    print(ner_metrics.report(tl, tp, digits=4))

    tr.save_model(args.out)
    tok.save_pretrained(args.out)
    json.dump({"labels": LABELS, "types": TYPES, "max_len": args.max_len, "stride": args.stride},
              open(os.path.join(args.out, "ner_meta.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\n-> lưu model: {args.out}")


if __name__ == "__main__":
    main()
