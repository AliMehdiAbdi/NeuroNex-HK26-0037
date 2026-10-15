"""Database configuration and session management using SQLAlchemy 2.0."""

from contextlib import contextmanager
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from backend.app.config import settings

db_url = settings.clean_database_url()
is_sqlite = "sqlite" in db_url.lower()

if is_sqlite:
    engine = create_engine(
        db_url,
        echo=False,
        connect_args={"check_same_thread": False},
    )
else:
    engine = create_engine(
        db_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency providing transactional database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_context() -> Generator[Session, None, None]:
    """Context manager for standalone scripts or background tasks."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Initializes schema tables and default roles."""
    from backend.app.models.user import User, Role, UserRole  # noqa: F401

    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        role_names = ["CITIZEN", "MUNICIPAL_OPERATOR", "MUNICIPAL_ADMIN", "SUPER_ADMIN"]
        roles_by_name = {}
        for r_name in role_names:
            role = db.query(Role).filter(Role.name == r_name).first()
            if not role:
                role = Role(
                    name=r_name, description=f"{r_name.replace('_', ' ').title()} access level"
                )
                db.add(role)
                db.commit()
                db.refresh(role)
            roles_by_name[r_name] = role

        # Seed default dev accounts if not present
        demo_users = [
            {
                "email": "citizen@drainiq.org",
                "firebase_uid": "demo_citizen_uid_123",
                "full_name": "Aarav Sharma",
                "phone": "+91 98765 43210",
                "role": "CITIZEN",
            },
            {
                "email": "operator@drainiq.gov.in",
                "firebase_uid": "demo_operator_uid_456",
                "full_name": "Rohan Verma (Emergency Coordinator)",
                "phone": "+91 98765 43211",
                "role": "MUNICIPAL_OPERATOR",
            },
            {
                "email": "admin@drainiq.gov.in",
                "firebase_uid": "demo_admin_uid_789",
                "full_name": "Dr. Neha Gupta (Disaster Response Chief)",
                "phone": "+91 98765 43212",
                "role": "MUNICIPAL_ADMIN",
            },
        ]

        for u in demo_users:
            existing = db.query(User).filter(User.email == u["email"]).first()
            if not existing:
                new_user = User(
                    email=u["email"],
                    firebase_uid=u["firebase_uid"],
                    full_name=u["full_name"],
                    phone=u["phone"],
                    is_active=True,
                    is_verified=True,
                )
                db.add(new_user)
                db.commit()
                db.refresh(new_user)

                target_role = roles_by_name.get(u["role"])
                if target_role:
                    ur = UserRole(user_id=new_user.id, role_id=target_role.id)
                    db.add(ur)
                    db.commit()
