from smart_dialer.config import Settings


def test_render_postgres_url_uses_installed_psycopg_driver() -> None:
    settings = Settings(
        database_url="postgresql://render_user:secret@internal-host/dialer"
    )

    assert settings.database_url == (
        "postgresql+psycopg://render_user:secret@internal-host/dialer"
    )
