import pytest

from app.core.config import (
    app_profile_enabled_project_ids,
    app_profile_required_for_project,
    settings,
)


def test_app_profile_feature_modes(monkeypatch):
    monkeypatch.setattr(settings, "app_profile_feature_mode", "off")
    monkeypatch.setattr(settings, "app_profile_enabled_project_ids", "1, 8,15")
    assert app_profile_required_for_project(1) is False

    monkeypatch.setattr(settings, "app_profile_feature_mode", "compat")
    assert app_profile_enabled_project_ids() == {1, 8, 15}
    assert app_profile_required_for_project(8) is True
    assert app_profile_required_for_project(9) is False

    monkeypatch.setattr(settings, "app_profile_feature_mode", "required")
    assert app_profile_required_for_project(999) is True


def test_app_profile_enabled_project_ids_rejects_invalid_value(monkeypatch):
    monkeypatch.setattr(settings, "app_profile_enabled_project_ids", "1,abc")
    with pytest.raises(RuntimeError, match="非法项目 ID"):
        app_profile_enabled_project_ids()
