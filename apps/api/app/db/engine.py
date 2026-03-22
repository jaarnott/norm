import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://norm:norm@localhost:5432/norm",
)

engine = create_engine(DATABASE_URL, pool_size=5, max_overflow=15)
SessionLocal = sessionmaker(bind=engine)


def get_db():
    """FastAPI dependency that yields a DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
