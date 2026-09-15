# Full precision — no quantization. We already provisioned the RTX PRO
# 6000 (96GB) for headroom, so there's no memory reason to trade quality
# for a smaller footprint here. ~64GB of weights leaves ~32GB for the KV
# cache; validate that's enough for real long-document context once
# deployed — if it isn't, revisit (cap context length before reaching for
# quantization).
#
# Repo name verified against huggingface.co/Qwen/Qwen3-32B — Qwen3 dense
# models ship as one unified checkpoint (thinking/non-thinking is a
# runtime toggle, not a separate "-Instruct" repo, unlike Qwen2.5).

FROM vllm/vllm-openai:latest

ARG MODEL_ID=Qwen/Qwen3-32B
ENV MODEL_ID=${MODEL_ID}

# Bakes the weights into this image layer at build time — no download at
# container startup. The base image only has python3 on PATH, not python.
RUN python3 -c "from huggingface_hub import snapshot_download; snapshot_download('${MODEL_ID}')"

CMD python3 -m vllm.entrypoints.openai.api_server \
    --model "$MODEL_ID" \
    --port 8000 \
    --enable-prefix-caching \
    --gpu-memory-utilization 0.90
