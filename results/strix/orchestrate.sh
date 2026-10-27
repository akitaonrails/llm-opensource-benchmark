#!/usr/bin/env bash
# Wait for model copies, build the remaining Strix packs, then run speed+quality for all variants.
set -u
cd /mnt/data/Projects/llm-opensource-benchmark
COPYLOG=/tmp/claude-1026/-mnt-data-Projects-llm-opensource-benchmark/7e84ed7c-2109-47dc-87db-f7c6013e24f2/scratchpad/copy_models.log
O=/var/strata-models/osb
until grep -q COPY_DONE "$COPYLOG"; do sleep 20; done
grep FAILED "$COPYLOG" && { echo "copy failed, stopping"; exit 1; }
echo "copies done $(date +%T)"
ssh -o BatchMode=yes 192.168.0.90 "$O/build_pack.sh iq3_xxs $O/models/qwen-iq3_xxs/IQ3_XXS/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_XXS-00001-of-00002.gguf > $O/pack-iq3_xxs.log 2>&1; tail -1 $O/pack-iq3_xxs.log"
ssh -o BatchMode=yes 192.168.0.90 "$O/build_pack.sh ud-q4_k_xl $O/models/qwen-q4_k_xl/UD-Q4_K_XL/Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf > $O/pack-ud-q4_k_xl.log 2>&1; tail -1 $O/pack-ud-q4_k_xl.log"
echo "packs done $(date +%T)"
bench/run_strix.sh q2_0 iq3_xxs iq3_s q4_k_xl
