# ITY MVP

FastAPI MVP for an Info-to-You (ITY) aggregation and personalization platform focused on haircut appointments.

## Run

```bash
# From /workspace/ity
python -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e .
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000

## Endpoints
- `/` search UI
- `/api/search/haircuts` JSON search
- `/api/book` mock booking
- `/healthz` health check