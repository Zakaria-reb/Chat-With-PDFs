"""Vérifie le format des requêtes NVIDIA et la reprise sur 429 (sans réseau) : python -m tests.test_nvidia_mock"""
import os

os.environ["DEMO_MODE"] = "0"
os.environ["NVIDIA_API_KEY"] = "nvapi-test"

from unittest import mock  # noqa: E402

from app import config, models  # noqa: E402

config.DEMO_MODE = False
config.NVIDIA_API_KEY = "nvapi-test"
calls = []


class Resp:
    def __init__(self, status, data=None, headers=None):
        self.status_code, self._d, self.headers = status, data or {}, headers or {}
        self.ok = status < 400
        self.text = str(data)

    def json(self):
        return self._d


def fake_post(url, headers=None, json=None, timeout=None):
    calls.append((url, json))
    if len(calls) == 1:
        return Resp(429, {"error": "rate"}, {"Retry-After": "0"})
    if url.endswith("/embeddings"):
        return Resp(200, {"data": [{"index": i, "embedding": [float(i), 1.0]} for i in range(len(json["input"]))]})
    return Resp(200, {"choices": [{"message": {"content": "réponse [1]"}}]})


with mock.patch("requests.post", fake_post), mock.patch("time.sleep", lambda s: None):
    vecs = models.embed([f"t{i}" for i in range(20)], "passage")
    assert len(vecs) == 20, len(vecs)
    assert calls[0][0].endswith("/embeddings") and calls[0][1]["input_type"] == "passage"
    assert len(calls) == 3, len(calls)  # 1 tentative en 429, puis 2 lots (16 + 4)
    assert [len(c[1]["input"]) for c in calls[1:]] == [16, 4]

    out = models.generate_answer(
        "Question ?", [{"n": 1, "doc": "a.pdf", "page": 2, "text": "contexte"}], [], [{"role": "user", "content": "avant"}]
    )
    assert out == "réponse [1]"
    body = calls[-1][1]
    assert body["model"] == config.TEXT_MODEL and body["messages"][0]["role"] == "system"
    assert "[1] (document : a.pdf, page 2)" in body["messages"][-1]["content"][0]["text"]
print("NVIDIA mock OK")
