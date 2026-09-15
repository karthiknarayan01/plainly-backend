# Weights baked in at build time — no download at container startup.
# Confirm the exact AWQ checkpoint name before relying on this; "an AWQ
# build of Qwen3-32B-Instruct exists" is not independently verified here,
# only that Qwen3 is Apache 2.0 and AWQ is a standard, well-supported
# vLLM quantization format. Search Hugging Face for the actual repo name
# closer to build time and swap MODEL_ID below.

FROM vllm/vllm-openai:latest

ARG MODEL_ID=Qwen/Qwen3-32B-Instruct-AWQ
ENV MODEL_ID=${MODEL_ID}

# Bakes the weights into this image layer at build time.
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('${MODEL_ID}')"

CMD python -m vllm.entrypoints.openai.api_server \
    --model "$MODEL_ID" \
    --port 8000 \
    --quantization awq \
    --enable-prefix-caching \
    --gpu-memory-utilization 0.90
