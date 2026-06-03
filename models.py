from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.sql import func
from database import Base


class User(Base):
    __tablename__ = "users"

    id               = Column(Integer, primary_key=True, index=True)
    email            = Column(String, unique=True, nullable=False)
    alpaca_api_key   = Column(String, nullable=False)
    alpaca_secret_key = Column(String, nullable=False)
    alpaca_base_url  = Column(String, nullable=False, default="https://paper-api.alpaca.markets")
    backtest_sharpe  = Column(Float, nullable=False)
    alert_days       = Column(Integer, nullable=False, default=30)
    alert_threshold  = Column(Float, nullable=False, default=0.3)
    created_at       = Column(DateTime(timezone=True), server_default=func.now())
