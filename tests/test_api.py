import json

import httpx

from annotate.api import MARKER_URI, HClient


def test_create_marker_posts_expected_body_and_returns_id():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "new-marker-id"})

    client = HClient(
        "http://localhost:5000", "6879-secret", transport=httpx.MockTransport(handler)
    )
    new_id = client.create_marker("grp42", "review:open")

    assert new_id == "new-marker-id"
    assert seen["method"] == "POST"
    assert seen["url"] == "http://localhost:5000/api/annotations"
    assert seen["auth"] == "Bearer 6879-secret"
    assert seen["body"] == {
        "uri": MARKER_URI,
        "group": "grp42",
        "text": "review:open",
        "tags": ["review:open"],
    }


def test_tag_merges_existing_tags_deduped_and_order_preserved():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"id": "ann1", "tags": ["paperA"]})
        return httpx.Response(200, json={"id": "ann1", "tags": json.loads(request.content)["tags"]})

    client = HClient("http://localhost:5000", "tok", transport=httpx.MockTransport(handler))
    # "paperA" already present -> not duplicated; "acted" new -> appended.
    client.tag("ann1", ["acted", "paperA"])

    get_req, patch_req = requests
    assert get_req.method == "GET"
    assert str(get_req.url) == "http://localhost:5000/api/annotations/ann1"
    assert patch_req.method == "PATCH"
    assert str(patch_req.url) == "http://localhost:5000/api/annotations/ann1"
    assert patch_req.headers.get("Authorization") == "Bearer tok"
    assert json.loads(patch_req.content)["tags"] == ["paperA", "acted"]
