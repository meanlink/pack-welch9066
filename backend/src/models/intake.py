from sqlalchemy import String, JSON, Double, Integer, Boolean, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class DatasetRevision(Base):
    """一条片段在一个版本上的有效内容；同片段以最高版本为准。"""
    __tablename__ = 'dataset_revisions'
    __table_args__ = (
        UniqueConstraint('dataset_id', 'run', 'version', name='uq_dataset_run_version'),
        Index('ix_dataset_revisions_dataset_id', 'dataset_id'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    intake_id: Mapped[int | None] = mapped_column(ForeignKey('dataset_intakes.id'), nullable=True)
    run: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    withdrawn: Mapped[bool] = mapped_column(Boolean, default=False)
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Double)


class DatasetIntake(Base):
    """每次向原批次合并的接入记录，保留新增/替换/忽略/撤回明细。"""
    __tablename__ = 'dataset_intakes'
    __table_args__ = (Index('ix_dataset_intakes_dataset_id', 'dataset_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    source: Mapped[str] = mapped_column(String(200), default='manual.json')
    actor: Mapped[str] = mapped_column(String(150), default='')
    status: Mapped[str] = mapped_column(String(20), default='accepted')
    checksum: Mapped[str] = mapped_column(String(64), default='')
    rows: Mapped[int] = mapped_column(Integer, default=0)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    details: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str] = mapped_column(String(1000), default='')
    created_at: Mapped[float] = mapped_column(Double)
