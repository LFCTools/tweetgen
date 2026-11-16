"""Smoke tests that run the whole Streamlit app (network faked out by app_harness.py)."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

HARNESS = str(Path(__file__).resolve().parent / "app_harness.py")
SECRETS = {
    "GEMINI_API_KEY": "x",
    "BUFFER_API_KEY": "x",
    "BUFFER_CHANNEL_ID": "x",
    "QSTASH_TOKEN": "x",
    "QSTASH_URL": "https://qstash.example",
    "DISCORD_WEBHOOK_URL": "https://discord.example/api/webhooks/1/a",
}


def new_app():
    at = AppTest.from_file(HARNESS, default_timeout=60)
    for k, v in SECRETS.items():
        at.secrets[k] = v
    at.run()
    assert not at.exception, at.exception
    return at


def press(at, key):
    buttons = [b for b in at.button if b.key == key]
    assert buttons, key
    if not buttons[0].disabled:
        buttons[0].click().run()
        assert not at.exception, at.exception


@pytest.mark.parametrize("tab", ["pl", "cup", "away", "cl"])
def test_scan_and_schedule_every_match_type(tab):
    # The fixtures include TBA dates (e.g. cup ballot results), which must not crash step 4.
    at = new_app()
    at.text_input(key="url").input(f"https://www.liverpoolfc.com/tickets/x/{tab}")
    at.button[0].click().run()
    assert not at.exception, at.exception
    assert at.session_state["match"]["type"]
    press(at, "buffer_go")
    press(at, "discord_go")
    assert any("qstash" in u for u in at.session_state["_sent"])


@pytest.mark.parametrize("match_type", ["pl_home", "cup_home", "league_away", "euro_away"])
def test_blank_manual_entry_with_everything_tba(match_type):
    at = new_app()
    at.segmented_control(key="type_choice").set_value(match_type).run()
    [b for b in at.button if "Enter manually" in b.label][0].click().run()
    assert not at.exception, at.exception
    assert at.session_state["match"]["type"] == match_type
