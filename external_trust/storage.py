"""独立的网关账本和不可变内容库。没有科研数据库依赖。"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile

from .contracts import BoundaryError, DOC_BYTES, canonical, digest


SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings (slot INTEGER PRIMARY KEY CHECK(slot=1), enabled INTEGER NOT NULL,
    generation INTEGER NOT NULL);
INSERT OR IGNORE INTO settings VALUES(1,0,0);
CREATE TABLE IF NOT EXISTS peers (id TEXT PRIMARY KEY, protocol TEXT NOT NULL, port INTEGER NOT NULL,
    scopes TEXT NOT NULL, expires INTEGER NOT NULL, state TEXT NOT NULL, generation INTEGER NOT NULL,
    inbound_key TEXT NOT NULL, outbound_key TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, peer TEXT NOT NULL REFERENCES peers(id),
    direction TEXT NOT NULL, sha TEXT NOT NULL, bytes INTEGER NOT NULL, kind TEXT NOT NULL, topic TEXT,
    state TEXT NOT NULL, review TEXT NOT NULL, message_id TEXT NOT NULL, envelope_sha TEXT,
    peer_generation INTEGER NOT NULL, global_generation INTEGER NOT NULL, created INTEGER NOT NULL,
    UNIQUE(peer,direction,message_id));
CREATE TABLE IF NOT EXISTS tasks (peer TEXT NOT NULL REFERENCES peers(id), id TEXT NOT NULL,
    offer_message TEXT NOT NULL, offer_direction TEXT NOT NULL, state TEXT NOT NULL,
    peer_generation INTEGER NOT NULL, global_generation INTEGER NOT NULL, PRIMARY KEY(peer,id));
CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, document TEXT NOT NULL REFERENCES documents(id),
    stage TEXT NOT NULL, binding TEXT NOT NULL, binding_sha TEXT NOT NULL, expires INTEGER NOT NULL,
    approved INTEGER NOT NULL, used INTEGER, revoked INTEGER, approver TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidates (document TEXT PRIMARY KEY REFERENCES documents(id),
    sha TEXT NOT NULL, approval TEXT NOT NULL REFERENCES approvals(id), created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS versions (topic TEXT NOT NULL, version INTEGER NOT NULL,
    document TEXT NOT NULL UNIQUE REFERENCES candidates(document), sha TEXT NOT NULL,
    approval TEXT NOT NULL REFERENCES approvals(id), created INTEGER NOT NULL, PRIMARY KEY(topic,version));
CREATE TABLE IF NOT EXISTS sends (document TEXT PRIMARY KEY REFERENCES documents(id),
    approval TEXT NOT NULL REFERENCES approvals(id), state TEXT NOT NULL, claimed INTEGER NOT NULL, ack_sha TEXT);
CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY, event TEXT NOT NULL, created INTEGER NOT NULL,
    details TEXT NOT NULL, previous TEXT NOT NULL, sha TEXT NOT NULL);
'''


def no_links(path):
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise BoundaryError('linked_boundary_path')


def read_regular(path, limit):
    no_links(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= limit:
        raise BoundaryError('invalid_document_file')
    flags = os.O_RDONLY | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_BINARY',0) | getattr(os,'O_NONBLOCK',0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        actual = os.fstat(stream.fileno())
        if (not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1
                or (actual.st_dev, actual.st_ino, actual.st_size) != (info.st_dev, info.st_ino, info.st_size)):
            raise BoundaryError('document_file_changed')
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    no_links(path)
    current = path.stat()
    if (len(data) != actual.st_size or len(data) > limit or after.st_mtime_ns != actual.st_mtime_ns
            or (current.st_dev,current.st_ino,current.st_nlink) != (actual.st_dev,actual.st_ino,1)):
        raise BoundaryError('document_file_changed')
    return data


class Store:
    def __init__(self, root, *, initialize=False):
        original = Path(root).absolute()
        no_links(original)
        self.root = original.resolve()
        marker = self.root/'external-boundary-v1'
        self.db_path = self.root/'gateway.sqlite'
        if initialize:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if any(self.root.iterdir()):
                raise BoundaryError('external_boundary_root_not_empty')
            for name in ('objects', 'communication', 'security'):
                (self.root/name).mkdir(mode=0o700)
            with self.db_path.open('xb'):
                pass
            with self.transaction() as db:
                db.executescript(SCHEMA)
            with marker.open('xb') as stream:
                stream.write(b'SKY External Trust Boundary v1\n')
        if read_regular(marker, 64) != b'SKY External Trust Boundary v1\n':
            raise BoundaryError('external_boundary_marker_invalid')

    @contextmanager
    def transaction(self, *, write=True):
        for suffix in ('','-wal','-shm','-journal'):
            path = self.db_path.with_name(self.db_path.name+suffix)
            no_links(path)
            if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
                raise BoundaryError('invalid_gateway_ledger')
        db = sqlite3.connect(self.db_path.as_uri() + ('?mode=rw' if write else '?mode=ro'),
                             uri=True, isolation_level=None, timeout=8)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def publish(self, raw):
        if not 0 < len(raw) <= DOC_BYTES:
            raise BoundaryError('document_size_limit')
        sha = digest(raw)
        root = self.root/'objects'
        no_links(root)
        target = root/(sha+'.json')
        if target.exists():
            if self.load(sha, len(raw)) != raw:
                raise BoundaryError('document_integrity_mismatch')
            return sha
        with tempfile.NamedTemporaryFile(dir=root, prefix='.pending-', delete=False) as stream:
            temp = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            if digest(read_regular(temp, DOC_BYTES)) != sha:
                raise BoundaryError('document_integrity_mismatch')
            try:
                if os.name == 'nt':
                    temp.rename(target)
                else:
                    os.link(temp, target)
            except FileExistsError:
                if self.load(sha, len(raw)) != raw:
                    raise BoundaryError('document_integrity_mismatch')
        finally:
            temp.unlink(missing_ok=True)
        return sha

    def load(self, sha, size):
        if not re.fullmatch(r'[0-9a-f]{64}', sha) or not 0 < size <= DOC_BYTES:
            raise BoundaryError('invalid_document_descriptor')
        raw = read_regular(self.root/'objects'/(sha+'.json'), size)
        if len(raw) != size or digest(raw) != sha:
            raise BoundaryError('document_integrity_mismatch')
        return raw

    @staticmethod
    def audit(db, event, now, **details):
        last = db.execute('SELECT seq,sha FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
        seq, previous = (last['seq']+1, last['sha']) if last else (1, '0'*64)
        item = dict(seq=seq, event=event, created=now, previous=previous, details=details)
        db.execute('INSERT INTO audit VALUES(?,?,?,?,?,?)', (seq,event,now,canonical(details).decode(),previous,digest(canonical(item))))

    def audit_log(self):
        with self.transaction(write=False) as db:
            rows = db.execute('SELECT * FROM audit ORDER BY seq').fetchall()
        previous = '0'*64
        output = []
        for seq, row in enumerate(rows, 1):
            item = {**dict(row), 'details':json.loads(row['details'])}
            claimed = item.pop('sha')
            if row['seq'] != seq or row['previous'] != previous or digest(canonical(item)) != claimed:
                raise BoundaryError('audit_chain_invalid')
            output.append({**item,'sha':claimed})
            previous = claimed
        return output
