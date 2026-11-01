import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import validate from '../scripts/ci/validate_dependency_report.cjs';

test('npm 转换器的空报告和非空漏洞报告都符合实例官方 schema', () => {
  const py = process.env.PY || '/opt/homebrew/bin/python3.11';
  const code = `import importlib.util,json
spec=importlib.util.spec_from_file_location('audit','scripts/ci/npm_audit_to_gitlab.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
audit={'advisories':{'1':{'module_name':'example','id':1,'severity':'high','title':'test advisory','url':'https://example.org/advisory','findings':[{'version':'1.0','paths':[]}]}}}
print(json.dumps([m.build_report([]),m.build_report(m.convert(audit))]))`;
  const proc = spawnSync(py, ['-c',code], {encoding:'utf8'});
  assert.equal(proc.status, 0, proc.stderr);
  for(const report of JSON.parse(proc.stdout)) {
    assert.equal(validate(report), true, JSON.stringify(validate.errors));
    const bad = structuredClone(report);delete bad.scan.analyzer;
    assert.equal(validate(bad), false, '缺少 analyzer 的旧格式必须失败');
  }
});
