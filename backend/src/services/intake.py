"""批次补传：片段版本、同版冲突、撤回与可追溯明细。

规则：
- 同一 (片段, 版本) 的重复投递若内容一致，忽略且不新增样本；
- 若内容不一致，整批拒收（不写入任何一行）；
- 低于当前最高版本且从未接入的版本，不覆盖新值，记为忽略；
- 撤回按 (片段, 版本) 生效；撤回当前最高版本后片段退出分析，
  投递更高版本可恢复；更高版本也可用于更正。
"""
import math
import re
import time

from fastapi import HTTPException
from sqlalchemy import select

from src.models import Dataset, DatasetIntake, DatasetRevision
from src.services.business import metadata
from src.utils.serialization import checksum

MAX_DETAILS = 200
ACTIONS = ('added', 'replaced', 'ignored', 'withdrawn')
_TRUTHY = {'true', '1', 'yes', 'y', '是', '撤回'}
_FALSY = {'false', '0', 'no', 'n', '否'}


class MergeRejected(HTTPException):
    """整批拒收；details 与 counts 供接入历史与界面复核。"""

    def __init__(self, status_code, message, details=None, counts=None):
        super().__init__(status_code=status_code, detail=message)
        self.details = details or []
        self.counts = counts or {}


def _int_version(row):
    raw = row.get('version')
    if raw is None or raw == '':
        return 1
    value = None
    if isinstance(raw, bool):
        value = None
    elif isinstance(raw, int):
        value = raw
    elif isinstance(raw, float) and raw.is_integer():
        value = int(raw)
    elif isinstance(raw, str) and re.fullmatch(r'[0-9]+', raw.strip()):
        value = int(raw.strip())
    if value is None or not 1 <= value <= 10000:
        raise MergeRejected(422, '片段版本必须为 1–10000 的整数')
    return value


def _bool_withdrawn(row):
    raw = row.get('withdrawn')
    if raw is None or raw == '':
        return False
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and raw in (0, 1):
        return bool(raw)
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in _TRUTHY:
            return True
        if text in _FALSY:
            return False
    raise MergeRejected(422, '撤回标记只能为 true 或 false')


def _content(row):
    """校验并返回该行的业务内容；版本与撤回标记不进入内容指纹。"""
    for column in metadata()['columns']:
        value = row.get(column['key'])
        if value is None or value == '':
            raise MergeRejected(422, f'缺少字段 {column["label"]}')
        if column['type'] == 'number':
            try:
                if isinstance(value, bool) or not math.isfinite(float(value)):
                    raise ValueError()
            except (TypeError, ValueError, OverflowError):
                raise MergeRejected(422, f'{column["label"]} 必须为有限数值')
    run = str(row['run']).strip()
    if len(run) > 120:
        raise MergeRejected(422, '片段号超过 120 个字符')
    return {'run': run, 'arm': str(row['arm']).strip(),
            'shift': str(row['shift']).strip(), 'seconds': float(row['seconds'])}


def prepare(records):
    """整批校验；任何一行不合法即整批拒收，不产生部分写入。"""
    entries, problems = [], []
    for index, row in enumerate(records):
        try:
            if not isinstance(row, dict):
                raise MergeRejected(422, '必须是对象')
            run = row.get('run')
            if run is None or str(run).strip() == '':
                raise MergeRejected(422, '缺少字段 片段')
            version = _int_version(row)
            withdrawn = _bool_withdrawn(row)
            if withdrawn:
                entries.append({'row': index + 1, 'run': str(run).strip(), 'version': version,
                                'withdrawn': True, 'content': None, 'fingerprint': None})
            else:
                content = _content(row)
                entries.append({'row': index + 1, 'run': content['run'], 'version': version,
                                'withdrawn': False, 'content': content,
                                'fingerprint': checksum(content)})
        except MergeRejected as error:
            problems.append({'row': index + 1, 'run': str(row.get('run', '')).strip(),
                             'version': row.get('version'), 'action': 'rejected',
                             'reason': str(error.detail)})
    if problems:
        counts = {action: 0 for action in ACTIONS}
        counts.update({'total': len(records), 'rejected': len(problems)})
        raise MergeRejected(422, _partial_message(problems, len(records)),
                            details=problems, counts=counts)
    return entries


def _legacy_state(dataset):
    """旧批次没有版本表时，把已有有效片段视为版本 1，后续补传可继续演进。"""
    state, order = {}, []
    for row in dataset.records or []:
        try:
            content = {c['key']: (float(row[c['key']]) if c['type'] == 'number' else str(row[c['key']]).strip())
                       for c in metadata()['columns'] if row.get(c['key']) not in (None, '')}
        except (KeyError, TypeError, ValueError):
            continue
        if len(content) != len(metadata()['columns']):
            continue
        run = content['run']
        if run in state:
            continue
        order.append(run)
        state[run] = {1: {'version': 1, 'content': content, 'fingerprint': checksum(content),
                          'withdrawn': False, 'obj': None, 'new': True, 'changed': True,
                          'bootstrap': True}}
    return state, order


def load_state(db, dataset, lock=False):
    query = select(DatasetRevision).where(DatasetRevision.dataset_id == dataset.id).order_by(DatasetRevision.id)
    if lock:
        query = query.with_for_update()
    revisions = db.scalars(query).all()
    if not revisions:
        return _legacy_state(dataset)
    state, order = {}, []
    for revision in revisions:
        if revision.run not in state:
            state[revision.run] = {}
            order.append(revision.run)
        state[revision.run][revision.version] = {
            'version': revision.version, 'content': revision.content,
            'fingerprint': revision.fingerprint, 'withdrawn': revision.withdrawn,
            'obj': revision, 'new': False, 'changed': False, 'bootstrap': False}
    return state, order


def _records_from_state(state, order):
    records = []
    for run in order:
        top = state[run][max(state[run])]
        if not top['withdrawn']:
            records.append(dict(top['content']))
    return records


def effective_records(db, dataset):
    state, order = load_state(db, dataset)
    return _records_from_state(state, order)


def version_map(db, dataset):
    """有效片段的最新版本，以及当前退出分析的片段数。"""
    state, order = load_state(db, dataset)
    versions, withdrawn = {}, 0
    for run in order:
        top = state[run][max(state[run])]
        if top['withdrawn']:
            withdrawn += 1
        else:
            versions[run] = top['version']
    return versions, withdrawn


def detail_rows(db, dataset):
    """并入原批次后的片段级当前状态，供数据接入页核查版本与撤回。"""
    state, order = load_state(db, dataset)
    rows = []
    for run in order:
        top = state[run][max(state[run])]
        rows.append({'run': run, 'version': top['version'], 'versions': len(state[run]),
                     'status': 'withdrawn' if top['withdrawn'] else 'active',
                     'arm': (top['content'] or {}).get('arm'),
                     'shift': (top['content'] or {}).get('shift'),
                     'seconds': (top['content'] or {}).get('seconds')})
    return rows


def _conflict_message(conflicts, total):
    shown = '；'.join(f'第 {d["row"]} 行片段 {d["run"]} 版本 {d["version"]} 与已接入内容不一致'
                      for d in conflicts[:6])
    more = f' 等 {len(conflicts)} 处' if len(conflicts) > 6 else ''
    return f'同版本内容冲突，整批 {total} 行已拒收：{shown}{more}'


def _partial_message(problems, total):
    shown = '；'.join(f'第 {p["row"]} 行：{p["reason"]}' for p in problems[:6])
    more = f' 等 {len(problems)} 处' if len(problems) > 6 else ''
    return f'整批 {total} 行已拒收：{shown}{more}'


def merge(db, dataset, source, actor, records):
    """把一组补传行合并进原批次；成功返回接入记录，冲突时整批拒收。"""
    entries = prepare(records)
    # 同一批次的补传按数据批次串行，避免并发重复新增或丢失替换。
    db.execute(select(Dataset.id).where(Dataset.id == dataset.id).with_for_update())
    state, order = load_state(db, dataset, lock=True)
    counts = {action: 0 for action in ACTIONS}
    counts['total'] = len(entries)
    details = []
    conflicts = []
    problems = []
    seen = {}

    for entry in entries:
        run, version = entry['run'], entry['version']
        # 同一批内的重复投递先与批内结果对齐，保证不会因批内重复新增样本。
        if (run, version) in seen:
            previous = seen[(run, version)]
            if entry['withdrawn'] == previous['withdrawn'] and entry['fingerprint'] == previous['fingerprint']:
                counts['ignored'] += 1
                details.append({'row': entry['row'], 'run': run, 'version': version,
                                'action': 'ignored', 'reason': '同批次内重复投递，不新增片段'})
            else:
                conflicts.append({'row': entry['row'], 'run': run, 'version': version,
                                  'action': 'conflict', 'reason': '同批次内同版本内容不一致'})
            continue
        seen[(run, version)] = entry

        versions = state.get(run, {})
        existing = versions.get(version)

        if entry['withdrawn']:
            if existing is None:
                problems.append({'row': entry['row'], 'run': run, 'version': version,
                                 'action': 'rejected', 'reason': f'片段 {run} 的版本 {version} 尚未接入，无法撤回'})
            elif existing['withdrawn']:
                counts['ignored'] += 1
                details.append({'row': entry['row'], 'run': run, 'version': version,
                                'action': 'ignored', 'reason': '该版本已撤回，无需重复撤回'})
            else:
                existing['withdrawn'] = True
                existing['changed'] = True
                counts['withdrawn'] += 1
                top = state[run][max(state[run])]
                reason = ('撤回当前版本，片段退出后续分析' if top['withdrawn']
                          else '撤回历史版本，当前有效内容不变')
                details.append({'row': entry['row'], 'run': run, 'version': version,
                                'action': 'withdrawn', 'reason': reason})
            continue

        if existing is not None:
            if existing['fingerprint'] == entry['fingerprint']:
                counts['ignored'] += 1
                reason = ('该版本已撤回；重复投递不恢复，请投递更高版本' if existing['withdrawn']
                          else '同版本重复投递，不新增片段')
                details.append({'row': entry['row'], 'run': run, 'version': version,
                                'action': 'ignored', 'reason': reason})
            else:
                # 同版本内容冲突：无论该版本是否仍为最新，整批拒收。
                conflicts.append({'row': entry['row'], 'run': run, 'version': version,
                                  'action': 'conflict', 'reason': '同版本内容与已接入内容不一致'})
            continue

        top_version = max(versions) if versions else 0
        if versions and version <= top_version:
            counts['ignored'] += 1
            details.append({'row': entry['row'], 'run': run, 'version': version,
                            'action': 'ignored', 'reason': '旧版本不覆盖新值'})
            continue
        was_withdrawn = bool(versions) and state[run][top_version]['withdrawn']
        action = 'replaced' if versions else 'added'
        versions[version] = {'version': version, 'content': entry['content'],
                             'fingerprint': entry['fingerprint'], 'withdrawn': False,
                             'obj': None, 'new': True, 'changed': True, 'bootstrap': False}
        state[run] = versions
        if run not in order:
            order.append(run)
        counts[action] += 1
        details.append({'row': entry['row'], 'run': run, 'version': version, 'action': action,
                        'reason': ('更高版本恢复已撤回片段' if was_withdrawn else
                                   '更高版本替换旧值' if action == 'replaced' else '新片段')})

    if conflicts or problems:
        counts['conflict'] = len(conflicts)
        counts['rejected'] = len(problems)
        if conflicts:
            raise MergeRejected(409, _conflict_message(conflicts, len(entries)),
                                details=conflicts + problems, counts=counts)
        raise MergeRejected(422, _partial_message(problems, len(entries)),
                            details=problems, counts=counts)

    now = time.time()
    intake = DatasetIntake(dataset_id=dataset.id, source=source, actor=actor, status='accepted',
                           checksum='', rows=len(entries), counts=counts,
                           details=details[:MAX_DETAILS], error='', created_at=now)
    db.add(intake)
    db.flush()
    for run in order:
        for version, revision in state[run].items():
            if revision['new']:
                db.add(DatasetRevision(
                    dataset_id=dataset.id, intake_id=None if revision['bootstrap'] else intake.id,
                    run=run, version=version, content=revision['content'],
                    withdrawn=revision['withdrawn'], fingerprint=revision['fingerprint'], created_at=now))
            elif revision['changed']:
                revision['obj'].withdrawn = revision['withdrawn']

    records_after = _records_from_state(state, order)
    dataset.records = records_after
    dataset.checksum = checksum(records_after)
    intake.checksum = dataset.checksum
    return intake


def intake_dict(intake):
    counts = intake.counts or {}
    details = intake.details or []
    return {'id': intake.id, 'dataset_id': intake.dataset_id, 'source': intake.source,
            'actor': intake.actor, 'status': intake.status, 'rows': intake.rows,
            'counts': counts, 'checksum': intake.checksum, 'error': intake.error,
            'created_at': intake.created_at, 'details': details,
            'details_total': counts.get('total', len(details))}


def record_rejected(db, dataset_id, source, actor, rows, error, counts, details):
    """整批拒收也留痕：数据未写入，但接入历史保留拒收原因与冲突明细。"""
    intake = DatasetIntake(dataset_id=dataset_id, source=source, actor=actor, status='rejected',
                           checksum='', rows=rows, counts=counts or {},
                           details=(details or [])[:MAX_DETAILS], error=str(error)[:990],
                           created_at=time.time())
    db.add(intake)
    db.flush()
    return intake
