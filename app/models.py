from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

ACTIVE = "active"
REMOVED = "removed"


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    kt_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    alias: Mapped[str | None] = mapped_column(String(255), nullable=True)
    geo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    streams: Mapped[list["Stream"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan", order_by="Stream.position"
    )


class Stream(Base):
    __tablename__ = "streams"

    id: Mapped[int] = mapped_column(primary_key=True)
    kt_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    kt_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kt_schema: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # поток с офферами редактируем; остальные (например, Google) просто показываем строкой
    has_offers: Mapped[bool] = mapped_column(Boolean, default=False)

    campaign: Mapped[Campaign] = relationship(back_populates="streams")
    offers: Mapped[list["StreamOffer"]] = relationship(
        back_populates="stream", cascade="all, delete-orphan", order_by="StreamOffer.id"
    )


class StreamOffer(Base):
    """Привязка оффера к потоку.

    share/status/pinned — черновик (то, что видно в редакторе).
    pub_* — последнее состояние, известное в Keitaro (после push или fetch).
    pub_status = None значит, что оффер добавлен в черновике и в Keitaro ещё не уходил.
    """

    __tablename__ = "stream_offers"
    __table_args__ = (UniqueConstraint("stream_id", "offer_id", name="uq_stream_offer"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    stream_id: Mapped[int] = mapped_column(ForeignKey("streams.id", ondelete="CASCADE"))
    offer_id: Mapped[int] = mapped_column(Integer)
    offer_name: Mapped[str] = mapped_column(String(512), default="")

    share: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default=ACTIVE)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)

    pub_share: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pub_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    pub_pinned: Mapped[bool] = mapped_column(Boolean, default=False)

    removed_in_kt: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    stream: Mapped[Stream] = relationship(back_populates="offers")

    @property
    def is_active(self) -> bool:
        return self.status == ACTIVE

    @property
    def is_changed(self) -> bool:
        if self.pub_status is None:
            return self.status == ACTIVE
        if self.status != self.pub_status:
            return True
        return self.status == ACTIVE and self.share != self.pub_share
