"""只从当前尝试已冻结的产物清单采集四份收敛原始数据。"""
import csv
import hashlib
import io
from pathlib import Path

from app.execution.files import bounded_read


def read_histories(execution, attempt_root):
    manifest=execution['evidence_contract']['artifacts']
    records=execution['validation']['meshes']
    if len(records)!=4:
        raise ValueError('four_cooling_histories_required')
    profiles=[]
    for i,record in enumerate(records,1):
        entry=manifest['convergence_'+str(i)]
        path=Path(entry['path'])
        path.resolve().relative_to(Path(attempt_root).resolve())
        data=bounded_read(path.parent,path.name,1_500_000)
        if hashlib.sha256(data).hexdigest()!=entry['sha256'] or entry['sha256']!=record['history_sha256']:
            raise ValueError('cooling_history_integrity_lost')
        rows=list(csv.DictReader(io.StringIO(data.decode('utf-8'))))
        if not 2<=len(rows)<=5000:
            raise ValueError('cooling_history_sample_limit')
        profiles.append({'sha256':entry['sha256'],'rows':[{k:float(v) for k,v in row.items()} for row in rows]})
    return {'profiles':profiles,'records':records}
