"""Runs app.py with the network faked out, for tests/test_app.py (Streamlit AppTest)."""
import json
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TESTS))

import google.genai  # noqa: E402
import requests  # noqa: E402
import streamlit as st  # noqa: E402
from fixtures import FAKES  # noqa: E402

TYPES = {"pl": "pl_home", "cup": "cup_home", "away": "league_away", "cl": "euro_away"}


class _Resp:
    status_code = 200

    def __init__(self, text="", data=None):
        self.text, self._data = text, data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _get(url, **kwargs):
    return _Resp("<main><p>TYPE=" + url.rstrip("/").rsplit("/", 1)[-1] + " " + "ticket info " * 100 + "</p></main>")


def _post(url, **kwargs):
    st.session_state.setdefault("_sent", []).append(url)
    if "qstash" in url:
        return _Resp(data={"messageId": "msg_1"})
    return _Resp(data={"data": {"createPost": {"post": {"id": "1"}}}})


class _Models:
    def generate_content(self, **kwargs):
        prompt = kwargs["contents"]
        tag = prompt.split("TYPE=")[1].split()[0]

        class Out:
            text = json.dumps({"match_type": TYPES[tag]} if "Classify which kind" in prompt else FAKES[tag])

        return Out()


class _Client:
    def __init__(self, **kwargs):
        self.models = _Models()


requests.get, requests.post, google.genai.Client = _get, _post, _Client
exec(compile((ROOT / "app.py").read_text(), str(ROOT / "app.py"), "exec"))
