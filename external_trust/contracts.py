"""外部文件只承载纯文本与明确的文档交换字段。"""
from hashlib import sha256
import hmac
import json
import re
from typing import Annotated, Literal
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, model_validator


DOC_BYTES = 36_000
WIRE_BYTES = 48_000
PEER_TTL = 7 * 24 * 3600
MESSAGE_TTL = 300
APPROVAL_TTL = 900
LOCAL = 'sky-external'
ID = Annotated[str, Field(pattern=r'^[a-z][a-z0-9-]{0,47}$')]
MESSAGE_ID = Annotated[str, Field(pattern=r'^[a-f0-9]{32}$')]
SHA = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
Protocol = Literal['a2a', 'dsh']


class BoundaryError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)


class Document(Strict):
    version: Annotated[int, Field(ge=1, le=1)] = 1
    media_type: Literal['text/plain'] = 'text/plain'
    classification: Literal['public'] = 'public'
    kind: Literal['note', 'offer', 'accept', 'reject', 'knowledge']
    title: Annotated[str, Field(min_length=1, max_length=160)]
    body: Annotated[str, Field(min_length=1, max_length=32_000)]
    topic: ID | None = None
    task_id: MESSAGE_ID | None = None
    in_reply_to: MESSAGE_ID | None = None

    @model_validator(mode='after')
    def validate_fields(self):
        task = self.kind in ('offer', 'accept', 'reject')
        if (self.task_id is not None) != task or (self.in_reply_to is not None) != (self.kind in ('accept', 'reject')):
            raise ValueError('document_task_scope_invalid')
        if (self.topic is not None) != (self.kind == 'knowledge'):
            raise ValueError('document_topic_invalid')
        if len(canonical(self)) > DOC_BYTES:
            raise ValueError('document_size_limit')
        for value in (self.title, self.body):
            if (not value.strip() or unicodedata.normalize('NFC', value) != value
                    or any(c not in '\r\n\t' and unicodedata.category(c) in {'Cc','Cf','Cs'} for c in value)):
                raise ValueError('document_unicode_invalid')
        if any(c in self.title for c in '\r\n\t'):
            raise ValueError('document_title_invalid')
        return self


class Envelope(Strict):
    version: Annotated[int, Field(ge=1, le=1)] = 1
    protocol: Protocol
    sender: ID
    recipient: ID
    message_id: MESSAGE_ID
    issued: Annotated[int, Field(ge=0)]
    expires: Annotated[int, Field(ge=0)]
    hop: Annotated[int, Field(ge=0, le=0)] = 0
    byte_count: Annotated[int, Field(gt=0, le=DOC_BYTES)]
    sha256: SHA
    document: Document


def canonical(value) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _unique(pairs):
    data = dict(pairs)
    if len(data) != len(pairs):
        raise BoundaryError('duplicate_json_keys')
    return data


def decode(raw: bytes, limit=WIRE_BYTES) -> dict:
    if not 0 < len(raw) <= limit:
        raise BoundaryError('wire_size_limit')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique)
    except (ValueError, RecursionError) as error:
        raise BoundaryError('invalid_wire_json') from error
    if not isinstance(value, dict):
        raise BoundaryError('wire_object_required')
    return value


def exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise BoundaryError('unexpected_wire_fields')
    return value


def mac(key: str, domain: str, value) -> str:
    return hmac.digest(bytes.fromhex(key), domain.encode('ascii') + b'\0' + canonical(value), 'sha256').hex()


def verify_mac(key, domain, value, signature):
    if not isinstance(signature, str) or not re.fullmatch(r'[a-f0-9]{64}', signature) or not hmac.compare_digest(mac(key, domain, value), signature):
        raise BoundaryError('peer_signature_invalid')


def make_envelope(document, protocol, sender, recipient, message_id, now):
    raw = canonical(document)
    return Envelope(protocol=protocol, sender=sender, recipient=recipient, message_id=message_id,
                    issued=now, expires=now + MESSAGE_TTL, byte_count=len(raw), sha256=digest(raw), document=document)


def encode_message(envelope, key):
    data = {'envelope':envelope.model_dump(), 'signature':mac(key, 'document-v1', envelope)}
    if envelope.protocol == 'a2a':
        value = {'jsonrpc':'2.0','method':'SendMessage','id':envelope.message_id,
                 'params':{'message':{'messageId':envelope.message_id,'role':'ROLE_USER','parts':[{'data':data}]}}}
    else:
        value = {'profile':'dsh-document-local-v1','from_instance':envelope.sender,'to_instance':envelope.recipient,
                 'message_id':envelope.message_id,'document':data}
    raw = canonical(value)
    if len(raw) > WIRE_BYTES:
        raise BoundaryError('wire_size_limit')
    return raw


def decode_message(raw, protocol, key, sender, recipient, now):
    value = decode(raw)
    if protocol == 'a2a':
        exact(value, ('jsonrpc','method','id','params'))
        message = exact(exact(value['params'], ('message',))['message'], ('messageId','role','parts'))
        parts = message['parts']
        if (value['jsonrpc'] != '2.0' or value['method'] != 'SendMessage' or message['role'] != 'ROLE_USER'
                or value['id'] != message['messageId'] or not isinstance(parts, list) or len(parts) != 1):
            raise BoundaryError('a2a_profile_rejected')
        data = exact(parts[0], ('data',))['data']
        message_id = message['messageId']
    elif protocol == 'dsh':
        exact(value, ('profile','from_instance','to_instance','message_id','document'))
        if value['profile'] != 'dsh-document-local-v1' or (value['from_instance'], value['to_instance']) != (sender, recipient):
            raise BoundaryError('dsh_profile_rejected')
        data, message_id = value['document'], value['message_id']
    else:
        raise BoundaryError('protocol_unavailable')
    data = exact(data, ('envelope','signature'))
    try:
        envelope = Envelope.model_validate(data['envelope'])
    except ValueError as error:
        raise BoundaryError('envelope_invalid') from error
    verify_mac(key, 'document-v1', envelope, data['signature'])
    if (envelope.protocol, envelope.sender, envelope.recipient, envelope.message_id) != (protocol, sender, recipient, message_id):
        raise BoundaryError('peer_identity_mismatch')
    if not envelope.issued <= now + 5 or not now < envelope.expires <= envelope.issued + MESSAGE_TTL:
        raise BoundaryError('message_expired')
    raw_doc = canonical(envelope.document)
    if len(raw_doc) != envelope.byte_count or digest(raw_doc) != envelope.sha256:
        raise BoundaryError('document_digest_mismatch')
    return envelope
