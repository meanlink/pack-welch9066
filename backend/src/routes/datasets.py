from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from src.config.database import get_db
from src.models import Dataset, DatasetIntake
from src.schemas.dataset import DatasetInput, DatasetMergeInput
from src.middlewares.auth import current_user, can_write
from src.services import datasets, intake
from src.services.audit import record
from src.utils.serialization import dataset_dict
router=APIRouter(prefix='/datasets')


def _dataset(db, id):
    d=db.get(Dataset,id)
    if not d:raise HTTPException(404,'数据批次不存在')
    return d


@router.get('')
def listing(db=Depends(get_db),u=Depends(current_user)):
    rows=[]
    for d in db.scalars(select(Dataset).order_by(Dataset.id.desc())):
        _,withdrawn=intake.version_map(db,d)
        rows.append({**dataset_dict(d),'withdrawn':withdrawn})
    return rows


@router.post('',status_code=201)
def create(data:DatasetInput,db=Depends(get_db),u=Depends(can_write)):
    d=datasets.create(db,data,u.email); record(db,u.email,'dataset.create',{'id':d.id,'rows':len(d.records)})
    db.commit(); return dataset_dict(d)


@router.get('/{id}')
def detail(id:int,db=Depends(get_db),u=Depends(current_user)):
    d=_dataset(db,id)
    _,withdrawn=intake.version_map(db,d)
    return {**dataset_dict(d),'withdrawn':withdrawn,'records':intake.effective_records(db,d),
            'segments':intake.detail_rows(db,d),'intakes':[intake.intake_dict(i) for i in db.scalars(select(DatasetIntake).where(DatasetIntake.dataset_id==d.id).order_by(DatasetIntake.id.desc()))]}


@router.get('/{id}/intakes')
def intakes(id:int,db=Depends(get_db),u=Depends(current_user)):
    d=_dataset(db,id)
    return [intake.intake_dict(i) for i in db.scalars(select(DatasetIntake).where(DatasetIntake.dataset_id==d.id).order_by(DatasetIntake.id.desc()))]


@router.post('/{id}/intakes',status_code=201)
def merge(id:int,data:DatasetMergeInput,db=Depends(get_db),u=Depends(can_write)):
    d=_dataset(db,id)
    try:
        result=intake.merge(db,d,data.source,u.email,data.records)
    except intake.MergeRejected as error:
        # 拒收不写任何片段：先回滚本次改动，再为接入历史与审计留下拒收记录。
        db.rollback()
        taken=intake.record_rejected(db,id,data.source,u.email,len(data.records),error.detail,error.counts,error.details)
        record(db,u.email,'dataset.merge.rejected',{'id':id,'intake':taken.id,'rows':len(data.records),'error':error.detail,'counts':error.counts})
        db.commit()
        raise
    record(db,u.email,'dataset.merge',{'id':d.id,'intake':result.id,'counts':result.counts,'rows':len(d.records)})
    db.commit()
    _,withdrawn=intake.version_map(db,d)
    return {**intake.intake_dict(result),'batch':{**dataset_dict(d),'withdrawn':withdrawn}}
