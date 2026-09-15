# Confirm the exact Hugging Face path for the chosen Qwen3-32B variant
# before relying on this — not independently verified.
FROM vllm/vllm-openai:latest

ENV MODEL_ID=Qwen/Qwen3-32B-Instruct

CMD python -m vllm.entrypoints.openai.api_server \
    --model "$MODEL_ID" \
    --port 8000 \
    --enable-prefix-caching \
    --gpu-memory-utilization 0.90
