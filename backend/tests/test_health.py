def test_health_reports_ok(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body["ffmpeg"], bool)
    assert body["youtube"] == {"cookies": False, "proxy": False, "pot_provider": False}
