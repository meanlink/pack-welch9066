from sqlalchemy import String, JSON, Double, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class JobSnapshot(Base):
    """分析任务排队时冻结的有效片段，后续补传不改变该次比较。"""
    __tablename__ = 'job_snapshots'
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'), primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    checksum: Mapped[str] = mapped_column(String(64))
    records: Mapped[list] = mapped_column(JSON)
    captured_at: Mapped[float] = mapped_column(Double)
