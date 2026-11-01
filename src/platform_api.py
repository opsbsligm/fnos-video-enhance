import http.client
import json
import os
import socket
import uuid

class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=8)
        self.path = path
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)

def call(method, data=None):
    token = os.environ.get('TRIM_API_TOKEN')
    if not token:
        raise PermissionError('系统未注入授权令牌，请从飞牛应用中心启动')
    conn = UnixConnection('/var/run/trim_open_gateway_apiscope.socket')
    try:
        conn.request('POST', '/api/v1/trimapp',
                     json.dumps(dict(reqId=uuid.uuid4().hex, req=method, appName='video-enhance', data=data or {})),
                     {'Content-Type': 'application/json', 'Authorization': 'Bearer '+token})
        r = conn.getresponse()
        j = json.loads(r.read(1024 * 1024))
        if r.status != 200 or j.get('code') != 0:
            raise PermissionError('飞牛授权接口拒绝访问：'+str(j.get('msg', r.status)))
        return j.get('data')
    finally:
        conn.close()

def shared_roots():
    return (call('trim.file.getSharedAccessibleFolders') or {}).get('paths', [])

def check_acl(uid, path, write=False):
    data = call('trim.file.checkUserACL', {'uid': int(uid), 'path': path})
    if not isinstance(data, list) or not data or not data[0].get('writable' if write else 'readable'):
        raise PermissionError('当前用户没有目标目录/文件的'+('写入' if write else '读取')+'权限')
