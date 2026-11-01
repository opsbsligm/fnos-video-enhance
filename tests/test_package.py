import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('verify_package', Path(__file__).resolve().parents[1] / 'scripts/verify_package.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PackageTests(unittest.TestCase):
    def make_package(self, root, extra=None, checksum=None, version='0.3.5', duplicate=None):
        files = ('version.py', 'server.py', 'worker.py', 'guard.py',
                'security.py', 'platform_api.py', 'job_store.py', 'media_plan.py',
                 'www/index.html', 'www/app.js', 'www/style.css')
        app = io.BytesIO()
        with tarfile.open(fileobj=app, mode='w:gz') as archive:
            for name in files + ((extra,) if extra else ()):
                data = b'fixture'
                path = root / 'app' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
            if duplicate is not None:
                entry = tarfile.TarInfo('version.py')
                entry.size = len(duplicate)
                archive.addfile(entry, io.BytesIO(duplicate))
        payload = app.getvalue()
        manifest = ('version = ' + version + '\nchecksum = ' +
                    (checksum or hashlib.md5(payload).hexdigest()) + '\n').encode()
        package = root / 'test.fpk'
        (root/'cmd').mkdir(exist_ok=True)
        (root/'cmd'/'main').write_bytes(b'#!/bin/bash\nexit 0\n')
        with tarfile.open(package, 'w:gz') as archive:
            # 官方包顺序不可假定，app.tgz先于manifest亦应成功。
            for name, data in [('app.tgz', payload), ('manifest', manifest),
                               ('cmd/main',(root/'cmd'/'main').read_bytes())]:
                entry = tarfile.TarInfo(name)
                if name == 'cmd/main':entry.mode=0o755
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        return package

    def test_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = module.verify(self.make_package(root), root, '0.3.5')
            self.assertTrue(result['checksum_verified'])
            self.assertTrue(result['lifecycle_verified'])
            self.assertEqual(len(result['core_files_verified']), 11)

    def test_reject_bad_packages(self):
        for options in ({'extra': 'www/trap.js'}, {'extra': 'server.py.bak-old'},
                        {'checksum': 'bad'}, {'version': '0.3.4'}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with self.assertRaises(ValueError):
                    module.verify(self.make_package(root, **options), root, '0.3.5')

    def test_reject_changed_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = self.make_package(root)
            (root / 'app/server.py').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                module.verify(package, root, '0.3.5')

    def test_reject_traversal(self):
        for name in ('../outside', '/absolute', 'www/../../outside'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                module.safe_name(name)

    def test_duplicate_must_be_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module.verify(self.make_package(root, duplicate=b'fixture'), root, '0.3.5')
            with self.assertRaises(ValueError):
                module.verify(self.make_package(root, duplicate=b'conflict'), root, '0.3.5')



    def test_security_and_checkpoint_module_mismatch_rejected(self):
        for name in ('security.py', 'platform_api.py', 'job_store.py', 'media_plan.py'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                package=self.make_package(root)
                (root/'app'/name).write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, name):
                    module.verify(package,root,'0.3.5')

    def test_lifecycle_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);package=self.make_package(root)
            (root/'cmd'/'main').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'cmd/main'):
                module.verify(package,root,'0.3.5')

if __name__ == '__main__':
    unittest.main()
