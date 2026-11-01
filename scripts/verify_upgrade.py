"""只读核对升级版本、任务身份与原配置；不创建任务、不改变授权。"""
import argparse
import http.client
import json
from pathlib import Path
import socket
import sqlite3


def preserved_jobs(before, after):
    current = {row['id']: row for row in after}
    for row in before:
        if row['id'] not in current:
            raise ValueError('升级后缺少原任务：' + row['id'])
        live = current[row['id']]
        if row['uid'] != live['uid'] or json.loads(row['spec']) != json.loads(live['spec']):
            raise ValueError('原任务身份或参数发生变化：' + row['id'])
        if row['state'] == 'completed' and live['state'] != 'completed':
            raise ValueError('已完成任务状态退化：' + row['id'])
    return len(before)


def jobs(path):
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('任务数据库完整性检查失败')
        return [dict(row) for row in db.execute('SELECT id,uid,spec,state FROM jobs')]


class Client(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=15)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(15)
        self.sock.connect(str(self.path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--var', type=Path, required=True)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--socket', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--uid', type=int, default=1000)
    args = parser.parse_args()
    client = Client(args.socket)
    try:
        client.request('GET', '/app/video-enhance/api/status',
                       headers={'X-Trim-Userid': str(args.uid)})
        response = client.getresponse()
        status = json.loads(response.read())
        if response.status != 200 or status.get('version') != args.version:
            raise ValueError('运行版本未达到预期：' + str(status.get('version')))
    finally:
        client.close()
    count = preserved_jobs(jobs(args.backup / 'tasks.sqlite'), jobs(args.var / 'tasks.sqlite'))
    baseline = json.loads((args.backup / 'baseline.json').read_text())
    for name in baseline['configs']:
        if Path(name).name != name:
            raise ValueError('备份配置名不合法')
        if (args.backup / name).read_bytes() != (args.var / name).read_bytes():
            raise ValueError('原配置发生变化：' + name)
    # 仅输出验收摘要，status内CSRF和配置正文不导出。
    print(json.dumps(dict(version=args.version, preserved_jobs=count,
                          preserved_configs=baseline['configs'], integrity_check='ok'),
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
