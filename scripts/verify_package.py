"""只读验收官方 fnpack 包；不解包到应用目录，不启动任何视频任务。"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile


class DigestReader:
    def __init__(self, source):
        self.source = source
        self.digest = hashlib.md5()

    def read(self, size=-1):
        data = self.source.read(size)
        self.digest.update(data)
        return data


def safe_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('包内路径非法: ' + name)
    return str(path)


def verify(package, stage, version):
    expected = ('version.py', 'server.py', 'worker.py', 'guard.py',
                'security.py', 'platform_api.py', 'job_store.py', 'media_plan.py',
                'www/index.html', 'www/app.js', 'www/style.css')
    found = {}
    manifest = None
    asset_digest = None
    outer_names = {}
    lifecycle_digest = None
    with tarfile.open(package, 'r|gz') as outer:
        for item in outer:
            name = safe_name(item.name)
            if name in outer_names and not (outer_names[name] and item.isdir()):
                raise ValueError('外层重复条目: ' + name)
            outer_names[name] = item.isdir()
            if name == 'cmd/main':
                if not item.isfile() or not item.mode & 0o111 or item.size > 65536:
                    raise ValueError('启动脚本类型、权限或大小错误')
                lifecycle_digest = hashlib.sha256(outer.extractfile(item).read()).hexdigest()
            elif name == 'manifest':
                if item.size > 65536:
                    raise ValueError('manifest过大')
                manifest = outer.extractfile(item).read().decode('utf-8')
            elif name == 'app.tgz':
                reader = DigestReader(outer.extractfile(item))
                seen = {}
                with tarfile.open(fileobj=reader, mode='r|gz') as app:
                    for entry in app:
                        entry_name = safe_name(entry.name)
                        parts = PurePosixPath(entry_name).parts
                        if any('.bak' in p for p in parts) or entry_name in (
                                'frontend.js', 'auth-flow.mjs', 'ui-state.mjs',
                                'www/trap.js', 'app.sock'):
                            raise ValueError('应用含开发残留: ' + entry_name)
                        if entry_name in expected and not entry.isfile():
                            raise ValueError('核心文件类型错误: ' + entry_name)
                        fingerprint = (entry.type, entry.linkname, entry.mode)
                        if entry.isfile():
                            digest = hashlib.sha256()
                            source = app.extractfile(entry)
                            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                                digest.update(chunk)
                            fingerprint += (digest.hexdigest(),)
                            if entry_name in expected:
                                found[entry_name] = digest.hexdigest()
                        # fnpack会追加config；只接受内容/类型/权限完全一致的重复项。
                        if entry_name in seen and seen[entry_name] != fingerprint:
                            raise ValueError('应用重复条目存在覆盖冲突: ' + entry_name)
                        seen[entry_name] = fingerprint
                while reader.read(1024 * 1024):
                    pass
                asset_digest = reader.digest.hexdigest()
    if manifest is None or asset_digest is None:
        raise ValueError('缺少manifest或app.tgz')
    if lifecycle_digest != hashlib.sha256((stage / 'cmd' / 'main').read_bytes()).hexdigest():
        raise ValueError('启动脚本与构建区不一致: cmd/main')
    fields = dict(re.findall(r'^\s*(\w+)\s*=\s*(.*?)\s*$', manifest, re.M))
    if fields.get('version') != version:
        raise ValueError('安装包版本不匹配')
    if fields.get('checksum') != asset_digest:
        raise ValueError('app.tgz checksum不匹配')
    for name in expected:
        digest = hashlib.sha256((stage / 'app' / name).read_bytes()).hexdigest()
        if found.get(name) != digest:
            raise ValueError('包内文件与构建区不一致: ' + name)
    return {'version': version, 'checksum_verified': True, 'lifecycle_verified': True,
            'core_files_verified': list(expected), 'development_residue': False,
            'package_bytes': package.stat().st_size}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.package, args.stage, args.version), ensure_ascii=False))
