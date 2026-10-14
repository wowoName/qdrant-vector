from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.providers.llm import factory


def test_ark_uses_global_model():
    config = SimpleNamespace(llm_provider="ark", llm_model="ep-global", ollama_model="local")
    with patch.object(factory, "settings", config):
        assert factory._get_default_model("ark") == "ep-global"


def test_ark_does_not_inherit_other_provider_model():
    config = SimpleNamespace(llm_provider="ollama", llm_model="local", ollama_model="local")
    with patch.object(factory, "settings", config):
        assert factory._get_default_model("ark") == ""


def test_explicit_scene_model_overrides_global():
    config = SimpleNamespace(llm_provider="ark", llm_model="ep-global")
    with patch.object(factory, "settings", config), patch.object(factory, "_create_llm_internal") as create:
        factory.create_llm(model="ep-scene", force_new=True)
        create.assert_called_once_with("ark", "ep-scene")


def test_missing_ark_model_points_to_unified_config():
    with pytest.raises(ValueError, match="LLM_MODEL"):
        factory._create_ark_llm("")
