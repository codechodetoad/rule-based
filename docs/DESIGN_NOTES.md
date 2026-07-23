# FINAL ARCHITECTURE — ViettelRace 2026 medical NER (new-input phase)

> Authoritative description of the CURRENT best solution. Written 2026-07-23.
> Best score: **35.8131** (`submission_wer591d.zip`), fully reproducible ≤9B.
> Other `architecture/*.md` are historical (old-input phase). `NEW_INPUT_PLAN.md` (repo root) is the
> running probe log with every leaderboard result.

---

## 1. Problem & metric

Extract medical concepts from free-form Vietnamese (code-switched VN+EN) clinical/patient text.
Per input `N.txt` → `N.json` = list of `{text, type, position[start,end], assertions[], candidates[]}`.

- **5 types:** TRIỆU_CHỨNG (symptom), TÊN_XÉT_NGHIỆM (test name), KẾT_QUẢ_XÉT_NGHIỆM (test result),
  CHẨN_ĐOÁN (diagnosis), THUỐC (drug).
- **assertions** (CHẨN_ĐOÁN/THUỐC/TRIỆU_CHỨNG only): isNegated, isHistorical, isFamily.
- **candidates** (CHẨN_ĐOÁN→ICD-10, THUỐC→RxNorm only).

**Score = 0.3·text + 0.3·assert + 0.4·cand**, where
`text = 1 − WER` (word error rate on concatenated entity text),
`assert = mean per-entity Jaccard(GT.assert, pred.assert)` (aligned by char-overlap),
`cand = weighted Jaccard of code sets`, weight = `len(GT.cand)+1`.
**Critical rule: `∅ vs ∅ → J=1`, `∅ vs non-∅ → J=0`** ⇒ predicting a code/assertion where GT is
empty COSTS a free point. Over-prediction is actively negative. Wrong `type` = counted twice, 0 on
all three axes.

**We have NO ground truth** for this input. Only the leaderboard scores. `recovered_gt/` (26
old-label files) was proven ANTI-correlated with the current grader — there is no offline signal,
so every idea costs a submission and probe discipline is mandatory.

---

## 2. The winning pipeline (end-to-end)

```
input/N.txt
    │
    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ (A) ENTITY EXTRACTION  — XLM-RoBERTa-base token classifier (~270M)    │
│     model_wer591 (distilled from the wer591 base extraction)         │
│     → span + type + exact char offset          [output_ner_wer591]   │
└─────────────────────────────────────────────────────────────────────┘
    │                                   │
    ▼ (B) CANDIDATE LINKING             ▼ (C) ASSERTION CLASSIFICATION
┌──────────────────────────────┐  ┌──────────────────────────────────┐
│ graft --no-recall            │  │ model_assert_betacc (~270M XLM-R) │
│  · rule code policy (luong1/ │  │  multi-label: isNegated,          │
│    luong2): drug resolver,   │  │  isHistorical (isFamily DROPPED,  │
│    ICD leaf/tech-suffix,      │  │  proven net-negative)             │
│    overcoding fix, de-        │  │  context window ±(300/80) chars,  │
│    overcode bare comorbidity │  │  threshold 0.5, REPLACE mode      │
│  · rag_link @0.82:           │  └──────────────────────────────────┘
│    Qwen3-Embedding-4B dense  │                    │
│    retrieval over ICD(31k)/  │                    │
│    RxNorm(48k), sim≥0.82 +   │                    │
│    margin≥0.03 (drugs: sim-  │                    │
│    only, no margin)          │                    │
└──────────────────────────────┘                    │
    │                                                │
    └───────────────────► merge (same entity set) ◄──┘
                                │
                                ▼
                    validate (text==input[s:e], schema)  →  N.json
```

**All three trained components are XLM-RoBERTa-base (~270M each). Qwen3-Embedding-4B (4B) is used
only for candidate retrieval. Total ≪ 9B, self-hostable.** No generative LLM in the final pipeline.

---

## 3. Components in detail

### (A) Entity extraction — `source/ner/`
- **Model:** XLM-RoBERTa-base fine-tuned for token classification (BIO tags over 5 types).
  `train_ner.py` (sliding window max_len 512/stride 128, offset_mapping → exact char spans).
- **Training data = silver.** We have no GT, so we distill the best available extraction into the
  model. Progression of silver sources (each better than the last):
  - `model_v2` ← old-genre silver (`data_v2/`), val F1 0.625
  - `model_betacc` ← `bet_acc` base labels (`data_betacc/`), val F1 0.698
  - **`model_wer591` ← `wer591` base labels (`data_wer591/`), val F1 0.735 — CURRENT**
- **Why distillation helps:** the trained XLM-R GENERALIZES the base extraction — its spans align to
  GT slightly better than the raw base (measured: distilled beat raw on text AND assertion axes,
  e.g. wer591d 35.81 > raw wer591_clf 35.47). It also makes the base reproducible/≤9B.
- Inference: `infer_ner.py --model model_wer591 --min-prob 0` → `output_ner_wer591` (span+type only).

### (B) Candidate linking — `source/generate_all.py` (graft) + `source/eval/rag_link.py`
- **Rule code policy** (ported from `luong1/extract_drugs.py`, `luong2/extract_findings.py`):
  drug name→RxCUI resolver (curated + library SCD dose-match + cache), ICD leaf codes for
  tech-suffix/specified diagnoses, `NO_CODE` list (drug classes, infusions, glued spans),
  overcoding fix (CKD staging regex, bare-comorbidity strip — validated wins).
  Run via `graft(entities_dir, dst)` with `DISABLE_RECALL=True` (entity-recall proven harmful).
- **Dense RAG** (`rag_link.py`): embed each still-uncoded CHẨN_ĐOÁN/THUỐC with **Qwen3-Embedding-4B**
  (2560-dim), cosine vs pre-embedded `library/diseases.json` (31401 ICD) + `drug_synonyms.json`
  (36157 RxNorm), cached in `library/embeddings/*.npz`. Gate: diseases sim≥0.82 AND margin≥0.03
  (ambiguity guard); drugs sim≥0.87 sim-only (dose-form multiplicity ≠ ambiguity). Fill gaps only,
  never overwrite. Length/history-prefix/diabetes-type2 filters for precision.
- **Candidate is the score bottleneck** (0.4 weight, stuck ~24-25). Decomposition proved ~+15 room
  exists (floor 18.12 → ceiling ~40, disease-led) but it is CONVENTION-GATED — every blind mechanism
  (lower threshold, drug recall, hedging, code union) over-codes and loses. Needs an offline dev set.

### (C) Assertion classification — `source/ner/model_assert_betacc`
- **Model:** XLM-RoBERTa-base multi-label sequence classifier (2 labels: isNegated, isHistorical).
  isFamily DROPPED — decomposition proved bet_acc's family predictions net-negative (−0.17).
- **Training:** `train_assertion_betacc.py` on `data_assert_betacc/` (bet_acc's 100-file assertion
  labels, 1958 examples). Input = context window `…«entity»…` (±300/80 chars). Val: **isNegated
  precision 0.95, isHistorical 0.72, f1 0.86.**
- **Apply:** `apply_assertion.py --replace --thresh 0.5` — REPLACE the base's assertions entirely.
  This was the biggest single breakthrough: the classifier's assertions BEAT the raw base's own
  labels by +1.68 (v14) / +0.83 (wer591d), because it generalizes/smooths the base's assertion noise
  and captures more of GT's assertions at high precision (the low-precision LLM approach v11 lost).

---

## 4. Score progression (leaderboard, this phase)

| milestone | text | assert | cand | FINAL | what changed |
|---|---|---|---|---|---|
| baseline (token-classifier) | 34.80 | 36.40 | 20.40 | 29.52 | starting point |
| v3 | 36.98 | 40.38 | 22.54 | 32.23 | fine-tune + overcoding bug-fix |
| v5 | 36.98 | 40.38 | 23.15 | 32.47 | + RAG candidate linking |
| v8 (bet_acc base) | 39.22 | 43.24 | 24.89 | 34.70 | user-provided better base + our pipeline |
| stripfam | 39.22 | 43.37 | 24.89 | 34.73 | drop isFamily (net-negative) |
| v14 (distilled bet_acc) | 39.71 | 45.05 | 24.34 | 35.16 | distill + assertion CLASSIFIER (+1.68 assert) |
| wer591_clf (wer591 base) | 41.17 | 44.97 | 24.07 | 35.47 | stronger entity base (better text) |
| **wer591d (distilled wer591)** | **41.29** | **45.80** | **24.21** | **35.8131** | distill wer591 → best & reproducible |

Session arc: **29.52 → 35.8131 (+6.29).**

**What LOST (do not retry — all in NEW_INPUT_PLAN.md):** LLM extraction (frontier & local, v2/v3),
blanket rule pipeline (v4), rule entity-recall (v6), drug-code recall (v10), disease hedging (v12),
bet_acc code union (v13), text-entity removal (notests/dedup), LLM assertions (v11), candidate
threshold below 0.82, assertion threshold below 0.5. Pattern: LEARNING from the genre / calibration
wins; IMPOSING predictions beyond the well-calibrated base over-predicts and loses (∅-vs-∅ tax).

---

## 5. Reproducibility & ≤9B compliance

Fully self-hostable, no external API required at inference (embeddings can be self-hosted):
- `source/ner/model_wer591` — XLM-R entity NER (~270M)
- `source/ner/model_assert_betacc` — XLM-R assertion classifier (~270M)
- `Qwen3-Embedding-4B` — candidate retrieval (4B; currently via ViettelAI API, self-hostable for
  the private rebuild). Library embeddings cached in `library/embeddings/`.
- Rule assets: `library/*.json`, `source/luong1`, `source/luong2` lexicons.

Rebuild from a base extraction `B/`:
```
# 1. distill entities
python -c "build data_B from B labels"            # see data_wer591 build in NEW_INPUT_PLAN.md
python source/ner/train_ner.py --model source/ner/model_betacc --data-dir data_B --out model_B
python source/ner/infer_ner.py --model source/ner/model_B --min-prob 0 --dst output_ner_B
# 2. candidates
python -c "import generate_all as ga; ga.DISABLE_RECALL=True; ga.graft('output_ner_B','out_pre')"
python source/eval/build_library_embeddings.py     # one-time, cached
python source/eval/rag_link.py --src out_pre --dst out_coded
# 3. assertions
python source/ner/apply_assertion.py --src out_coded --dst out_full \
       --model source/ner/model_assert_betacc --replace --thresh 0.5
# 4. validate + zip out_full → submission
```

---

## 6. Encoder choice (XLM-RoBERTa) — and why not ViHealthBERT/PhoBERT

All trained models use **XLM-RoBERTa-base**, chosen because:
1. The text is heavily **code-switched VN+EN** (drug names, lab codes, `po bid`, `mg` in English) —
   XLM-R's massively-multilingual pretraining handles the mix natively.
2. Vietnamese-only medical encoders (ViHealthBERT, PhoBERT, vibert) risk being WORSE on the English
   medical tokens, which are exactly the codeable drug/test entities.
3. The bottleneck was never the encoder — it was label/convention quality and the candidate
   convention-gating. Encoder swaps optimize the non-limiting part.

**ViHealthBERT is a legitimate untried lever** (one-line change: `train_ner.py --model
demdecuong/vihealthbert-base` or `vinai/phobert-base`). Expectation is modest given the
code-switching risk, but cheap to test now that the pipeline is stable.

---

## 7. Open ceiling / next levers
- **Candidates (~24, biggest weight):** proven +15 room, disease-led, but convention-gated —
  no blind method reaches it. The offline dev set (hand-label 15-25 files) is the only unlock.
- **A still-better base extraction** (like wer591 beat bet_acc) → distill → biggest single jumps.
- **Encoder swap** (ViHealthBERT) — untried, modest expectation.
- **Better isHistorical head** (p0.72, the weaker assertion label).
