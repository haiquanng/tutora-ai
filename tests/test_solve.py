from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
    r = client.get("/health")
    assert r.status_code == 200

def test_solve_text():
    r = client.post("/solve", json={
        "text": "Giải phương trình $x^2 - 5x + 6 = 0$",
        "grade": "10"
    })
    assert r.status_code == 200
    data = r.json()
    assert "steps" in data
    assert len(data["steps"]) > 0
