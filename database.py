from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv
import os

load_dotenv()

# The engine is the connection to the database
engine = create_engine(os.getenv("DATABASE_URL"))

# SessionLocal is a factory — every time you need to talk to the db you open a session
SessionLocal = sessionmaker(bind=engine)

# Base is what all models inherit from — it lets SQLAlchemy know about your tables
Base = declarative_base()


def get_db():
    """
    Dependency used by FastAPI endpoints to get a database session.
    Opens a session, yields it to the endpoint, then closes it when done.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
