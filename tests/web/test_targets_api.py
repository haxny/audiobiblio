def test_create_target_with_approval_mode(client):
    r = client.post("/api/v1/targets", json={
        "url": "https://mujrozhlas.cz/hra-na-nedeli",
        "kind": "program", "approval_mode": "auto",
    })
    assert r.status_code == 201
    assert r.json()["approval_mode"] == "auto"


def test_create_target_defaults_to_review(client):
    r = client.post("/api/v1/targets", json={
        "url": "https://mujrozhlas.cz/cetba-na-pokracovani", "kind": "program",
    })
    assert r.status_code == 201
    assert r.json()["approval_mode"] == "review"


def test_patch_approval_mode(client):
    r = client.post("/api/v1/targets", json={
        "url": "https://mujrozhlas.cz/x", "kind": "program"})
    tid = r.json()["id"]
    r2 = client.patch(f"/api/v1/targets/{tid}", json={"approval_mode": "auto"})
    assert r2.status_code == 200
    assert r2.json()["approval_mode"] == "auto"


def test_content_class_round_trip(client):
    r = client.post("/api/v1/targets", json={
        "url": "https://mujrozhlas.cz/toulky", "kind": "program",
        "content_class": "serial", "priority_base": -5})
    assert r.status_code == 201
    body = r.json()
    assert body["content_class"] == "SERIAL" and body["priority_base"] == -5
    r2 = client.patch(f"/api/v1/targets/{body['id']}", json={"content_class": "MUSIC-CYCLE"})
    assert r2.json()["content_class"] == "MUSIC-CYCLE"
    assert r2.json()["priority_base"] == -5
    listed = {t["id"]: t for t in client.get("/api/v1/targets").json()}
    assert listed[body["id"]]["content_class"] == "MUSIC-CYCLE"


def test_content_class_defaults_to_none(client):
    r = client.post("/api/v1/targets", json={"url": "https://mujrozhlas.cz/y", "kind": "program"})
    assert r.json()["content_class"] is None and r.json()["priority_base"] is None


def test_invalid_content_class_rejected(client):
    r = client.post("/api/v1/targets", json={
        "url": "https://mujrozhlas.cz/z", "kind": "program", "content_class": "poetry"})
    assert r.status_code == 400


def test_patch_can_clear_content_class(client):
    r = client.post("/api/v1/targets", json={
        "url": "https://mujrozhlas.cz/w", "kind": "program", "content_class": "BOOK"})
    tid = r.json()["id"]
    r2 = client.patch(f"/api/v1/targets/{tid}", json={"content_class": ""})
    assert r2.json()["content_class"] is None
