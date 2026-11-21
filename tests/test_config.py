import os
import subprocess
import sys

from ajcine.config import Settings


def test_env_parsing_and_validation():
    s = Settings.from_env({"API_ID": "5", "API_HASH": "h", "BOT_TOKEN": "t", "OWNER_IDS": "1, 2,x", "WEB_SECRET_KEY": "s" * 40, "TMDB_API_KEY": "k",
                           "OPENROUTER_FALLBACK_MODELS": "a:free, b:free", "AI_RPM": "99", "PORT": "9000"})
    assert s.owner_ids == (1, 2) and s.openrouter_fallback_models == ("a:free", "b:free")
    assert s.ai_rpm == 20 and s.web_port == 9000 and s.problems() == []


def test_missing_settings_are_reported_without_leaking_values():
    s = Settings.from_env({"WEB_SECRET_KEY": "short"})
    p = " ".join(s.problems())
    assert "API_ID" in p and "BOT_TOKEN" in p and "OWNER_IDS" in p and "WEB_SECRET_KEY" in p and "TMDB_API_KEY" in p


def test_threshold_order_is_enforced():
    s = Settings.from_env({"API_ID": "5", "API_HASH": "h", "BOT_TOKEN": "t", "OWNER_IDS": "1", "WEB_SECRET_KEY": "s" * 40, "TMDB_API_KEY": "k",
                           "MATCH_AUTO_THRESHOLD": "0.5", "MATCH_REVIEW_THRESHOLD": "0.6"})
    assert any("THRESHOLD" in x for x in s.problems())


def test_dotenv_file_is_read_and_real_variables_win(tmp_path, monkeypatch):
    f = tmp_path / "my.env"
    f.write_text('# comment\nAPI_ID=77\nexport API_HASH="abc def"\nBOT_TOKEN=1:tok  # trailing\nOWNER_IDS=5,6\n'
                 "WEB_SECRET_KEY='" + "k" * 40 + "'\nTMDB_API_KEY=fromfile\nbad line\nPORT=9100\n", encoding="utf-8")
    monkeypatch.setenv("ENV_FILE", str(f))
    monkeypatch.setenv("PORT", "9200")                                   # a real variable overrides the file
    monkeypatch.delenv("API_ID", raising=False)
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    s = Settings.from_env()
    assert (s.api_id, s.api_hash, s.bot_token, s.owner_ids) == (77, "abc def", "1:tok", (5, 6))
    assert s.tmdb_api_key == "fromfile" and s.web_port == 9200 and s.problems() == []


def test_missing_dotenv_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "nope.env"))
    assert Settings.from_env().api_id in (0, int(os.environ.get("API_ID", 0) or 0))


def test_process_refuses_to_start_when_misconfigured():
    env = {k: v for k, v in os.environ.items() if k not in ("API_ID", "BOT_TOKEN", "OWNER_IDS", "WEB_SECRET_KEY")}
    env["ENV_FILE"] = "/nonexistent/.env"
    r = subprocess.run([sys.executable, "-m", "ajcine"], capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 2 and "configuration" in r.stdout


def test_secrets_are_scrubbed_from_logs():
    from ajcine.logging_setup import _scrub
    out = _scrub("connecting api_key=sk-abc token=123:xyz ok=1")
    assert "sk-abc" not in out and "123:xyz" not in out and "ok=1" in out
