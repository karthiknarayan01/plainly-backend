FROM vllm/vllm-openai:latest

ENV MODEL_ID=AtlaAI/Selene-1-Mini-Llama-3.1-8B

CMD python -m vllm.entrypoints.openai.api_server \
    --model "$MODEL_ID" \
    --port 8000
