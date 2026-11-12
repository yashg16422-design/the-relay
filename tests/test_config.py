from __future__ import annotations

import pytest
from pydantic import ValidationError

from relay.config import AppConfig, load_config


def sample_config(**overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "provider": "standin",
        "request_timeout_s": 30,
        "stream": True,
        "groq": {
            "base_url": "https://api.groq.com/openai/v1",
            "api_key_env": "GROQ_API_KEY",
            "model": None,
        },
        "standin": {"base_url": "http://127.0.0.1:8000/v1"},
    }
    config.update(overrides)
    return config


def test_default_config_selects_local_standin() -> None:
    config = load_config()

    assert config.provider == "standin"
    assert config.active_base_url == "http://127.0.0.1:8000/v1"
    assert config.api_key is None


def test_groq_provider_can_be_loaded_before_model_discovery() -> None:
    raw = sample_config(provider="groq")

    config = AppConfig.model_validate(raw)

    assert config.provider == "groq"
    assert config.groq.model is None


@pytest.mark.parametrize(
    "url",
    [
        "http://api.groq.com/openai/v1",
        "https://example.com/openai/v1",
        "https://api.groq.com:444/openai/v1",
        "https://user:password@api.groq.com/openai/v1",
        "https://api.groq.com/openai/v1?forward=elsewhere",
        "https://api.groq.com:bad/openai/v1",
    ],
)
def test_groq_endpoint_rejects_untrusted_url(url: str) -> None:
    raw = sample_config(
        groq={"base_url": url, "api_key_env": "GROQ_API_KEY", "model": "test-model"}
    )

    with pytest.raises(ValidationError, match="Groq base_url"):
        AppConfig.model_validate(raw)


@pytest.mark.parametrize("key", [None, "", "   "])
def test_groq_provider_fails_on_missing_or_blank_key(
    monkeypatch: pytest.MonkeyPatch, key: str | None
) -> None:
    config = AppConfig.model_validate(
        sample_config(
            provider="groq",
            groq={
                "base_url": "https://api.groq.com/openai/v1",
                "api_key_env": "TEST_GROQ_KEY",
                "model": "test-model",
            },
        )
    )
    if key is None:
        monkeypatch.delenv("TEST_GROQ_KEY", raising=False)
    else:
        monkeypatch.setenv("TEST_GROQ_KEY", key)

    with pytest.raises(RuntimeError, match="Missing TEST_GROQ_KEY"):
        _ = config.api_key


def test_groq_example_key_placeholder_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    config = AppConfig.model_validate(
        sample_config(
            provider="groq",
            groq={
                "base_url": "https://api.groq.com/openai/v1",
                "api_key_env": "TEST_GROQ_KEY",
                "model": "test-model",
            },
        )
    )
    monkeypatch.setenv("TEST_GROQ_KEY", "put-your-key-in-.env-not-here")

    with pytest.raises(RuntimeError, match="example placeholder"):
        _ = config.api_key


def test_groq_key_is_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    config = AppConfig.model_validate(
        sample_config(
            provider="groq",
            groq={
                "base_url": "https://api.groq.com/openai/v1",
                "api_key_env": "TEST_GROQ_KEY",
                "model": "test-model",
            },
        )
    )
    monkeypatch.setenv("TEST_GROQ_KEY", "  local-test-value  ")

    assert config.api_key == "local-test-value"


def test_loader_rejects_non_mapping_yaml(tmp_path) -> None:
    config_path = tmp_path / "bad.yaml"
    config_path.write_text("- not-a-mapping\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="root must be a YAML mapping"):
        load_config(config_path)
