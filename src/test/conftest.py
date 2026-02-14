from dotenv import load_dotenv
from fastapi.testclient import TestClient

from api.main import app

load_dotenv()

client = TestClient(app)
