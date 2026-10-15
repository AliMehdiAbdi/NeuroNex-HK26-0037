"""Firebase authentication, user synchronization, profile management, and dev login."""

from datetime import datetime, timezone
from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.core.database import get_db
from backend.app.core.rate_limit import limiter
from backend.app.core.security import (
    create_access_token,
    get_current_user,
    verify_firebase_token,
)
from backend.app.models.citizen import NotificationPreference
from backend.app.models.user import Role, User, UserRole
from backend.app.schemas.auth import (
    DevLoginRequest,
    FirebaseSyncRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserProfileResponse,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/sync", response_model=UserProfileResponse, summary="Sync Firebase User Session")
async def sync_firebase_user(
    payload: FirebaseSyncRequest,
    db: Session = Depends(get_db),
):
    """Verify client Firebase ID token and synchronize or auto-provision local user profile."""
    claims = verify_firebase_token(payload.id_token)
    uid = claims.get("uid")
    email = claims.get("email")

    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Firebase token does not contain a verified email address.",
        )

    user = db.query(User).filter((User.firebase_uid == uid) | (User.email == email.lower())).first()

    if not user:
        full_name = payload.full_name or claims.get("name") or email.split("@")[0].title()
        user = User(
            firebase_uid=uid,
            email=email.lower(),
            full_name=full_name,
            phone=payload.phone or claims.get("phone_number"),
            photo_url=payload.photo_url or claims.get("picture"),
            is_active=True,
            is_verified=bool(claims.get("email_verified", False)),
            created_at=datetime.now(timezone.utc),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        # Default to CITIZEN role
        citizen_role = db.query(Role).filter(Role.name == "CITIZEN").first()
        if not citizen_role:
            citizen_role = Role(name="CITIZEN", description="Citizen access level")
            db.add(citizen_role)
            db.commit()
            db.refresh(citizen_role)

        db.add(UserRole(user_id=user.id, role_id=citizen_role.id))

        # Default notification preferences
        pref = NotificationPreference(
            user_id=user.id,
            enable_rain_alerts=True,
            enable_flood_alerts=True,
            enable_drainage_alerts=True,
            enable_emergency_broadcasts=True,
            min_severity_threshold="MODERATE",
            channel_dashboard=True,
            channel_browser_push=True,
            channel_sms=False,
        )
        db.add(pref)
        db.commit()
        db.refresh(user)
    else:
        # Update Firebase UID and photo if newly linked
        updated = False
        if not user.firebase_uid and uid:
            user.firebase_uid = uid
            updated = True
        if payload.full_name and user.full_name != payload.full_name:
            user.full_name = payload.full_name
            updated = True
        if payload.photo_url and user.photo_url != payload.photo_url:
            user.photo_url = payload.photo_url
            updated = True
        if updated:
            db.commit()
            db.refresh(user)

    return UserProfileResponse(
        id=user.id,
        firebase_uid=user.firebase_uid,
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        photo_url=user.photo_url,
        is_active=user.is_active,
        is_verified=user.is_verified,
        roles=user.role_names,
        created_at=user.created_at,
    )


@router.post("/dev-login", response_model=TokenResponse, summary="Dev Mode Authentication Bypass")
async def dev_login(
    payload: DevLoginRequest,
    db: Session = Depends(get_db),
):
    """Local offline token generator for testing when Firebase Auth credentials are off."""
    if not settings.FIREBASE_AUTH_DISABLED_FOR_DEV:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dev login bypass is disabled in this environment.",
        )

    user = db.query(User).filter(User.email == payload.email.lower()).first()
    target_role_name = payload.role.upper()

    if not user:
        user = User(
            email=payload.email.lower(),
            full_name=payload.email.split("@")[0].title(),
            is_active=True,
            is_verified=True,
            created_at=datetime.now(timezone.utc),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        role = db.query(Role).filter(Role.name == target_role_name).first()
        if not role:
            role = Role(name=target_role_name, description=f"{target_role_name} access")
            db.add(role)
            db.commit()
            db.refresh(role)

        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
        db.refresh(user)

    roles = user.role_names or [target_role_name]
    token = create_access_token({"sub": str(user.id), "email": user.email, "roles": roles})

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        roles=roles,
    )


@router.get("/me", response_model=UserProfileResponse, summary="Get Current Authenticated Profile")
async def get_current_user_profile(
    current_user: User = Depends(get_current_user),
):
    """Return user profile with assigned RBAC roles and verification status."""
    return UserProfileResponse(
        id=current_user.id,
        firebase_uid=current_user.firebase_uid,
        email=current_user.email,
        full_name=current_user.full_name,
        phone=current_user.phone,
        photo_url=current_user.photo_url,
        is_active=current_user.is_active,
        is_verified=current_user.is_verified,
        roles=current_user.role_names,
        created_at=current_user.created_at,
    )


@router.put("/me", response_model=UserProfileResponse, summary="Update Authenticated Profile")
async def update_current_user_profile(
    payload: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Modify user profile attributes."""
    if payload.full_name is not None:
        current_user.full_name = payload.full_name
    if payload.phone is not None:
        current_user.phone = payload.phone
    if payload.photo_url is not None:
        current_user.photo_url = payload.photo_url

    db.commit()
    db.refresh(current_user)

    return UserProfileResponse(
        id=current_user.id,
        firebase_uid=current_user.firebase_uid,
        email=current_user.email,
        full_name=current_user.full_name,
        phone=current_user.phone,
        photo_url=current_user.photo_url,
        is_active=current_user.is_active,
        is_verified=current_user.is_verified,
        roles=current_user.role_names,
        created_at=current_user.created_at,
    )
