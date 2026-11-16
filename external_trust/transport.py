"""已注册的本地测试 Agent 传输。协议负载没有地址或回调权限。"""
from contextlib import contextmanager
from http.client import HTTPConnection, HTTPException
import re
import socket
from threading import Timer
from uuid import uuid4

from .contracts import BoundaryError, LOCAL, WIRE_BYTES, decode, exact, mac, verify_mac


PREFIX = '/sky-external-trust/v1'
DEADLINE = 3


def port_number(value):
    if type(value) is not int or not 1024 <= value <= 65535:
        raise BoundaryError('loopback_port_required')
    return value


def read_http_body(response, limit):
    lengths = response.headers.get_all('Content-Length') or []
    if (len(lengths) != 1 or response.headers.get('Transfer-Encoding') is not None
            or response.headers.get('Content-Encoding', 'identity') != 'identity'
            or not re.fullmatch(r'[0-9]{1,6}', lengths[0]) or int(lengths[0]) > limit):
        raise BoundaryError('invalid_http_length')
    size = int(lengths[0])
    if size and response.headers.get('Content-Type','').split(';')[0] != 'application/json':
        raise BoundaryError('invalid_http_media_type')
    data = response.read(size)
    if len(data) != size:
        raise BoundaryError('truncated_http_body')
    return data


class LoopbackTransport:
    @contextmanager
    def _channel(self, peer, method, after=None):
        conn = HTTPConnection('127.0.0.1', port_number(peer['port']), timeout=DEADLINE)
        timer = None
        try:
            conn.connect()
            original_socket = conn.sock

            def stop():
                try:
                    original_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            timer = Timer(DEADLINE, stop)
            timer.daemon = True
            timer.start()
            challenge = {'nonce':uuid4().hex, 'peer':peer['id'], 'audience':LOCAL, 'protocol':peer['protocol']}
            conn.request('GET', PREFIX+'/challenge/'+challenge['nonce'])
            response = conn.getresponse()
            if response.status != 200:
                raise BoundaryError('peer_challenge_rejected')
            data = exact(decode(read_http_body(response, 1024), 1024), ('challenge','signature'))
            if data['challenge'] != challenge or conn.sock is not original_socket:
                raise BoundaryError('peer_challenge_mismatch')
            verify_mac(peer['inbound_key'], 'transport-challenge-v1', challenge, data['signature'])
            request = {'challenge':challenge, 'method':method, 'after':after}
            headers = {'X-SKY-Challenge':challenge['nonce'],
                       'X-SKY-Proof':mac(peer['outbound_key'], 'transport-client-v1', request),
                       'Content-Type':'application/json'}
            if after is not None:
                headers['X-SKY-After'] = after
            yield conn, headers
        except (OSError, HTTPException) as error:
            raise BoundaryError('peer_transport_unavailable') from error
        finally:
            if timer is not None:
                timer.cancel()
            conn.close()

    def pull(self, peer, after=None):
        with self._channel(peer,'GET',after) as (conn,headers):
            conn.request('GET', PREFIX+'/documents', headers=headers)
            response = conn.getresponse()
            if response.status not in (200,204):
                raise BoundaryError('peer_pull_rejected')
            data = read_http_body(response, WIRE_BYTES)
            if (response.status == 204) != (not data):
                raise BoundaryError('peer_pull_status_mismatch')
            return data

    def send(self, peer, wire, envelope):
        if not 0 < len(wire) <= WIRE_BYTES:
            raise BoundaryError('wire_size_limit')
        with self._channel(peer,'POST') as (conn,headers):
            conn.request('POST', PREFIX+'/documents', body=wire, headers=headers)
            response = conn.getresponse()
            if response.status != 200:
                raise BoundaryError('peer_send_rejected')
            ack = exact(decode(read_http_body(response, 1024), 1024), ('receipt','signature'))
            wanted = dict(sender=peer['id'], recipient=LOCAL, protocol=peer['protocol'],
                          message_id=envelope.message_id, sha256=envelope.sha256)
            if ack['receipt'] != wanted:
                raise BoundaryError('peer_ack_mismatch')
            verify_mac(peer['inbound_key'], 'document-ack-v1', wanted, ack['signature'])
            return ack
