#!/usr/bin/env python3
"""P1 — train ASSERTION multi-label classifier (XLM-R) trên data_assert/.

3 nhãn sigmoid độc lập [isNegated, isHistorical, isFamily]. isFamily gần như không có mẫu (2) ->
model sẽ hầu như không kích hoạt (chấp nhận: isFamily hiếm trong GT). Ưu tiên ĐỘ CHÍNH XÁC lúc
infer bằng ngưỡng cao (apply_assertion.py) — sai assertion biến ∅-vs-∅=1 thành 0.

Chạy: python source/ner/train_assertion.py --epochs 15 --out source/ner/model_assert
"""
import argparse
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
LABELS = ["isNegated", "isHistorical"]


ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DATA_DIR = os.path.join(ROOT, "data", "silver", "assertions")   # overridden by --data-dir


def load_jsonl(name):
    fp = os.path.join(DATA_DIR, f"{name}.jsonl")
    return [json.loads(l) for l in open(fp, encoding="utf-8")]


def main():
    global DATA_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=DATA_DIR, help="folder with train.jsonl/val.jsonl")
    ap.add_argument("--model", default="xlm-roberta-base")
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--max-len", type=int, default=256)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--out", default=os.path.join(HERE, "model_assert"))
    args = ap.parse_args()
    DATA_DIR = args.data_dir

    import torch
    from datasets import Dataset
    from transformers import (AutoTokenizer, AutoModelForSequenceClassification,
                              DataCollatorWithPadding, TrainingArguments, Trainer)

    tok = AutoTokenizer.from_pretrained(args.model)

    def encode(rows):
        out = []
        for r in rows:
            enc = tok(r["text"], truncation=True, max_length=args.max_len)
            enc["labels"] = [float(x) for x in r["labels"]]
            out.append(enc)
        return out

    train = Dataset.from_list(encode(load_jsonl("train")))
    val = Dataset.from_list(encode(load_jsonl("val")))
    print(f"train={len(train)} val={len(val)}  labels={LABELS}")

    model = AutoModelForSequenceClassification.from_pretrained(
        args.model, num_labels=len(LABELS), problem_type="multi_label_classification",
        id2label={i: l for i, l in enumerate(LABELS)},
        label2id={l: i for i, l in enumerate(LABELS)})
    collator = DataCollatorWithPadding(tok)

    def metrics(p):
        probs = 1 / (1 + np.exp(-p.predictions))
        preds = (probs >= 0.5).astype(int)
        gold = p.label_ids.astype(int)
        res = {}
        for i, l in enumerate(LABELS):
            tp = int(((preds[:, i] == 1) & (gold[:, i] == 1)).sum())
            fp = int(((preds[:, i] == 1) & (gold[:, i] == 0)).sum())
            fn = int(((preds[:, i] == 0) & (gold[:, i] == 1)).sum())
            prec = tp / (tp + fp) if tp + fp else 0.0
            rec = tp / (tp + fn) if tp + fn else 0.0
            res[f"{l}_p"] = round(prec, 3)
            res[f"{l}_r"] = round(rec, 3)
        res["f1_micro"] = round(np.mean([res[f"{l}_p"] for l in LABELS[:2]]), 3)
        return res

    targs = TrainingArguments(
        output_dir=args.out, num_train_epochs=args.epochs, learning_rate=args.lr,
        per_device_train_batch_size=args.batch, per_device_eval_batch_size=32,
        eval_strategy="epoch", save_strategy="epoch", load_best_model_at_end=True,
        metric_for_best_model="f1_micro", greater_is_better=True, save_total_limit=1,
        fp16=torch.cuda.is_available(), logging_steps=20, report_to="none", seed=42)
    tr = Trainer(model=model, args=targs, train_dataset=train, eval_dataset=val,
                 data_collator=collator, compute_metrics=metrics)
    tr.train()
    print("\n=== VAL final ===")
    print(tr.evaluate())
    tr.save_model(args.out)
    tok.save_pretrained(args.out)
    json.dump({"labels": LABELS, "max_len": args.max_len, "pre": 300, "post": 80},
              open(os.path.join(args.out, "assert_meta.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"-> {args.out}")


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    main()
