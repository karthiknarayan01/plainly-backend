# Built with the repo root as context (see .github/workflows/deploy.yml)
# so prompts/ — outside services/worker/ — can be copied in. The COPY
# layout below mirrors the repo's own relative structure
# (services/worker/llm.py -> ../../prompts) so llm.py's PROMPTS_DIR
# resolves identically here and in a local checkout, no path
# special-casing needed.
FROM python:3.12-slim
WORKDIR /app

COPY services/worker/requirements.txt services/worker/requirements.txt
RUN pip install --no-cache-dir -r services/worker/requirements.txt

COPY services/worker/ services/worker/
COPY prompts/ prompts/

WORKDIR /app/services/worker
CMD ["python", "main.py"]
