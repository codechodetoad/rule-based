# ViettelRace 2026 — Medical Concept Extraction & Normalization

Extract medical concepts from free-form Vietnamese (code-switched VN+EN) clinical / patient text and
normalize them: detect **5 entity types**, map diagnoses → **ICD-10** and drugs → **RxNorm**, and
infer contextual **assertions** (negated / historical / family).

**Leaderboard score: 35.81** (`0.3·text + 0.3·assert + 0.4·candidates`). Fully self-hostable,
**every model ≤ 9B** — competition-compliant for the private-test rebuild.

> The solution is **three small encoder models + rules + dense retrieval** — NOT a fine-tuned
> generative LLM. See [docs/DESIGN_NOTES.md](docs/DESIGN_NOTES.md) for the full design rationale,
> score progression, and every approach that was tried and rejected.

---

## 1. How it works (architecture)

```
input/N.txt
   │
   ▼  (A) ENTITY EXTRACTION — XLM-RoBERTa-base token classifier (~270M)   [models/ner]
   │      span + type + exact char offset
   ├───────────────────────────────┬───────────────────────────────────┐
   ▼ (B) CANDIDATE LINKING          ▼ (C) ASSERTIONS                     │
   │   rules (graft): drug→RxCUI       XLM-RoBERTa-base multi-label      │
   │   resolver, ICD leaf/tech codes,  classifier (~270M) [models/assert]│
   │   overcoding fixes, NO_CODE gate  isNegated / isHistorical          │
   │   + RAG: Qwen3-Embedding-4B dense (isFamily dropped — net-negative) │
   │   retrieval over ICD(31k)/RxNorm  context window ±(300/80), thr 0.5 │
   │   (48k), cached in library/embeddings                               │
   └───────────────────────────────┴───────────────────────────────────┘
                          ▼  merge (same entity set) + validate
                     output_final/N.json  →  submission.zip
```

Three components, each explained in [docs/DESIGN_NOTES.md §3]:

- **(A) Entity NER** — `models/ner`, XLM-RoBERTa-base fine-tuned for BIO token classification over the
  5 types, giving exact character spans. Trained on *silver* labels (a strong base extraction distilled
  into the model — distillation generalizes the spans and makes it reproducible/≤9B).
- **(B) Candidate linking** — a validated **rule code policy** (`source/generate_all.py` graft +
  `source/luong1` drugs + `source/luong2` findings) followed by **dense RAG**
  (`source/eval/rag_link.py`) that embeds each still-uncoded diagnosis/drug with **Qwen3-Embedding-4B**
  and matches it against pre-embedded ICD-10 / RxNorm libraries. Precision-gated (over-coding is
  penalized by the metric's `∅-vs-∅` rule).
- **(C) Assertions** — `models/assert`, XLM-RoBERTa-base multi-label classifier predicting
  isNegated / isHistorical from the entity's context window. (isFamily is intentionally dropped —
  measured net-negative.)

Why **XLM-RoBERTa** and not a Vietnamese medical BERT (ViHealthBERT/PhoBERT): the text is heavily
code-switched (English drug/lab names, `po bid`, `mg`), which XLM-R's multilingual pretraining handles
natively; Vietnamese-only encoders risk being worse on exactly the codeable English tokens.

---

## 2. Repository layout

```
.
├── README.md                     ← you are here
├── docs/DESIGN_NOTES.md          ← full architecture, score history, rejected approaches
├── requirements.txt
├── run.sh                        ← end-to-end inference: input/ -> submission.zip
├── train.sh                      ← train both models from a base extraction
├── .env.example                  ← API key for the embedding endpoint (copy -> .env)
│
├── input/                        ← 100 test records (1.txt … 100.txt)
│
├── source/
│   ├── generate_all.py           ← rule orchestrator + graft (candidate code policy)
│   ├── ner/
│   │   ├── build_entity_dataset.py      ← base extraction -> NER dataset
│   │   ├── train_ner.py                 ← train XLM-R entity model
│   │   ├── infer_ner.py                 ← NER inference -> spans+types
│   │   ├── build_assertion_dataset.py   ← base extraction -> assertion dataset
│   │   ├── train_assertion.py           ← train XLM-R assertion classifier
│   │   ├── apply_assertion.py           ← classifier -> assertions on an output
│   │   └── ner_metrics.py
│   ├── luong1/                   ← THUỐC → RxNorm rule extractor + lexicon
│   ├── luong2/                   ← CHẨN_ĐOÁN/TRIỆU/XÉT_NGHIỆM rule extractor + lexicons
│   └── eval/
│       ├── rag_link.py                  ← dense candidate linking (embeddings)
│       ├── build_library_embeddings.py  ← embed ICD/RxNorm libraries (one-time cache)
│       └── local_scorer.py              ← metric implementation
│
├── library/                      ← derived ICD-10 (31k) + RxNorm (48k) catalogs + build scripts
│   └── embeddings/               ← (git-ignored) rebuildable embedding cache
├── database/                     ← gazetteers used by the rule extractors
├── data/
│   ├── base_extractions/wer591/  ← the silver source (best base extraction) we distill from
│   └── silver/                   ← generated NER + assertion training sets
└── models/
    ├── ner/                      ← trained XLM-R entity model  (Git LFS)
    └── assert/                   ← trained XLM-R assertion classifier  (Git LFS)
```

---

## 3. Setup

```bash
python -m venv .venv && source .venv/bin/activate      # (Windows: .venv\Scripts\activate)
pip install -r requirements.txt
cp .env.example .env       # then paste your embedding-endpoint API key into .env
```

**Trained model weights (~2.1 GB) are NOT shipped in this repo** — they are `.gitignore`d to keep
it lean. Rebuild them once with a GPU (~4 minutes total):
```bash
./train.sh          # -> models/ner + models/assert  (from data/base_extractions/wer591)
```
Everything needed to reproduce them (the base extraction silver + training scripts) is included.

---

## 4. Run inference (produce a submission)

```bash
./run.sh          # input/*.txt  ->  submission.zip
```
Steps (see `run.sh`): rule pipeline → NER → rule candidate policy → RAG candidate linking →
assertion classifier → validated `output_final/` → `submission.zip`.

> The **first** run builds `library/embeddings/` (~30 min, one-time, needs the `.env` API key or a
> self-hosted embedder). Subsequent runs reuse the cache.

---

## 5. Train from scratch

```bash
./train.sh                                   # trains from data/base_extractions/wer591
# or from a different base extraction folder of {1..100}.json:
./train.sh data/base_extractions/<your_base>
```
Produces `models/ner` and `models/assert`. Then `./run.sh`.

A "base extraction" is silver: any strong labeling of `input/` (list of
`{text,type,position,assertions}` per file). The pipeline distills it into the reproducible ≤9B
models. Swapping in a better base extraction and re-running `train.sh` was historically the biggest
single score lever (see design notes).

---

## 6. Output format

Each `input/N.txt` → `N.json`, a list of:
```json
{
  "text": "amlodipine 10 mg po daily",
  "type": "THUỐC",
  "position": [58, 83],
  "assertions": ["isHistorical"],
  "candidates": ["308135"]
}
```
- `type` ∈ {TRIỆU_CHỨNG, TÊN_XÉT_NGHIỆM, KẾT_QUẢ_XÉT_NGHIỆM, CHẨN_ĐOÁN, THUỐC}
- `assertions` (CHẨN_ĐOÁN/THUỐC/TRIỆU_CHỨNG): isNegated / isHistorical / isFamily
- `candidates` (CHẨN_ĐOÁN→ICD-10, THUỐC→RxNorm)

---

## 7. Compliance (≤ 9B, self-hostable)

| component | model | size |
|---|---|---|
| entity NER | XLM-RoBERTa-base | ~270M |
| assertions | XLM-RoBERTa-base | ~270M |
| candidate retrieval | Qwen3-Embedding-4B | 4B |

Total ≪ 9B. The embedder is called via an OpenAI-compatible API by default; self-host any equivalent
4B embedding model for a fully offline run. No generative LLM is used at inference.
