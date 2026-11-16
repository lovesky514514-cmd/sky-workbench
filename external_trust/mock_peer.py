"""独立本地模拟 Agent。仅用于合成文档交换和攻击回归。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from threading import RLock
import time
from uuid import uuid4

from .contracts import (BoundaryError, Document, LOCAL, WIRE_BYTES, canonical, decode_message,
                        digest, encode_message, mac, make_envelope, verify_mac)
from .transport import PREFIX


class MockPeer(ThreadingHTTPServer):
    def __init__(self, port=0, *, clock=time.time):
        super().__init__(('127.0.0.1',port), _Handler)
        self.credentials = None
        self.clock = clock
        self.lock = RLock()
        self.inbox = {}
        self.outbox = []
        self.fault = None

    def enqueue(self, document):
        with self.lock:
            peer = self.credentials
            env = make_envelope(Document.model_validate(document),peer['protocol'],peer['id'],LOCAL,uuid4().hex,int(self.clock()))
            self.outbox.append((env.message_id, encode_message(env,peer['inbound_key'])))
            return env.message_id


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(4)
        self.challenge = None

    def log_message(self, *args):
        pass

    def _reply(self, status, raw=b''):
        self.send_response(status)
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Content-Type','application/json')
        self.end_headers()
        if raw:
            self.wfile.write(raw)

    def _auth(self):
        proof = self.headers.get_all('X-SKY-Proof') or []
        nonce = self.headers.get_all('X-SKY-Challenge') or []
        cursor = self.headers.get_all('X-SKY-After') or []
        challenge,self.challenge = self.challenge,None
        if (challenge is None or len(proof)!=1 or nonce!=[challenge['nonce']] or len(cursor)>1
                or (cursor and (self.command!='GET' or not re.fullmatch(r'[a-f0-9]{32}',cursor[0])))):
            raise BoundaryError('mock_authentication_required')
        after = cursor[0] if cursor else None
        verify_mac(self.server.credentials['outbound_key'],'transport-client-v1',
                   {'challenge':challenge,'method':self.command,'after':after},proof[0])
        return after

    def do_GET(self):
        peer = self.server.credentials
        if peer is None:
            self._reply(503)
            return
        match = re.fullmatch(re.escape(PREFIX)+r'/challenge/([a-f0-9]{32})',self.path)
        if match:
            self.challenge = {'nonce':match.group(1),'peer':peer['id'],'audience':LOCAL,'protocol':peer['protocol']}
            signed = mac(peer['inbound_key'],'transport-challenge-v1',self.challenge)
            self._reply(200,canonical({'challenge':self.challenge,'signature':signed}))
            return
        if self.path != PREFIX+'/documents':
            self._reply(404)
            return
        try:
            after = self._auth()
            with self.server.lock:
                queue = self.server.outbox
                index = next((i+1 for i,row in enumerate(queue) if row[0]==after),0)
                if after is not None and index == 0:
                    raise BoundaryError('unknown_delivery_cursor')
                payload = queue[index][1] if index < len(queue) else b''
            self._reply(200 if payload else 204,payload)
        except BoundaryError:
            self._reply(403)

    def do_POST(self):
        if self.path != PREFIX+'/documents':
            self._reply(404)
            return
        try:
            self._auth()
            sizes = self.headers.get_all('Content-Length') or []
            if (len(sizes)!=1 or self.headers.get('Transfer-Encoding') is not None or not re.fullmatch(r'[0-9]{1,6}',sizes[0])
                    or not 0 < int(sizes[0]) <= WIRE_BYTES):
                raise BoundaryError('mock_request_limit')
            peer = self.server.credentials
            envelope = decode_message(self.rfile.read(int(sizes[0])),peer['protocol'],peer['outbound_key'],
                                      LOCAL,peer['id'],int(self.server.clock()))
            fingerprint = digest(canonical(envelope))
            with self.server.lock:
                previous = self.server.inbox.setdefault(envelope.message_id,(fingerprint,envelope))
                if previous[0] != fingerprint:
                    raise BoundaryError('mock_message_conflict')
            if self.server.fault == 'drop_after_commit':
                self.close_connection = True
                return
            receipt = dict(sender=peer['id'],recipient=LOCAL,protocol=peer['protocol'],
                           message_id=envelope.message_id,sha256=envelope.sha256)
            key = '0'*64 if self.server.fault == 'forged_ack' else peer['inbound_key']
            self._reply(200,canonical({'receipt':receipt,'signature':mac(key,'document-ack-v1',receipt)}))
        except (OSError, BoundaryError):
            self.close_connection = True
            self._reply(403)
