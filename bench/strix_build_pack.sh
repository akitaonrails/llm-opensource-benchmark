#!/usr/bin/env bash
# Build one Strata 0.1.45.3 pack the way setup does, into our own folder only.
set -eu
O=/var/strata-models/osb
variant=$1; shard=$2
pack=$O/data/packs/$variant
export STRATA_GGUF_PY=$O/strata/third_party/llama.cpp/gguf-py
cd $O/strata
PY=./python/bin/python3.12
case $variant in
  q2_0)
    $PY tools/strata_pack.py build --gguf "$shard" --out "$pack" --skip-hash --force
    $PY tools/pack_index.py --pack "$pack"
    $PY tools/strata_tokenizer.py --gguf "$shard" --out "$pack" ;;
  iq3_xxs)
    $PY tools/iq_pack.py --gguf "$shard" --out "$pack" ;;
  ud-q4_k_xl)
    $PY tools/iq_pack.py --gguf "$shard" --out "$pack" --compat-bf16 ;;
esac
echo "PACK_DONE $variant"
