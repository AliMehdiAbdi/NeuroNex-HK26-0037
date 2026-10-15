"""Pydantic models for Firebase authentication, user profiles, and roles."""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class FirebaseSyncRequest(BaseModel):
    """Payload sent by client after authenticating with Firebase Client SDK."""

    id_token: str = Field(description="Firebase ID token from client SDK")
    full_name: Optional[str] = Field(default=None, max_length=150)
    phone: Optional[str] = Field(default=None, max_length=30)
    photo_url: Optional[str] = Field(default=None, max_length=500)


class DevLoginRequest(BaseModel):
    """Bypass authentication for local development when Firebase is disabled."""

    email: EmailStr
    role: str = Field(default="citizen", description="Role to assume: citizen, municipal, emergency")


class TokenResponse(BaseModel):
    """Access token response for offline dev sessions."""

    access_token: str
    token_type: str = "bearer"
    user_id: int
    email: str
    full_name: str
    roles: List[str]


class UserProfileResponse(BaseModel):
    """Public user profile with assigned roles and verification status."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    firebase_uid: Optional[str] = None
    email: str
    full_name: str
    phone: Optional[str] = None
    photo_url: Optional[str] = None
    is_active: bool
    is_verified: bool
    roles: List[str]
    created_at: datetime


class UpdateProfileRequest(BaseModel):
    """Profile attributes modifiable by the authenticated user."""

    full_name: Optional[str] = Field(default=None, min_length=2, max_length=150)
    phone: Optional[str] = Field(default=None, max_length=30)
    photo_url: Optional[str] = Field(default=None, max_length=500)
