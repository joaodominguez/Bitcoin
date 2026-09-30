from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("email", name="uq_contacts_email"),
        Index("ix_contacts_status", "status"),
        Index("ix_contacts_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    # active | unsubscribed | bounced | suppressed
    source: Mapped[str | None] = mapped_column(String(120))
    tags: Mapped[str | None] = mapped_column(String(500))  # comma-separated for MVP
    consent_note: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    memberships: Mapped[list[ListMember]] = relationship(back_populates="contact")


class ContactList(Base):
    __tablename__ = "lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    members: Mapped[list[ListMember]] = relationship(back_populates="list")


class ListMember(Base):
    __tablename__ = "list_members"
    __table_args__ = (
        UniqueConstraint("list_id", "contact_id", name="uq_list_contact"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    list_id: Mapped[int] = mapped_column(ForeignKey("lists.id", ondelete="CASCADE"))
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    list: Mapped[ContactList] = relationship(back_populates="members")
    contact: Mapped[Contact] = relationship(back_populates="memberships")


class Suppression(Base):
    __tablename__ = "suppressions"
    __table_args__ = (UniqueConstraint("email", name="uq_suppressions_email"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), default="manual")
    # unsubscribe | bounce | complaint | manual
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
