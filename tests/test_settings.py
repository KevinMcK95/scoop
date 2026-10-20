import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from scoop.server.settings import Settings

try:
    from langchain_core.messages import AIMessage
    from starlette.testclient import TestClient
    from scoop.server import create_app
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] not in {"langgraph", "langchain_core", "starlette", "httpx"}:
        raise
    HAS_SERVER = False
else:
    HAS_SERVER = True

KEY = "sk-or-v1-0123456789abcdef"


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "conf" / "settings.json"

    def test_secrets_are_saved_privately_and_never_shown(self):
        settings = Settings(self.path, defaults={"model": "openai/gpt-6-luna"})
        settings.update({"api_key": KEY, "base_url": "https://openrouter.ai/api/v1"})
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        view = settings.public()
        self.assertEqual(view, {"model": "openai/gpt-6-luna", "base_url": "https://openrouter.ai/api/v1",
                                "api_key_set": True, "api_key_hint": "…cdef", "ads_token_set": False, "ads_token_hint": None,
                                "sources": {"model": "environment", "base_url": "settings", "api_key": "settings",
                                            "ads_token": None}})
        # The file holds only what was set here, never environment values.
        self.assertEqual(set(json.loads(self.path.read_text())), {"api_key", "base_url"})
        self.assertNotIn(KEY, json.dumps(view))
        # Saved values survive a restart and win over environment defaults.
        again = Settings(self.path, defaults={"model": "other/model", "api_key": "env-key"})
        self.assertEqual((again.get("model"), again.get("api_key")), ("other/model", KEY))
        # Removing a saved value falls back to the environment immediately.
        again.update({"api_key": ""})
        self.assertEqual((again.get("api_key"), again.source("api_key")), ("env-key", "environment"))

    def test_update_sets_clears_and_keeps(self):
        settings = Settings(self.path)
        settings.update({"api_key": KEY, "ads_token": "ads-token-123"})
        settings.update({"ads_token": ""})
        self.assertEqual((settings.get("api_key"), settings.get("ads_token")), (KEY, None))
        for bad in ({"password": "x"}, {"model": "two words"}, {"base_url": "http://plain.example"},
                    {"api_key": "line\nbreak"}, {"api_key": 5}, []):
            with self.assertRaises(ValueError, msg=bad):
                settings.update(bad)


class Model:
    def __init__(self, name):
        self.name = name

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages, config=None):
        return AIMessage(content=f"I am {self.name}.")


@unittest.skipUnless(HAS_SERVER, "optional server dependencies absent")
class SettingsEndpointTests(unittest.TestCase):
    def setUp(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "settings.json"
        self.settings = Settings(path)
        self.client = TestClient(create_app(settings=self.settings,
                                            make_model=lambda s: Model(s.get("model")) if s.get("model") else None,
                                            make_ads=lambda s: None))

    def post(self, body, origin="http://127.0.0.1:5173"):
        return self.client.post("/api/settings", json=body, headers={"Origin": origin})

    def test_saving_settings_switches_the_model_live(self):
        self.assertEqual(self.client.get("/api/health").json()["chat"], False)
        saved = self.post({"model": "openai/gpt-6-luna", "api_key": KEY})
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()["chat"])
        self.assertNotIn(KEY, saved.text)
        self.assertNotIn(KEY, self.client.get("/api/settings").text)
        reply = self.client.post("/api/chat", json={"thread_id": "settings-0001", "message": "who?", "context": {}})
        self.assertIn("I am openai/gpt-6-luna.", reply.text)

    def test_other_sites_cannot_change_settings(self):
        self.assertEqual(self.post({"api_key": "stolen"}, origin="https://evil.example").status_code, 403)
        form = self.client.post("/api/settings", content="api_key=x", headers={"Origin": "http://127.0.0.1:5173",
                                                                               "Content-Type": "text/plain"})
        self.assertEqual(form.status_code, 403)
        self.assertIsNone(self.settings.get("api_key"))
        self.assertEqual(self.post({"model": "bad model"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
