from core.config import Settings


class TestRateLimitSettings:
    """Тесты дефолтных значений полей rate limiting в ``Settings``."""

    def test_defaults_when_not_overridden_by_env(self) -> None:
        """Без переопределения через окружение используются дефолты."""
        settings = Settings()

        assert settings.rate_limit_requests == 100
        assert settings.rate_limit_window_seconds == 60
        assert settings.rate_limit_exempt_paths == ["/health"]
