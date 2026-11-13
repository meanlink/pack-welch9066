import time
from fastapi import HTTPException
from sqlalchemy import select
from src.models import Dataset
from src.services import intake as intake_service
from src.utils.serialization import checksum


def create(db, data, actor='seed'):
    name=data.name.strip()
    if len(name)<2: raise HTTPException(422, '批次名称去除空格后至少需要两个字符')
    if db.scalar(select(Dataset).where(Dataset.name==name)):
        raise HTTPException(409, '该数据批次名已存在，请修改名称')
    d=Dataset(name=name, source=data.source, records=[], checksum=checksum([]), created_at=time.time())
    db.add(d); db.flush()
    intake_service.merge(db, d, data.source, actor, data.records)
    return d
