"""Security, Firebase token verification, and Role-Based Access Control (RBAC)."""

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.core.database import get_db
from backend.app.models.user import Role, User, UserRole

http_bearer = HTTPBearer(auto_error=False)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create local JWT access token for offline dev sessions."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def verify_firebase_token(token: str) -> Dict[str, Any]:
    """
    Validates a Firebase ID Token.

    In production: Validates with Firebase Admin SDK against Google certs.
    In local dev mode: Supports demo and mock tokens for offline testing.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization token is missing",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Local development & mock tokens bypass
    if settings.FIREBASE_AUTH_DISABLED_FOR_DEV:
        if token.startswith("demo_citizen") or token == "citizen_token":
            return {
                "uid": "demo_citizen_uid_123",
                "email": "citizen@drainiq.org",
                "name": "Aarav Sharma",
                "role": "CITIZEN",
            }
        elif token.startswith("demo_operator") or token == "operator_token":
            return {
                "uid": "demo_operator_uid_456",
                "email": "operator@drainiq.gov.in",
                "name": "Rohan Verma",
                "role": "MUNICIPAL_OPERATOR",
            }
        elif token.startswith("demo_admin") or token == "admin_token":
            return {
                "uid": "demo_admin_uid_789",
                "email": "admin@drainiq.gov.in",
                "name": "Dr. Neha Gupta",
                "role": "MUNICIPAL_ADMIN",
            }

    # Verify with Firebase Admin SDK if configured
    try:
        import firebase_admin
        from firebase_admin import auth as firebase_auth

        # Initialize Firebase Admin app once if credentials provided
        if not firebase_admin._apps:
            if settings.FIREBASE_CREDENTIALS_PATH:
                cred = firebase_admin.credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
                firebase_admin.initialize_app(cred)
            else:
                firebase_admin.initialize_app(options={"projectId": settings.FIREBASE_PROJECT_ID})

        decoded_token = firebase_auth.verify_id_token(token)
        return decoded_token
    except Exception as exc:
        # If in dev mode, allow token decoding fallback
        if settings.FIREBASE_AUTH_DISABLED_FOR_DEV:
            return {
                "uid": f"dev_{token[:12]}",
                "email": f"user_{token[:8]}@drainiq.local",
                "name": "DrainIQ User",
            }
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Firebase authentication token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    auth_header: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency resolving and auto-provisioning the current user via Firebase Auth."""
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_data = verify_firebase_token(auth_header.credentials)
    uid = token_data.get("uid")
    email = token_data.get("email") or f"{uid}@firebase.drainiq.org"
    name = token_data.get("name") or token_data.get("display_name") or "DrainIQ Citizen"
    phone = token_data.get("phone_number")

    user = db.query(User).filter((User.firebase_uid == uid) | (User.email == email)).first()

    if not user:
        # Auto-provision new Firebase user in local database
        user = User(
            firebase_uid=uid,
            email=email,
            full_name=name,
            phone=phone,
            is_active=True,
            is_verified=token_data.get("email_verified", True),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        # Default role: CITIZEN
        citizen_role = db.query(Role).filter(Role.name == "CITIZEN").first()
        if citizen_role:
            ur = UserRole(user_id=user.id, role_id=citizen_role.id)
            db.add(ur)
            db.commit()
            db.refresh(user)

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated",
        )

    return user


def require_roles(allowed_roles: List[str]) -> Callable:
    """Enforces role-based access control (RBAC)."""

    async def role_checker(current_user: User = Depends(get_current_user)) -> User:
        user_roles = current_user.role_names
        if "SUPER_ADMIN" in user_roles:
            return current_user
        if not any(role in user_roles for role in allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {allowed_roles}",
            )
        return current_user

    return role_checker
