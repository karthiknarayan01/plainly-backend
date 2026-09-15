# Weights baked in at build time too, same reasoning as writer-model.
# 8B is small enough that quantization is optional here — full precision
# fits the L4's 24GB comfortably with room for KV cache — but keeping
# both Dockerfiles the same shape for consistency.

FROM vllm/vllm-openai:latest

ARG MODEL_ID=AtlaAI/Selene-1-Mini-Llama-3.1-8B
ENV MODEL_ID=${MODEL_ID}

RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('${MODEL_ID}')"

CMD python -m vllm.entrypoints.openai.api_server \
    --model "$MODEL_ID" \
    --port 8000
