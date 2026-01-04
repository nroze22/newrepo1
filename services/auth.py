"""
Authentication and authorization service for the AI Consultant Platform.
Implements JWT-based authentication and usage tracking.
"""

from datetime import datetime, timedelta
from typing import Optional
import jwt
from passlib.context import CryptContext
from pydantic import BaseModel
import os

# Configuration
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class User(BaseModel):
    user_id: str
    email: str
    subscription_tier: str  # starter, professional, enterprise
    sessions_remaining: int
    total_sessions: int
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    user_id: Optional[str] = None


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password."""
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str) -> Optional[TokenData]:
    """Verify and decode a JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            return None
        return TokenData(user_id=user_id)
    except jwt.PyJWTError:
        return None


class SubscriptionManager:
    """Manage subscription tiers and usage limits."""

    TIERS = {
        "starter": {
            "sessions_per_month": 5,
            "price": 199,
            "features": ["Basic analysis", "Session recording", "Standard support"]
        },
        "professional": {
            "sessions_per_month": 20,
            "price": 599,
            "features": ["Advanced analysis", "Priority support", "Custom consultants", "API access"]
        },
        "enterprise": {
            "sessions_per_month": -1,  # Unlimited
            "price": 1499,
            "features": ["Unlimited sessions", "Dedicated support", "White-label", "SLA guarantee"]
        }
    }

    @staticmethod
    def check_session_limit(user: User) -> bool:
        """Check if user has sessions remaining."""
        if user.subscription_tier == "enterprise":
            return True
        return user.sessions_remaining > 0

    @staticmethod
    def consume_session(user: User) -> User:
        """Consume one session from user's quota."""
        if user.subscription_tier != "enterprise":
            user.sessions_remaining -= 1
        user.total_sessions += 1
        return user

    @staticmethod
    def get_tier_info(tier: str) -> dict:
        """Get information about a subscription tier."""
        return SubscriptionManager.TIERS.get(tier, SubscriptionManager.TIERS["starter"])
