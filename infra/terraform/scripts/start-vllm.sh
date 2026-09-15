#!/bin/bash
# Starts both models as separate vLLM servers, one per port.
# Model IDs below are placeholders — confirm the exact Hugging Face path
# and pick the final size (14B vs 32B for the writer) before relying on
# this. Re-run once that's settled.

set -e

pip install --upgrade vllm

WRITING_MODEL="Qwen/Qwen3-14B-Instruct"   # placeholder — confirm exact HF id
JUDGE_MODEL="AtlaAI/Selene-1-Mini-Llama-3.1-8B"

nohup python -m vllm.entrypoints.openai.api_server \
  --model "$WRITING_MODEL" \
  --port 8000 \
  --quantization awq \
  --enable-prefix-caching \
  > /var/log/vllm-writer.log 2>&1 &

nohup python -m vllm.entrypoints.openai.api_server \
  --model "$JUDGE_MODEL" \
  --port 8001 \
  --quantization awq \
  > /var/log/vllm-judge.log 2>&1 &

wait
