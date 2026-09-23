from datetime import timedelta
from unittest.mock import MagicMock, call

import pytest

from storage.minio_service import BUCKETS, MinIOService, _guess_content_type

# Фикстуры


@pytest.fixture
def mock_client() -> MagicMock:
    """Мок клиента Minio."""
    return MagicMock()


@pytest.fixture
def service(mock_client: MagicMock) -> MinIOService:
    """MinIOService с внедрённым мок-клиентом."""
    return MinIOService(client=mock_client)


# Тесты ensure_buckets


class TestEnsureBuckets:
    def test_creates_missing_buckets(
        self, service: MinIOService, mock_client: MagicMock
    ) -> None:
        """Бакеты, которых ещё нет, создаются."""
        mock_client.bucket_exists.return_value = False

        service.ensure_buckets()

        assert mock_client.make_bucket.call_count == len(BUCKETS)
        mock_client.make_bucket.assert_has_calls([call(b) for b in BUCKETS])

    def test_skips_existing_buckets(
        self, service: MinIOService, mock_client: MagicMock
    ) -> None:
        """Уже существующие бакеты не создаются повторно."""
        mock_client.bucket_exists.return_value = True

        service.ensure_buckets()

        mock_client.make_bucket.assert_not_called()

    def test_checks_each_bucket(
        self, service: MinIOService, mock_client: MagicMock
    ) -> None:
        """Каждый бакет из BUCKETS проверяется на существование."""
        mock_client.bucket_exists.return_value = True

        service.ensure_buckets()

        mock_client.bucket_exists.assert_has_calls([call(b) for b in BUCKETS])


# Тесты upload_file


class TestUploadFile:
    def test_uploads_with_given_object_name(
        self, service: MinIOService, mock_client: MagicMock
    ) -> None:
        """fput_object вызывается с переданным object_name и корректным content-type."""
        mock_client.presigned_get_object.return_value = "https://example.com/presigned"

        result = service.upload_file(
            "reports", "/tmp/report.xlsx", object_name="custom.xlsx"
        )

        mock_client.fput_object.assert_called_once_with(
            "reports",
            "custom.xlsx",
            "/tmp/report.xlsx",
            content_type=(
                "application/vnd.openxmlformats-officedocument" ".spreadsheetml.sheet"
            ),
        )
        assert result == "https://example.com/presigned"

    def test_infers_object_name_from_file_path(
        self, service: MinIOService, mock_client: MagicMock
    ) -> None:
        """object_name выводится из имени файла, если не передан явно."""
        mock_client.presigned_get_object.return_value = "https://example.com/presigned"

        service.upload_file("exports", "/tmp/data/export.csv")

        mock_client.fput_object.assert_called_once_with(
            "exports", "export.csv", "/tmp/data/export.csv", content_type="text/csv"
        )

    def test_returns_presigned_url_with_default_expiry(
        self, service: MinIOService, mock_client: MagicMock
    ) -> None:
        """Presigned-ссылка запрашивается со сроком действия по умолчанию 7 дней."""
        mock_client.presigned_get_object.return_value = "https://example.com/presigned"

        service.upload_file("imports", "/tmp/file.pdf")

        mock_client.presigned_get_object.assert_called_once_with(
            "imports", "file.pdf", expires=timedelta(days=7)
        )


# Тесты download_file / delete_file / list_files


class TestDownloadFile:
    def test_calls_fget_object(
        self, service: MinIOService, mock_client: MagicMock
    ) -> None:
        service.download_file("reports", "report.xlsx", "/tmp/report.xlsx")

        mock_client.fget_object.assert_called_once_with(
            "reports", "report.xlsx", "/tmp/report.xlsx"
        )


class TestDeleteFile:
    def test_calls_remove_object(
        self, service: MinIOService, mock_client: MagicMock
    ) -> None:
        service.delete_file("reports", "report.xlsx")

        mock_client.remove_object.assert_called_once_with("reports", "report.xlsx")


class TestListFiles:
    def test_calls_list_objects_with_prefix(
        self, service: MinIOService, mock_client: MagicMock
    ) -> None:
        mock_client.list_objects.return_value = iter([])

        result = service.list_files("reports", prefix="2024/")

        mock_client.list_objects.assert_called_once_with(
            "reports", prefix="2024/", recursive=True
        )
        assert result == []

    def test_calls_list_objects_without_prefix(
        self, service: MinIOService, mock_client: MagicMock
    ) -> None:
        mock_client.list_objects.return_value = iter([])

        service.list_files("reports")

        mock_client.list_objects.assert_called_once_with(
            "reports", prefix=None, recursive=True
        )


# Тесты get_presigned_url


class TestGetPresignedUrl:
    def test_calls_presigned_get_object_with_custom_expiry(
        self, service: MinIOService, mock_client: MagicMock
    ) -> None:
        mock_client.presigned_get_object.return_value = "https://example.com/url"

        result = service.get_presigned_url("reports", "report.xlsx", expires_days=3)

        mock_client.presigned_get_object.assert_called_once_with(
            "reports", "report.xlsx", expires=timedelta(days=3)
        )
        assert result == "https://example.com/url"


# Тесты определения content-type


class TestGuessContentType:
    @pytest.mark.parametrize(
        ("file_path", "expected"),
        [
            (
                "report.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            ("legacy.xls", "application/vnd.ms-excel"),
            ("data.csv", "text/csv"),
            ("document.pdf", "application/pdf"),
            ("payload.json", "application/json"),
            ("archive.zip", "application/octet-stream"),
            ("NoExtension", "application/octet-stream"),
        ],
    )
    def test_content_type_by_extension(self, file_path: str, expected: str) -> None:
        assert _guess_content_type(file_path) == expected
