"""确定性外部信任网关。模型、Worker 和外部消息均不拥有审批权限。"""
from functools import wraps
import json
import re
from secrets import token_hex
import time
from uuid import uuid4

from .contracts import (APPROVAL_TTL, DOC_BYTES, LOCAL, PEER_TTL, BoundaryError, Document,
                        canonical, decode, decode_message, digest, encode_message, make_envelope)
from .lanes import Lanes
from .storage import Store
from .transport import LoopbackTransport, port_number


SCOPES = frozenset({'send','receive','import','update'})
STAGES = {
    'send': ('out','REVIEW_REQUIRED','external-sync'),
    'import': ('in','REVIEW_REQUIRED','knowledge-candidate'),
    'update': ('in','CANDIDATE','versioned-release'),
}
IDENTITY = re.compile(r'[a-z][a-z0-9-]{0,47}')


def audited(action):
    @wraps(action)
    def run(self, *args, **kwargs):
        try:
            return action(self, *args, **kwargs)
        except BoundaryError as error:
            with self.store.transaction() as db:
                self.store.audit(db, 'denied', self.now(), operation=action.__name__, code=error.code)
            raise
    return run


class Gateway:
    def __init__(self, root, *, initialize=False, clock=time.time, transport=None):
        self.store = Store(root, initialize=initialize)
        self.lanes = Lanes(self.store.root)
        self.transport = transport or LoopbackTransport()
        self.clock = clock

    def now(self):
        return int(self.clock())

    @audited
    def switch(self, enabled):
        if type(enabled) is not bool:
            raise BoundaryError('invalid_gateway_switch')
        with self.store.transaction() as db:
            db.execute('UPDATE settings SET enabled=?,generation=generation+1 WHERE slot=1 AND enabled<>?', (enabled,enabled))
            self.store.audit(db, 'gateway_switched', self.now(), enabled=enabled)

    @audited
    def authorize(self, peer, protocol, port, scopes, expires):
        if (not isinstance(peer,str) or not IDENTITY.fullmatch(peer) or peer == LOCAL or protocol not in ('a2a','dsh')
                or not scopes or not set(scopes) <= SCOPES or type(expires) is not int
                or not self.now() < expires <= self.now()+PEER_TTL):
            raise BoundaryError('peer_authorization_invalid')
        port_number(port)
        inbound, outbound = token_hex(32), token_hex(32)
        with self.store.transaction() as db:
            if (db.execute('SELECT count(*) FROM peers').fetchone()[0] >= 64
                    and not db.execute('SELECT 1 FROM peers WHERE id=?',(peer,)).fetchone()):
                raise BoundaryError('peer_enrollment_quota')
            db.execute('''INSERT INTO peers VALUES(?,?,?,?,?,'ACTIVE',1,?,?)
                ON CONFLICT(id) DO UPDATE SET protocol=excluded.protocol,port=excluded.port,scopes=excluded.scopes,
                expires=excluded.expires,state='ACTIVE',generation=peers.generation+1,
                inbound_key=excluded.inbound_key,outbound_key=excluded.outbound_key''',
                (peer,protocol,port,canonical(sorted(set(scopes))).decode(),expires,inbound,outbound))
            self.store.audit(db, 'peer_authorized', self.now(), peer=peer, protocol=protocol,
                             scopes=sorted(set(scopes)), expires=expires)
        # 密钥仅交本地操作员 / Give secrets only to the local operator.
        return dict(id=peer,protocol=protocol,inbound_key=inbound,outbound_key=outbound)

    @audited
    def revoke(self, peer):
        with self.store.transaction() as db:
            if not db.execute("UPDATE peers SET state='REVOKED',generation=generation+1 WHERE id=?", (peer,)).rowcount:
                raise BoundaryError('unknown_peer')
            self.store.audit(db, 'peer_revoked', self.now(), peer=peer)

    def _active(self, db, peer_id, scope):
        settings = db.execute('SELECT * FROM settings WHERE slot=1').fetchone()
        peer = db.execute('SELECT * FROM peers WHERE id=?', (peer_id,)).fetchone()
        if not settings['enabled']:
            raise BoundaryError('external_gateway_disabled')
        if not peer or peer['state'] != 'ACTIVE' or peer['expires'] <= self.now() or scope not in json.loads(peer['scopes']):
            raise BoundaryError('peer_scope_unavailable')
        return peer, settings['generation']

    @staticmethod
    def _row(db, document_id):
        row = db.execute('SELECT * FROM documents WHERE id=?', (document_id,)).fetchone()
        if not row:
            raise BoundaryError('unknown_document')
        return row

    def _document(self, row):
        try:
            raw = self.store.load(row['sha'], row['bytes'])
            return Document.model_validate(decode(raw, DOC_BYTES))
        except (OSError, ValueError) as error:
            raise BoundaryError('stored_document_integrity') from error

    def _prepare(self, data):
        try:
            document = self.lanes.normalize(Document.model_validate(data))
        except (ValueError, UnicodeError) as error:
            if isinstance(error, BoundaryError):
                raise
            raise BoundaryError('document_contract_invalid') from error
        return document, self.lanes.review(document)

    def _record(self, db, document, review, peer, generation, direction, message_id, envelope_sha=None):
        if db.execute('SELECT count(*) FROM documents WHERE peer=?', (peer['id'],)).fetchone()[0] >= 2000:
            raise BoundaryError('peer_document_quota')
        raw = canonical(document)
        doc_id, sha = uuid4().hex, self.store.publish(raw)
        state = 'QUARANTINED' if review['flags'] else 'REVIEW_REQUIRED'
        db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (doc_id,peer['id'],direction,sha,len(raw),document.kind,document.topic,state,
             canonical(review).decode(),message_id,envelope_sha,peer['generation'],generation,self.now()))
        self.store.audit(db, 'document_recorded', self.now(), document=doc_id, peer=peer['id'], sha=sha,
                         direction=direction, state=state, flags=review['flags'], message_id=message_id)
        return dict(id=doc_id, sha256=sha, state=state, message_id=message_id, review=review)

    def _task(self, db, peer, document, message_id, direction, *, apply):
        if not document.task_id:
            return
        row = db.execute('SELECT * FROM tasks WHERE peer=? AND id=?', (peer,document.task_id)).fetchone()
        peer_generation = db.execute('SELECT generation FROM peers WHERE id=?',(peer,)).fetchone()[0]
        global_generation = db.execute('SELECT generation FROM settings WHERE slot=1').fetchone()[0]
        if document.kind == 'offer':
            if row:
                raise BoundaryError('task_already_offered')
            expected = 'OFFERED'
        else:
            if (not row or row['state'] != 'OFFERED' or row['offer_direction'] == direction
                    or row['offer_message'] != document.in_reply_to
                    or (row['peer_generation'],row['global_generation']) != (peer_generation,global_generation)):
                raise BoundaryError('task_reply_scope_invalid')
            expected = document.kind.upper()+'ED'
        if apply:
            transition = self.lanes.task_transition(document.kind, row['state'] if row else None,
                bool(row and row['offer_direction'] != direction), bool(row and row['offer_message'] == document.in_reply_to))
            if transition != expected:
                raise BoundaryError('communication_task_mismatch')
            if row:
                db.execute('UPDATE tasks SET state=? WHERE peer=? AND id=?', (transition,peer,document.task_id))
            else:
                db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?)',
                    (peer,document.task_id,message_id,direction,transition,peer_generation,global_generation))
            self.store.audit(db, 'task_state_changed', self.now(), peer=peer, task=document.task_id,
                             message_id=message_id, transition=document.kind)

    @audited
    def draft(self, peer_id, data):
        # 仅外发新建本地文档 / Outbound exchange accepts newly authored local documents.
        if not isinstance(data, dict) or data.get('classification') != 'public':
            raise BoundaryError('outbound_public_declaration_required')
        with self.store.transaction() as db:
            peer, generation = self._active(db,peer_id,'send')
            document, review = self._prepare(data)
            message_id = uuid4().hex
            self._task(db,peer_id,document,message_id,'out',apply=False)
            return self._record(db,document,review,peer,generation,'out',message_id)

    def _receive(self, db, peer, generation, wire):
        envelope = decode_message(wire,peer['protocol'],peer['inbound_key'],peer['id'],LOCAL,self.now())
        envelope_sha = digest(canonical(envelope))
        known = db.execute("SELECT * FROM documents WHERE peer=? AND direction='in' AND message_id=?",
                           (peer['id'],envelope.message_id)).fetchone()
        if known:
            if known['envelope_sha'] != envelope_sha:
                raise BoundaryError('message_id_conflict')
            self._document(known)
            self.store.audit(db,'duplicate_ignored',self.now(),peer=peer['id'],document=known['id'],message_id=envelope.message_id)
            return dict(id=known['id'],sha256=known['sha'],state=known['state'],duplicate=True)
        document, review = self._prepare(envelope.document.model_dump())
        if digest(canonical(document)) != envelope.sha256:
            raise BoundaryError('document_not_normalized')
        if not review['flags']:
            self._task(db,peer['id'],document,envelope.message_id,'in',apply=True)
        return self._record(db,document,review,peer,generation,'in',envelope.message_id,envelope_sha)

    @audited
    def receive(self, peer_id, wire):
        with self.store.transaction() as db:
            peer, generation = self._active(db,peer_id,'receive')
            return self._receive(db,peer,generation,wire)

    def _suspend(self, db, peer_id, reason):
        db.execute("UPDATE peers SET state='OFFLINE',generation=generation+1 WHERE id=? AND state='ACTIVE'", (peer_id,))
        self.store.audit(db,'peer_offline',self.now(),peer=peer_id,code=reason)

    @audited
    def pull(self, peer_id):
        failure = None
        with self.store.transaction() as db:
            peer, generation = self._active(db,peer_id,'receive')
            previous = db.execute("SELECT message_id FROM documents WHERE peer=? AND direction='in' AND peer_generation=? "
                                  "ORDER BY rowid DESC LIMIT 1", (peer_id,peer['generation'])).fetchone()
            try:
                wire = self.transport.pull(peer, previous['message_id'] if previous else None)
            except BoundaryError as error:
                self._suspend(db,peer_id,error.code)
                failure = error
            else:
                return self._receive(db,peer,generation,wire) if wire else None
        raise failure

    def _binding(self, db, stage, document_id, expires):
        if stage not in STAGES:
            raise BoundaryError('unknown_approval_stage')
        direction,state,purpose = STAGES[stage]
        row = self._row(db,document_id)
        peer,generation = self._active(db,row['peer'],stage)
        if row['state'] != state or row['direction'] != direction or (stage != 'send' and row['kind'] != 'knowledge'):
            raise BoundaryError('document_not_eligible_for_stage')
        if (row['peer_generation'],row['global_generation']) != (peer['generation'],generation):
            raise BoundaryError('document_authorization_stale')
        if type(expires) is not int or not self.now() < expires <= min(self.now()+APPROVAL_TTL,peer['expires']):
            raise BoundaryError('approval_expired_or_invalid')
        doc = self._document(row)
        base = None
        if stage == 'update':
            base = db.execute('SELECT COALESCE(MAX(version),0) FROM versions WHERE topic=?',(row['topic'],)).fetchone()[0]
        receiver = peer['id'] if stage=='send' else 'external-knowledge-candidates' if stage=='import' else 'external-knowledge'
        binding = dict(document_id=document_id,document_sha256=row['sha'],bytes=row['bytes'],recipient=receiver,
            source_peer=peer['id'],protocol=peer['protocol'],purpose=purpose,scope='document:'+stage,
            expires=expires,peer_generation=peer['generation'],gateway_generation=generation,base_version=base,
            message_id=row['message_id'])
        return binding, row, peer, doc

    @audited
    def preview(self, stage, document_id, expires):
        with self.store.transaction(write=False) as db:
            binding,_,_,doc = self._binding(db,stage,document_id,expires)
            return {'binding':binding,'confirmation_sha256':digest(canonical(binding)), 'document':doc.model_dump()}

    @audited
    def approve(self, stage, document_id, expires, confirmation_sha, approver):
        if not isinstance(approver,str) or not IDENTITY.fullmatch(approver):
            raise BoundaryError('local_operator_identity_invalid')
        with self.store.transaction() as db:
            binding,_,_,_ = self._binding(db,stage,document_id,expires)
            sha = digest(canonical(binding))
            if sha != confirmation_sha:
                raise BoundaryError('approval_confirmation_mismatch')
            approval_id = uuid4().hex
            db.execute('INSERT INTO approvals VALUES(?,?,?,?,?,?,?,NULL,NULL,?)',
                       (approval_id,document_id,stage,canonical(binding).decode(),sha,expires,self.now(),approver))
            self.store.audit(db,'human_approved',self.now(),approval=approval_id,stage=stage,approver=approver,**binding)
            return dict(approval_id=approval_id,binding=binding,confirmation_sha256=sha)

    @audited
    def revoke_approval(self, approval_id):
        with self.store.transaction() as db:
            if not db.execute('UPDATE approvals SET revoked=? WHERE id=? AND used IS NULL AND revoked IS NULL',
                              (self.now(),approval_id)).rowcount:
                raise BoundaryError('approval_unavailable')
            self.store.audit(db,'approval_revoked',self.now(),approval=approval_id)

    def _use(self, db, stage, approval_id):
        approval = db.execute('SELECT * FROM approvals WHERE id=?', (approval_id,)).fetchone()
        if not approval or approval['used'] is not None or approval['revoked'] is not None or approval['stage'] != stage:
            raise BoundaryError('approval_unavailable')
        binding,row,peer,doc = self._binding(db,stage,approval['document'],approval['expires'])
        raw = canonical(binding)
        if digest(raw) != approval['binding_sha'] or raw.decode() != approval['binding']:
            raise BoundaryError('approval_binding_stale')
        db.execute('UPDATE approvals SET used=? WHERE id=?', (self.now(),approval_id))
        self.store.audit(db,'approval_consumed',self.now(),approval=approval_id,stage=stage,document=row['id'])
        return binding,row,peer,doc

    @audited
    def import_candidate(self, approval_id):
        with self.store.transaction() as db:
            _,row,_,_ = self._use(db,'import',approval_id)
            db.execute('INSERT INTO candidates VALUES(?,?,?,?)',(row['id'],row['sha'],approval_id,self.now()))
            db.execute("UPDATE documents SET state='CANDIDATE' WHERE id=?",(row['id'],))
            self.store.audit(db,'candidate_imported',self.now(),document=row['id'],sha=row['sha'])
            return row['id']

    @audited
    def publish_version(self, approval_id):
        with self.store.transaction() as db:
            binding,row,_,_ = self._use(db,'update',approval_id)
            version = binding['base_version'] + 1
            db.execute('INSERT INTO versions VALUES(?,?,?,?,?,?)',(row['topic'],version,row['id'],row['sha'],approval_id,self.now()))
            db.execute("UPDATE documents SET state='PUBLISHED' WHERE id=?",(row['id'],))
            self.store.audit(db,'knowledge_published',self.now(),document=row['id'],sha=row['sha'],topic=row['topic'],version=version)
            return dict(topic=row['topic'], version=version, sha256=row['sha'])

    @audited
    def send(self, approval_id):
        with self.store.transaction() as db:
            binding,row,peer,doc = self._use(db,'send',approval_id)
            doc_id, peer_id = row['id'],peer['id']
            self._task(db,peer_id,doc,row['message_id'],'out',apply=False)
            db.execute("INSERT INTO sends VALUES(?,?,'SENDING',?,NULL)", (doc_id,approval_id,self.now()))
            db.execute("UPDATE documents SET state='SENDING' WHERE id=?", (doc_id,))
            self.store.audit(db,'send_claimed',self.now(),document=doc_id,peer=peer_id,sha=row['sha'])
        try:
            with self.store.transaction() as db:
                peer,generation = self._active(db,peer_id,'send')
                if (peer['generation'],generation) != (binding['peer_generation'],binding['gateway_generation']) or self.now() >= binding['expires']:
                    raise BoundaryError('dispatch_authorization_stale')
                if db.execute('SELECT state FROM sends WHERE document=?',(doc_id,)).fetchone()['state'] != 'SENDING':
                    raise BoundaryError('dispatch_claim_stale')
                doc = self._document(self._row(db,doc_id))
                self._task(db,peer_id,doc,row['message_id'],'out',apply=False)
                envelope = make_envelope(doc,peer['protocol'],LOCAL,peer_id,row['message_id'],self.now())
                ack = self.transport.send(peer,encode_message(envelope,peer['outbound_key']),envelope)
                ack_sha = digest(canonical(ack))
                self._task(db,peer_id,doc,row['message_id'],'out',apply=True)
                db.execute("UPDATE sends SET state='ACKED',ack_sha=? WHERE document=?",(ack_sha,doc_id))
                db.execute("UPDATE documents SET state='DELIVERED' WHERE id=?",(doc_id,))
                self.store.audit(db,'send_acknowledged',self.now(),document=doc_id,peer=peer_id,ack_sha=ack_sha)
                return dict(document=doc_id,state='DELIVERED',ack_sha256=ack_sha)
        except (BoundaryError,OSError):
            with self.store.transaction() as db:
                self._unknown(db,doc_id,peer_id)
            raise

    def _unknown(self, db, document, peer):
        if db.execute("UPDATE sends SET state='UNKNOWN' WHERE document=? AND state='SENDING'",(document,)).rowcount:
            db.execute("UPDATE documents SET state='DELIVERY_UNKNOWN' WHERE id=?",(document,))
            self._suspend(db,peer,'delivery_unknown')
            self.store.audit(db,'send_unknown',self.now(),document=document,peer=peer,automatic_retry=False)

    def recover(self):
        # 只收尾过期领取 / Expire old claims without replaying uncertain sends.
        with self.store.transaction() as db:
            rows = db.execute("SELECT sends.document,documents.peer FROM sends JOIN documents ON documents.id=sends.document "
                "WHERE sends.state='SENDING' AND sends.claimed<=?",(self.now()-30,)).fetchall()
            for row in rows:
                self._unknown(db,row['document'],row['peer'])
            return len(rows)

    def inspect(self, document_id):
        with self.store.transaction(write=False) as db:
            row = dict(self._row(db,document_id))
            row['review'] = json.loads(row['review'])
            row['document'] = self._document(row).model_dump()
            return row

    def knowledge(self):
        with self.store.transaction(write=False) as db:
            return {'candidates':[dict(row) for row in db.execute('SELECT * FROM candidates ORDER BY rowid')],
                    'versions':[dict(row) for row in db.execute('SELECT * FROM versions ORDER BY topic,version')]}

    def offline_training_manifest(self):
        with self.store.transaction(write=False) as db:
            versions = [dict(row) for row in db.execute('SELECT * FROM versions ORDER BY topic,version')]
            for version in versions:
                source = self._row(db, version['document'])
                if source['state'] != 'PUBLISHED' or version['sha'] != source['sha']:
                    raise BoundaryError('knowledge_version_integrity')
                self._document(source)
        return {'mode':'future-offline-interface', 'training_executed':False, 'approved_sources':versions}

    def status(self):
        with self.store.transaction(write=False) as db:
            return {'mode':'local_mock_only','enabled':bool(db.execute('SELECT enabled FROM settings').fetchone()[0]),
                    'peers':[dict(row) for row in db.execute('SELECT id,protocol,port,state,expires,generation FROM peers ORDER BY id')],
                    'documents':[dict(row) for row in db.execute('SELECT id,peer,kind,sha,state FROM documents ORDER BY rowid')],
                    'tasks':[dict(row) for row in db.execute('SELECT * FROM tasks ORDER BY rowid')]}
