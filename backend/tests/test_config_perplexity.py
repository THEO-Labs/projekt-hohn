from app.config import Settings


def test_perplexity_settings_present(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pk-test")
    s = Settings(_env_file=None)
    assert s.perplexity_api_key == "pk-test"
    assert s.perplexity_model  # non-empty default
    assert s.perplexity_base_url.startswith("https://")
