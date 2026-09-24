import os

from dotenv import load_dotenv
from fastapi.testclient import TestClient

load_dotenv()

# Set before importing the app: the API refuses to serve when no key is
# configured, and every request has to carry one that names a tenant.
#
# Assigned, not setdefault: run inside the deployed image this inherits the
# real PAYMENTS_API_KEYS, the test key is never registered, and every request
# comes back 401 — which is exactly how five of these tests came to be
# permanently red and ignored.
os.environ["PAYMENTS_API_KEYS"] = "test:test-key"

from api.main import app  # noqa: E402

client = TestClient(app, headers={"X-API-Key": "test-key"})
