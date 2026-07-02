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
