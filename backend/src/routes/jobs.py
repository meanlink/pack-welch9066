from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from src.config.database import get_db
from src.models import Job, JobSnapshot
from src.schemas.job import JobInput
from src.middlewares.auth import current_user, can_write, admin_only
from src.services import jobs
from src.services.audit import record
from src.utils.serialization import job_dict
router=APIRouter(prefix='/jobs')
@router.get('')
def listing(db=Depends(get_db),u=Depends(current_user)):
    jobs=[job_dict(j) for j in db.scalars(select(Job).order_by(Job.id.desc()).limit(100))]
    snaps={s.job_id:s for s in db.scalars(select(JobSnapshot).where(JobSnapshot.job_id.in_([j['id'] for j in jobs])))}
    for item in jobs:
        s=snaps.get(item['id'])
        if s:item['snapshot']={'checksum':s.checksum,'rows':len(s.records),'captured_at':s.captured_at}
    return jobs
@router.post('',status_code=201)
def create(data:JobInput,db=Depends(get_db),u=Depends(can_write)):
    j=jobs.create(db,data,u); snap=db.get(JobSnapshot,j.id)
    record(db,u.email,'job.create',{'id':j.id,'params':j.params,'snapshot':snap.checksum if snap else None,'rows':len(snap.records) if snap else 0})
    db.commit(); jobs.enqueue(j.id); return job_dict(j,snap)
@router.get('/{id}')
def detail(id:int,db=Depends(get_db),u=Depends(current_user)):
    j=db.get(Job,id)
    if not j: raise HTTPException(404,'分析任务不存在')
    return job_dict(j,db.get(JobSnapshot,id))
@router.post('/{id}/approve')
def approve(id:int,db=Depends(get_db),u=Depends(admin_only)):
    j=db.get(Job,id)
    if not j: raise HTTPException(404,'分析任务不存在')
    if j.status!='completed':raise HTTPException(409,'仅已完成结果可以批准')
    j.approved=True; record(db,u.email,'job.approve',{'id':id}); db.commit()
    return job_dict(j)
