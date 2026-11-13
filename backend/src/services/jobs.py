import time
from fastapi import HTTPException
from redis import Redis
from src.config.settings import REDIS_URL
from src.models import Dataset, Job, JobSnapshot
from src.services.business import metadata
from src.services import intake
from src.utils.serialization import checksum


def create(db, data, user):
    dataset = db.get(Dataset, data.dataset_id)
    if not dataset: raise HTTPException(404, '数据批次不存在')
    allowed = {p['key']: p for p in metadata().get('parameters', [])}
    if set(data.params) - set(allowed): raise HTTPException(422, '包含未知分析参数')
    params = {k: p['default'] for k, p in allowed.items()}
    params.update(data.params)
    for key, p in allowed.items():
        v = params[key]
        if p['type'] == 'number':
            try:
                v = float(v)
                if not (p.get('min', -1e9) <= v <= p.get('max', 1e9)): raise ValueError()
                params[key] = v
            except (ValueError, TypeError): raise HTTPException(422, f'{p["label"]} 超出范围或非数值')
        elif str(v) not in [str(o['value']) for o in p.get('options', [])]:
            raise HTTPException(422, f'{p["label"]} 不是有效选项')
    # 排队即冻结当时的有效片段；之后的补传、更正与撤回不改变这次比较。
    records = intake.effective_records(db, dataset)
    if not records: raise HTTPException(409, '该数据批次当前没有有效片段，请先补传或更正数据')
    j = Job(name=data.name, dataset_id=data.dataset_id, params=params, created_by=user.id,
            created_at=time.time(), status='queued')
    db.add(j)
    db.flush()
    db.add(JobSnapshot(job_id=j.id, dataset_id=dataset.id, checksum=checksum(records),
                       records=records, captured_at=time.time()))
    db.flush()
    return j


def enqueue(job_id):
    try:
        r = Redis.from_url(REDIS_URL); r.rpush('analysis:queue', job_id); r.close()
    except Exception:
        pass  # SQL queued status is the durable fallback
