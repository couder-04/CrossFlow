"""Boot refuses default secrets outside dev."""

from __future__ import annotations

import pytest
from anpr_common.config import DEFAULT_JWT_SECRET, Settings
from api.main import _validate_settings


def test_prod_rejects_default_jwt_secret():
    settings = Settings(app_env="prod", jwt_secret=DEFAULT_JWT_SECRET)
    with pytest.raises(RuntimeError):
        _validate_settings(settings)


def test_dev_allows_default_jwt_secret():
    settings = Settings(app_env="dev", jwt_secret=DEFAULT_JWT_SECRET)
    _validate_settings(settings)
