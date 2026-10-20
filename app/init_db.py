"""Initialize the local database: python -m app.init_db"""
from .database import initialize_database
from .settings import settings


if __name__ == "__main__":
    initialize_database()
    print(f"Database initialized at {settings.database_path}")
