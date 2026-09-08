import os

from dotenv import load_dotenv
from fastapi.testclient import TestClient

load_dotenv()

# Set before importing the app: the API refuses to serve when no key is
# configured, and every request has to carry one that names a tenant.
os.environ.setdefault("PAYMENTS_API_KEYS", "test:test-key")

from api.main import app  # noqa: E402

client = TestClient(app, headers={"X-API-Key": "test-key"})
