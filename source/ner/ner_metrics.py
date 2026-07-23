#!/usr/bin/env python3
"""Entity-level P/R/F1 từ chuỗi nhãn BIO (thay seqeval — không phụ thuộc build ngoài).
Một entity = (start, end, type) trích từ B-.. / I-.. liên tiếp cùng type."""


def extract(seq):
    ents, cur = set(), None
    for i, lab in enumerate(seq + ["O"]):
        if lab.startswith("B-"):
            if cur:
                ents.add(cur)
            cur = (i, i + 1, lab[2:])
        elif lab.startswith("I-") and cur and lab[2:] == cur[2]:
            cur = (cur[0], i + 1, cur[2])
        else:
            if cur:
                ents.add(cur)
            cur = None
    return ents


def _counts(true_seqs, pred_seqs):
    tp = fp = fn = 0
    per = {}
    for t, p in zip(true_seqs, pred_seqs):
        te, pe = extract(t), extract(p)
        tp += len(te & pe); fp += len(pe - te); fn += len(te - pe)
        for kind, s in (("t", te), ("p", pe)):
            for (_, _, ty) in s:
                per.setdefault(ty, {"t": 0, "p": 0, "tp": 0})[kind] += 1
        for e in te & pe:
            per.setdefault(e[2], {"t": 0, "p": 0, "tp": 0})["tp"] += 1
    return tp, fp, fn, per


def _prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def score(true_seqs, pred_seqs):
    tp, fp, fn, _ = _counts(true_seqs, pred_seqs)
    p, r, f = _prf(tp, fp, fn)
    return {"precision": p, "recall": r, "f1": f}


def report(true_seqs, pred_seqs, digits=4):
    tp, fp, fn, per = _counts(true_seqs, pred_seqs)
    lines = [f"{'type':22} {'prec':>8} {'rec':>8} {'f1':>8} {'support':>8}"]
    for ty in sorted(per):
        d = per[ty]
        p, r, f = _prf(d["tp"], d["p"] - d["tp"], d["t"] - d["tp"])
        lines.append(f"{ty:22} {p:8.{digits}f} {r:8.{digits}f} {f:8.{digits}f} {d['t']:8}")
    p, r, f = _prf(tp, fp, fn)
    lines.append(f"{'MICRO-AVG':22} {p:8.{digits}f} {r:8.{digits}f} {f:8.{digits}f} {tp+fn:8}")
    return "\n".join(lines)
