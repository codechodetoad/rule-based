#!/usr/bin/env bash
# TRAIN both models from a base extraction (silver labels). Needs a GPU (~2 min each on RTX 4060).
# Reproduces models/ner and models/assert from data/base_extractions/<base>.
#   usage: ./train.sh [base_extraction_dir]     (default: data/base_extractions/wer591)
set -e
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
BASE="${1:-data/base_extractions/wer591}"
echo "Base extraction (silver): $BASE"

echo "== 1. entity NER model =="
python source/ner/build_entity_dataset.py --base "$BASE" --out data/silver/entities
python source/ner/train_ner.py --model xlm-roberta-base \
  --data-dir "$PWD/data/silver/entities" --out models/ner --epochs 10 --lr 2e-5

echo "== 2. assertion classifier =="
python source/ner/build_assertion_dataset.py --base "$BASE" --out data/silver/assertions
python source/ner/train_assertion.py \
  --data-dir data/silver/assertions --out models/assert --epochs 12 --lr 2e-5

echo "DONE -> models/ner, models/assert.  Now run ./run.sh to produce a submission."
