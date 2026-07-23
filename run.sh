#!/usr/bin/env bash
# End-to-end INFERENCE: input/*.txt  ->  submission.zip
# Uses the trained models in models/ner and models/assert (no training needed).
set -e
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8

echo "[1/6] rule pipeline -> output_closest/ (provides code-transfer source for graft)"
python source/generate_all.py

echo "[2/6] entity NER (XLM-R) -> output_ner/"
python source/ner/infer_ner.py --model models/ner --min-prob 0 --dst output_ner

echo "[3/6] candidate linking part 1: rule code policy (graft, recall disabled)"
python -c "import sys; sys.path.insert(0,'source'); import generate_all as ga; \
ga.DISABLE_RECALL=True; ga.ASSERT_FROM_OVERLAP=False; ga.graft('output_ner','output_pre')"

echo "[4/6] candidate linking part 2: dense RAG over ICD/RxNorm (Qwen3-Embedding-4B)"
if [ ! -d library/embeddings ]; then
  echo "      building library embeddings (one-time, ~30 min, needs .env API key)"
  python source/eval/build_library_embeddings.py
fi
python source/eval/rag_link.py --src output_pre --dst output_coded

echo "[5/6] assertions (XLM-R classifier, replace @0.5)"
python source/ner/apply_assertion.py --src output_coded --dst output_final \
  --model models/assert --replace --thresh 0.5

echo "[6/6] package -> submission.zip"
rm -f submission.zip
( cd output_final && zip -q -r ../submission.zip . )
echo "DONE -> submission.zip  (unzip gives 1.json .. 100.json)"
