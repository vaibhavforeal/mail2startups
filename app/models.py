import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class StartupStatus(str, enum.Enum):
    DISCOVERED = "discovered"
    ENRICHED = "enriched"
    DRAFTED = "drafted"
    IN_REVIEW = "in_review"
    QUEUED = "queued"
    SENT = "sent"
    REPLIED = "replied"
    BOUNCED = "bounced"
    NO_RESPONSE = "no_response"
    DEAD = "dead"


class DraftMode(str, enum.Enum):
    FORMAL = "formal"
    CASUAL = "casual"


class DraftStatus(str, enum.Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class MessageType(str, enum.Enum):
    INITIAL = "initial"
    FOLLOWUP = "followup"


class MessageStatus(str, enum.Enum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    BOUNCED = "bounced"
    REPLIED = "replied"


class InboxKind(str, enum.Enum):
    REPLY = "reply"
    BOUNCE = "bounce"


class ReplyLabel(str, enum.Enum):
    INTERESTED = "interested"
    REJECTION = "rejection"
    AUTO_REPLY = "auto_reply"
    OTHER = "other"


class Startup(Base):
    __tablename__ = "startups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source: Mapped[str] = mapped_column(String(50))
    location: Mapped[str] = mapped_column(String(200), default="")
    industry: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    team_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    founder_names: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[StartupStatus] = mapped_column(
        Enum(StartupStatus, values_callable=lambda e: [m.value for m in e]),
        default=StartupStatus.DISCOVERED,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    contacts: Mapped[list["Contact"]] = relationship(back_populates="startup")


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    startup_id: Mapped[int] = mapped_column(ForeignKey("startups.id"))
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str] = mapped_column(String(100), default="")
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    found_via: Mapped[str] = mapped_column(String(30), default="scraped")  # scraped|api|pattern_guess|generic
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    verified: Mapped[bool] = mapped_column(default=False)

    startup: Mapped["Startup"] = relationship(back_populates="contacts")


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    startup_id: Mapped[int] = mapped_column(ForeignKey("startups.id"))
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    mode: Mapped[DraftMode] = mapped_column(
        Enum(DraftMode, values_callable=lambda e: [m.value for m in e]),
        default=DraftMode.FORMAL,
    )
    subject: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    resume_pdf_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[DraftStatus] = mapped_column(
        Enum(DraftStatus, values_callable=lambda e: [m.value for m in e]),
        default=DraftStatus.PENDING_REVIEW,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("drafts.id"))
    type: Mapped[MessageType] = mapped_column(
        Enum(MessageType, values_callable=lambda e: [m.value for m in e]),
        default=MessageType.INITIAL,
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    smtp_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus, values_callable=lambda e: [m.value for m in e]),
        default=MessageStatus.QUEUED,
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    startup_id: Mapped[int | None] = mapped_column(ForeignKey("startups.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(50))  # e.g. discovered, bounce, reply, error, retry, pause
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InboxMessage(Base):
    __tablename__ = "inbox_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    startup_id: Mapped[int] = mapped_column(ForeignKey("startups.id"))
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True)
    kind: Mapped[InboxKind] = mapped_column(
        Enum(InboxKind, values_callable=lambda e: [m.value for m in e]))
    imap_message_id: Mapped[str] = mapped_column(String(255), unique=True)
    imap_uid: Mapped[int] = mapped_column(Integer, default=0)
    from_addr: Mapped[str] = mapped_column(String(255), default="")
    subject: Mapped[str] = mapped_column(String(500), default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    label: Mapped[ReplyLabel | None] = mapped_column(
        Enum(ReplyLabel, values_callable=lambda e: [m.value for m in e]),
        nullable=True)
    matched_message_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow)


class EnrichmentUsage(Base):
    __tablename__ = "enrichment_usage"
    __table_args__ = (UniqueConstraint("provider", "period", name="uq_provider_period"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(50))
    period: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    calls: Mapped[int] = mapped_column(Integer, default=0)


class CampaignState(Base):
    __tablename__ = "campaign_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # singleton, id=1
    paused: Mapped[bool] = mapped_column(default=False)
    paused_reason: Mapped[str] = mapped_column(String(200), default="")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    first_send_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    last_imap_uid: Mapped[int] = mapped_column(Integer, default=0)
    imap_uidvalidity: Mapped[int] = mapped_column(Integer, default=0)
