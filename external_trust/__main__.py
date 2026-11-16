"""本地操作员 CLI。外部 Agent 没有管理接口。"""
import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys

from .contracts import BoundaryError, DOC_BYTES, decode, exact
from .gateway import Gateway
from .storage import no_links, read_regular


def _json(result):
    # 控制符和非 ASCII 保持转义，任何文档均以纯文本数据展示。
    print(json.dumps(result, ensure_ascii=True, indent=2, allow_nan=False))


def _secrets_file(path, data):
    no_links(path)
    import os
    fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='ascii') as stream:
        json.dump(data,stream)


def _serve_mock(args):
    from .mock_peer import MockPeer
    from .transport import port_number
    data = exact(decode(read_regular(args.credentials,2048),2048),('id','protocol','inbound_key','outbound_key'))
    if (data['protocol'] not in ('a2a','dsh') or not isinstance(data['id'],str)
            or any(not isinstance(data[key],str) or not re.fullmatch(r'[a-f0-9]{64}',data[key])
                   for key in ('inbound_key','outbound_key'))):
        raise BoundaryError('mock_credentials_invalid')
    with MockPeer(port_number(args.port)) as peer:
        peer.credentials = data
        if args.fixture is not None:
            peer.enqueue(decode(read_regular(args.fixture,DOC_BYTES),DOC_BYTES))
        print('local mock agent ready',flush=True)
        peer.serve_forever()


def main(argv=None):
    cli = argparse.ArgumentParser(description='SKY External Trust Boundary (local mock transport)')
    cli.add_argument('--root',type=Path)
    subs = cli.add_subparsers(dest='action',required=True)
    for name in ('init','enable','disable','status','audit','knowledge','training-manifest','recover'):
        subs.add_parser(name)
    auth = subs.add_parser('authorize')
    auth.add_argument('peer'); auth.add_argument('protocol',choices=('a2a','dsh')); auth.add_argument('port',type=int)
    auth.add_argument('--scopes',nargs='+',choices=('send','receive','import','update'),required=True)
    auth.add_argument('--expires',type=int,required=True)
    auth.add_argument('--mock-credentials',type=Path,required=True)
    for name in ('revoke','draft','pull'):
        subs.add_parser(name).add_argument('peer')
    for name in ('send','import','publish','revoke-approval'):
        subs.add_parser(name).add_argument('approval')
    subs.add_parser('inspect').add_argument('document')
    for name in ('preview','approve'):
        item = subs.add_parser(name)
        item.add_argument('stage',choices=('send','import','update'))
        item.add_argument('document'); item.add_argument('--expires',type=int,required=True)
        if name == 'approve':
            item.add_argument('--confirm',required=True); item.add_argument('--operator',required=True)
    mock = subs.add_parser('mock-peer')
    mock.add_argument('port',type=int); mock.add_argument('--credentials',type=Path,required=True)
    mock.add_argument('--fixture',type=Path)
    args = cli.parse_args(argv)
    try:
        if args.action == 'mock-peer':
            return _serve_mock(args)
        if args.root is None:
            cli.error('--root is required for local operator commands')
        gate = Gateway(args.root,initialize=args.action=='init')
        match args.action:
            case 'init' | 'status': result = gate.status()
            case 'enable' | 'disable': result = gate.switch(args.action=='enable')
            case 'authorize':
                if args.mock_credentials.exists():
                    raise BoundaryError('mock_credentials_already_exist')
                credentials = gate.authorize(args.peer,args.protocol,args.port,args.scopes,args.expires)
                try:
                    _secrets_file(args.mock_credentials,credentials)
                except (OSError,BoundaryError):
                    gate.revoke(args.peer)
                    raise
                result = dict(peer=args.peer, enrolled=True, transport='loopback-only')
            case 'revoke': result = gate.revoke(args.peer)
            case 'draft': result = gate.draft(args.peer,decode(sys.stdin.buffer.read(DOC_BYTES+1),DOC_BYTES))
            case 'pull': result = gate.pull(args.peer)
            case 'preview': result = gate.preview(args.stage,args.document,args.expires)
            case 'approve': result = gate.approve(args.stage,args.document,args.expires,args.confirm,args.operator)
            case 'send': result = gate.send(args.approval)
            case 'import': result = gate.import_candidate(args.approval)
            case 'publish': result = gate.publish_version(args.approval)
            case 'revoke-approval': result = gate.revoke_approval(args.approval)
            case 'recover': result = {'interrupted_sends_closed':gate.recover()}
            case 'inspect': result = gate.inspect(args.document)
            case 'knowledge': result = gate.knowledge()
            case 'audit': result = gate.store.audit_log()
            case 'training-manifest': result = gate.offline_training_manifest()
            case _: raise BoundaryError('unsupported_operator_command')
        _json(result)
    except BoundaryError as error:
        _json({'error':error.code})
        return 2
    except (OSError, ValueError, sqlite3.Error):
        _json({'error':'local_boundary_unavailable'})
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
