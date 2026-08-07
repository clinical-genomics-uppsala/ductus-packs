from flask import Flask, jsonify, request

app = Flask(__name__)

FAKE_TOKEN = "fake-token-abc123"

RUNS = [
    {
        "name": "test_run_001",
        "uniqueId": "aaaaaaaa-0000-0000-0000-000000000001",
        "createdAt": "2026-06-01T10:00:00.000Z",
        "summary": "Mock run",
    }
]

COLLECTIONS = {
    "aaaaaaaa-0000-0000-0000-000000000001": [
        {
            "name": "test_run_001_A01",
            "uniqueId": "bbbbbbbb-0000-0000-0000-000000000001",
            "status": "Complete",
            "well": "A01",
            "collectionPathUri": "/data/revio/test_run_001/1_A01",
            "ccsId": "cccccccc-0000-0000-0000-000000000001",
            "movieMinutes": 120,
        }
    ]
}


@app.post("/token")
def token():
    return jsonify({"access_token": FAKE_TOKEN, "expires_in": 7200})


@app.get("/SMRTLink/1.0.0/status")
def status():
    assert request.headers.get("Authorization") == f"Bearer {FAKE_TOKEN}"
    return jsonify({"version": "25.1", "user": "testuser"})


@app.get("/SMRTLink/1.0.0/smrt-link/runs")
def runs():
    assert request.headers.get("Authorization") == f"Bearer {FAKE_TOKEN}"
    return jsonify(RUNS)


@app.get("/SMRTLink/1.0.0/smrt-link/runs/<run_uuid>/collections")
def collections(run_uuid):
    assert request.headers.get("Authorization") == f"Bearer {FAKE_TOKEN}"
    return jsonify(COLLECTIONS.get(run_uuid, []))


@app.post("/SMRTLink/1.0.0/smrt-link/job-manager/jobs/analysis")
def submit_job():
    body = request.json
    return jsonify({
        "uuid": "dddddddd-0000-0000-0000-000000000001",
        "id": 42,
        "state": "CREATED",
        "name": body.get("name"),
    }), 201


if __name__ == "__main__":
    app.run(port=9999)