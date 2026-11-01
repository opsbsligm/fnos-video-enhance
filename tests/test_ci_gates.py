"""CI 门禁脚本自身的测试。

门禁是"允许合并"的唯一判据，所以它本身必须有测试：一个不会被测试的裁决脚本，
和一个永远绿的流水线一样不可信。这里覆盖两条真实踩过的坑：
  1) 分析器报出 High 但作业 success —— 裁决脚本必须失败；
  2) 豁免清单会变成新的静默通道 —— 过期条目与已失效条目都必须失败。
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT/'scripts'/'ci'


def load(name):
    spec = importlib.util.spec_from_file_location(name, CI/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def finding(severity, rule='r1', file='src/a.py', line=1):
    return {'severity': severity, 'name': f'{severity} finding',
            'identifiers': [{'type': 'semgrep_id', 'value': rule}],
            'location': {'file': file, 'start_line': line}}


class AdjudicationTests(unittest.TestCase):
    def run_gate(self, findings, waivers=None, extra=()):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name, typ in load('assess_sast_findings').REQUIRED_REPORTS.items():
                (base/name).write_text(json.dumps({'scan': {'status': 'success', 'type': typ},
                    'vulnerabilities': findings if typ == 'sast' else []}), encoding='utf-8')
            triage = base/'triage.json'
            triage.write_text(json.dumps({'waivers': waivers or []}), encoding='utf-8')
            proc = subprocess.run(
                [sys.executable, str(CI/'assess_sast_findings.py'), '--dir', str(base),
                 '--triage', str(triage), *extra],
                capture_output=True, text=True)
            return proc

    def test_high_finding_blocks_even_though_analyzer_exited_zero(self):
        proc = self.run_gate([finding('High'), finding('Medium'), finding('Info'), finding('Low')])
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn('high ', proc.stdout)
        self.assertIn('medium ', proc.stdout)
        # Low/Info 只报告不阻断，避免把噪音当成豁免对象。
        self.assertNotIn('[未处置] low', proc.stdout)

    def test_clean_scan_passes(self):
        proc = self.run_gate([finding('Info')])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('安全裁决通过', proc.stdout)

    def test_missing_analyzer_report_is_not_silence_but_failure(self):
        """扫描器整体失联时必须失败：空目录里"读到 0 条"不等于没有漏洞。

        实证过的静默通道：旧版本在没有任何报告时输出"（无发现）… 安全裁决通过"
        并退出 0；只要 semgrep 作业没跑成或 artifacts 没被 needs 取到，门禁就变绿。
        """
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            proc = subprocess.run(
                [sys.executable, str(CI/'assess_sast_findings.py'), '--dir', str(base),
                 '--triage', str(base/'none.json')],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn('无法裁决', proc.stdout)
            self.assertNotIn('安全裁决通过', proc.stdout)

    def test_each_required_report_missing_or_failed_blocks(self):
        mod = load('assess_sast_findings')
        for missing in mod.REQUIRED_REPORTS:
            with self.subTest(report=missing), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                for name, typ in mod.REQUIRED_REPORTS.items():
                    if name != missing:
                        (base/name).write_text(json.dumps({'scan': {'status': 'success', 'type': typ},
                                                          'vulnerabilities': []}))
                with self.assertRaises(ValueError):mod.load_findings(base)
                for bad in ({}, {'scan': {'status': 'failure'}},
                            {'scan': {'status': 'success', 'type': mod.REQUIRED_REPORTS[missing]}},
                            {'scan': {'status': 'success', 'type': mod.REQUIRED_REPORTS[missing]},
                             'vulnerabilities': [{}]}):
                    (base/missing).write_text(json.dumps(bad))
                    with self.assertRaises(ValueError):mod.load_findings(base)

    def test_waivers_require_reviewer_reason_expiry_and_no_duplicates(self):
        entry = {'fingerprint': 'r1@src/a.py:1', 'reason': '有证据的误报',
                 'reviewed_by': 'liguangming', 'expires_at': '2099-01-01', 'recheck': '代码变化复核'}
        for field in entry:
            bad = dict(entry);bad.pop(field)
            proc = self.run_gate([finding('High')], [bad])
            self.assertEqual(proc.returncode, 1, proc.stdout)
        for change in ({'reviewed_by': 'codex-agent'}, {'expires_at': 'bad'}, {'reason': ' '}):
            self.assertEqual(self.run_gate([finding('High')], [dict(entry, **change)]).returncode, 1)
        self.assertEqual(self.run_gate([finding('High')], [entry, entry]).returncode, 1)

    def test_ci_config_ships_adjudicator_reports_and_triage(self):
        """配置层把守：裁决作业声明要消费的三份报告，扫描端必须真的下载/存在。

        踩过的坑：依赖扫描把 gl-dependency-scanning-report.json 只挂到 reports:，
        没进 artifacts.paths；needs.artifacts:true 取的是 archive，于是裁决作业
        拿不到它（pipeline 986 / job 6322）。豁免清单同样必须入库，否则门禁
        读的是"不存在的文件"，等价于没有豁免通道。
        """
        self.assertTrue((CI/'sast-triage.json').exists(), '豁免清单必须入库')
        ci = (ROOT/'.gitlab-ci.yml').read_text(encoding='utf-8')
        block = ci.split('依赖漏洞扫描:', 1)[1].split('安全裁决门禁:', 1)[0]
        paths = block.split('paths:', 1)[1].split(']', 1)[0]
        for name in ('npm-audit.json', 'gl-dependency-scanning-report.json'):
            self.assertIn(name, paths, f'{name} 必须列入 artifacts.paths，否则下游取不到')
        # 压缩产物行号不可定位、判定口径由第三方 SDK 决定，必须排除产物保留源码。
        semgrep = ci.split('semgrep-sast:', 1)[1].split('secret_detection:', 1)[0]
        self.assertIn('src/www', semgrep, '扫描必须排除 esbuild 压缩产物目录')
        self.assertIn('src/frontend.js', (CI/'sast-triage.json').read_text(encoding='utf-8'))

    def test_waiver_with_reason_unblocks(self):
        proc = self.run_gate([finding('High', rule='ssrf', file='src/a.py', line=3)],
                             [{'fingerprint': 'ssrf@src/a.py:3', 'reason': '固定回环地址，无外部输入',
                               'recheck': '边界变化时复查', 'reviewed_by': 'liguangming', 'expires_at': '2099-01-01'}])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('固定回环地址', proc.stdout)

    def test_expired_waiver_blocks(self):
        proc = self.run_gate([finding('High', rule='ssrf', file='src/a.py', line=3)],
                             [{'fingerprint': 'ssrf@src/a.py:3', 'reason': '已过期',
                               'recheck': '边界变化时复查', 'reviewed_by': 'liguangming', 'expires_at': '2020-01-01'}])
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn('豁免已过期', proc.stdout)

    def test_stale_waiver_blocks(self):
        """代码里已经不再报出的条目必须删除，防止豁免清单只增不减。"""
        proc = self.run_gate([finding('Info')],
                             [{'fingerprint': 'gone@src/old.py:1', 'reason': '历史条目',
                               'recheck': '边界变化时复查', 'reviewed_by': 'liguangming', 'expires_at': '2099-01-01'}])
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn('已失效条目', proc.stdout)

    def test_fingerprint_moves_with_code(self):
        """改动被保护的代码后行号变化，豁免自动失效，必须重新定性。"""
        proc = self.run_gate([finding('High', rule='ssrf', file='src/a.py', line=9)],
                             [{'fingerprint': 'ssrf@src/a.py:3', 'reason': '行号已漂移',
                               'recheck': '边界变化时复查', 'reviewed_by': 'liguangming', 'expires_at': '2099-01-01'}])
        self.assertEqual(proc.returncode, 1, proc.stdout)


class NpmAuditReportTests(unittest.TestCase):
    def setUp(self):
        self.mod = load('npm_audit_to_gitlab')

    def test_advisory_becomes_dependency_finding(self):
        audit = {'advisories': {'1': {
            'module_name': 'esbuild', 'id': 111, 'severity': 'high', 'title': 'RCE',
            'overview': '远程代码执行', 'url': 'https://example/advisory', 'cves': ['CVE-2024-1'],
            'cvss_score': 9.8, 'findings': [{'version': '0.25.12', 'paths': ['esbuild']}]}}}
        vulns = self.mod.convert(audit)
        self.assertEqual(len(vulns), 1)
        self.assertEqual(vulns[0]['severity'], 'High')
        self.assertEqual(vulns[0]['location']['dependency']['package']['name'], 'esbuild')
        self.assertEqual(vulns[0]['location']['dependency']['package']['version'], '0.25.12')
        self.assertIn('CVE-2024-1', [i['value'] for i in vulns[0]['identifiers']])

    def test_moderate_maps_to_medium_so_it_still_blocks(self):
        audit = {'advisories': {'2': {'module_name': 'x', 'id': 2, 'severity': 'moderate',
                                      'title': 't', 'findings': [{'version': '1.0.0', 'paths': []}]}}}
        self.assertEqual(self.mod.convert(audit)[0]['severity'], 'Medium')

    def test_npm_v2_format_is_parsed(self):
        """npm 11 的 auditReportVersion 2 用 vulnerabilities 而不是 advisories；
        漏掉这个分支就会把真漏洞静默转成 0 条报告。"""
        audit = {'auditReportVersion': 2, 'metadata': {'vulnerabilities': {'critical': 1, 'total': 1}},
                 'vulnerabilities': {'minimist': {
                     'name': 'minimist', 'severity': 'critical', 'isDirect': True,
                     'range': '1.0.0 - 1.2.5', 'nodes': ['node_modules/minimist'],
                     'fixAvailable': {'name': 'minimist', 'version': '1.2.8'},
                     'via': [{'source': 1097678, 'name': 'minimist', 'dependency': 'minimist',
                              'title': 'Prototype Pollution in minimist',
                              'url': 'https://github.com/advisories/GHSA-xvch-5gv4-984h',
                              'severity': 'critical', 'cwe': ['CWE-1321'],
                              'cvss': {'score': 9.8}, 'range': '>=1.0.0 <1.2.6'}]}}}
        vulns = self.mod.convert(audit, versions={'minimist': ['1.2.0']})
        self.assertEqual(len(vulns), 1)
        self.assertEqual(vulns[0]['severity'], 'Critical')
        self.assertEqual(vulns[0]['location']['dependency']['package']['version'], '1.2.0')
        self.assertEqual(vulns[0]['location']['dependency']['paths'], ['node_modules/minimist'])

    def test_self_check_refuses_empty_report_when_summary_reports_vulns(self):
        """汇总说有阻断级漏洞而转换结果为 0 条，必须失败而不是产出'无漏洞'报告。"""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)/'npm-audit.json'
            src.write_text(json.dumps({'metadata': {'vulnerabilities': {'critical': 2, 'total': 2}}}),
                           encoding='utf-8')
            proc = subprocess.run([sys.executable, str(CI/'npm_audit_to_gitlab.py'),
                                   '--input', str(src), '--output', str(Path(tmp)/'out.json')],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertIn('自检失败', proc.stderr)

    def test_clean_lock_yields_empty_report(self):
        self.assertEqual(self.mod.convert({'advisories': {}, 'metadata': {'vulnerabilities': {'total': 0}}}), [])
        # 报告骨架仍须完整，GitLab 才能把它当依赖扫描工件解析。
        report = self.mod.build_report([])
        self.assertEqual(report['scan']['type'], 'dependency_scanning')
        self.assertEqual(report['vulnerabilities'], [])

    def test_unusable_input_fails_instead_of_empty_report(self):
        """镜像源不支持 audit 时不能产出"0 漏洞"的假绿。"""
        for payload in ('', 'not json at all', '{"error":{"code":"E404"}}'):
            with self.subTest(payload=payload[:20]):
                with tempfile.TemporaryDirectory() as tmp:
                    src = Path(tmp)/'npm-audit.json'
                    src.write_text(payload, encoding='utf-8')
                    proc = subprocess.run(
                        [sys.executable, str(CI/'npm_audit_to_gitlab.py'), '--input', str(src),
                         '--output', str(Path(tmp)/'out.json')],
                        capture_output=True, text=True)
                    self.assertNotEqual(proc.returncode, 0, payload[:40])
                    self.assertFalse((Path(tmp)/'out.json').exists())


if __name__ == '__main__':
    unittest.main()
