# Stub — health check only. Real endpoints (POST /rewrite-jobs, GET
# /rewrite-jobs/:id, GET /rewrite-jobs/:id/stream) are the next piece of
# work, once this infra/CI-CD path is confirmed working end to end.

from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}
