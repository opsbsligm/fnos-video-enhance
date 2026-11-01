#!/usr/bin/env python3
"""把 `npm audit --json` 的结果转成 GitLab Dependency Scanning 报告。

使用已锁定的 package-lock.json 执行 npm audit，将公告转为实例支持的
GitLab 15.2.4 依赖报告；CI 在上传之前按官方 schema 校验。
此作业覆盖 npm 依赖，不代表对 NAS 外置 Python/CUDA 运行包已完成漏洞扫描。

三条不容妥协的纪律：
  1) 输入不可用即失败：npm audit 因镜像源不支持/网络失败而没有给出结果时退出非零，
     绝不产出一份"0 条漏洞"的漂亮空报告。
  2) 汇总与条数必须自洽：audit 自己的 metadata 汇总说存在阻断级漏洞，而本脚本转出
     0 条，即判失败。npm 的报告格式在 v1(`advisories`) 与 v2(`vulnerabilities`)
     之间变过，这条自检就是防止解析分支写漏时静默丢漏洞。
  3) 两种格式都支持，并逐条保留公告自身等级，不用包级聚合等级冒充。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

# npm 的等级词与 GitLab 报告等级对齐；moderate → Medium，
# 这样中等漏洞会进入裁决门禁的默认阻断集，不会被降级成噪音。
SEVERITY = {'critical': 'Critical', 'high': 'High', 'moderate': 'Medium',
            'low': 'Low', 'info': 'Info', 'unknown': 'Unknown'}
BLOCKED_SUMMARY_KEYS = ('critical', 'high', 'moderate')


def _identifiers(value: dict, advisory_id, url: str, cvss=None, cwe=()) -> list[dict]:
    ids = [{'type': 'npm_advisory_id', 'name': 'npm advisory', 'value': str(advisory_id)}]
    if url: ids[0]['url'] = url
    for cve in (value.get('cves') or []):
        if cve:
            ids.append({'type': 'cve', 'name': 'CVE', 'value': cve,
                        'url': f'https://nvd.nist.gov/vuln/detail/{cve}'})
    for item in cwe:
        ids.append({'type': 'cwe', 'name': 'CWE', 'value': str(item).replace('CWE-', '')})
    if cvss is not None:
        ids.append({'type': 'cvss', 'name': 'CVSS', 'value': str(cvss)})
    return ids


def _finding(*, module, version, severity, title, overview, identifiers, url,
             lock_path, paths, fix_available) -> dict:
    return {
        'id': hashlib.sha256(f"npm-{module}-{version}-{identifiers[0]['value']}".encode()).hexdigest(),
        'name': (title or '依赖漏洞').strip(),
        'severity': SEVERITY.get(str(severity).lower(), 'Unknown'),
        'description': (overview or title or '').strip(),
        'identifiers': identifiers,
        'links': [{'url': url}] if url else [],
        'solution': f'升级到 {fix_available}' if fix_available else '见公告中的修复建议',
        'location': {
            'file': lock_path,
            'dependency': {
                'package': {'name': module, 'ecosystem': 'npm', 'version': version},
                'path': lock_path,
                'package_manager': 'npm',
                'version': version,
                # 依赖链用于人工定位是哪条路径引入，截断避免报告膨胀。
                'paths': list(paths)[:8],
            },
        },
    }


def versions_from_lock(lock_path: Path) -> dict[str, list[str]]:
    """从 package-lock.json 取每个包的实际安装版本；读不到就退回影响范围。"""
    try:
        lock = json.loads(lock_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, list[str]] = {}
    for name, info in (lock.get('packages') or {}).items():
        if not name:
            continue
        leaf = name.rsplit('node_modules/', 1)[-1]
        version = info.get('version')
        if version:
            out.setdefault(leaf, []).append(version)
    return out


def convert(audit: dict, lock_path: str = 'package-lock.json',
            versions: dict[str, list[str]] | None = None) -> list[dict]:
    """npm audit（v1/v2）→ GitLab 依赖扫描漏洞条目列表。"""
    versions = versions or {}
    findings: list[dict] = []

    if audit.get('advisories'):  # auditReportVersion 1（npm ≤ 6）
        for advisory in audit['advisories'].values():
            module = advisory.get('module_name') or 'unknown'
            seen = sorted({f.get('version', '') for f in (advisory.get('findings') or []) if f.get('version')})
            paths = sorted({p for f in (advisory.get('findings') or []) for p in (f.get('paths') or [])})
            fix = advisory.get('fixAvailable')
            fix_version = str(fix.get('version')) if isinstance(fix, dict) else ('' if not fix else '见建议')
            for version in (seen or ['unknown']):
                findings.append(_finding(
                    module=module, version=version, severity=advisory.get('severity'),
                    title=advisory.get('title'), overview=advisory.get('overview'),
                    identifiers=_identifiers(advisory, advisory.get('id', 'unknown'),
                                             advisory.get('url') or '', advisory.get('cvss_score')),
                    url=advisory.get('url') or '', lock_path=lock_path, paths=paths,
                    fix_available=fix_version))
        return findings

    for module, entry in (audit.get('vulnerabilities') or {}).items():
        # via 里的 dict 条目才是具体公告；字符串条目表示"由该依赖传递引入"，
        # 没有独立公告，跳过以免重复计数。
        advisories = [v for v in (entry.get('via') or []) if isinstance(v, dict)]
        paths = sorted(entry.get('nodes') or []) or [f'node_modules/{module}']
        fix = entry.get('fixAvailable')
        fix_version = str(fix.get('version')) if isinstance(fix, dict) else ('' if not fix else '见建议')
        for adv in advisories:
            # 版本优先取 lock 里的实际安装版本；定位不到时用 'unknown' 而不是编一个
            # 看起来精确但没核实的版本号。
            for version in (versions.get(module) or ['unknown']):
                findings.append(_finding(
                    module=module, version=version,
                    severity=adv.get('severity') or entry.get('severity'),
                    title=adv.get('title'), overview=adv.get('title'),
                    identifiers=_identifiers(adv, adv.get('source', 'unknown'), adv.get('url') or '',
                                             (adv.get('cvss') or {}).get('score'), adv.get('cwe') or ()),
                    url=adv.get('url') or '', lock_path=lock_path, paths=paths,
                    fix_available=fix_version))
    return findings


def build_report(vulnerabilities: list[dict], scanner_version: str = 'unknown') -> dict:
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    return {
        'version': '15.2.4',
        'scan': {
            'type': 'dependency_scanning', 'status': 'success',
            'analyzer': {'id': 'npm-audit-adapter', 'name': 'npm audit adapter',
                         'version': '1.0.0', 'vendor': {'name': 'HOMELAB'}},
            'scanner': {'id': 'npm-audit', 'name': 'npm audit', 'version': scanner_version,
                        'vendor': {'name': 'npm'}, 'url': 'https://docs.npmjs.com/cli/commands/npm-audit'},
            'start_time': now, 'end_time': now,
        },
        'vulnerabilities': vulnerabilities,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', default='npm-audit.json', help='npm audit --json 的输出文件')
    ap.add_argument('--output', default='gl-dependency-scanning-report.json')
    ap.add_argument('--lock', default='package-lock.json')
    ap.add_argument('--scanner-version', default='unknown')
    args = ap.parse_args()

    raw = Path(args.input).read_text(encoding='utf-8').strip()
    if not raw:
        print(f'{args.input} 为空：npm audit 没有产出结果（镜像源不支持 audit 接口或网络失败）。',
              file=sys.stderr)
        return 2
    try:
        audit = json.loads(raw)
    except json.JSONDecodeError as error:
        # 镜像源（如 npmmirror）会把 audit 错误以非 JSON 形式混进 stdout，必须判失败。
        print(f'{args.input} 不是合法 JSON（{error}）；前 200 字：{raw[:200]}', file=sys.stderr)
        return 2
    if not isinstance(audit, dict) or audit.get('error'):
        print('npm audit 返回错误或非对象，拒绝生成报告', file=sys.stderr)
        return 2
    if not audit.get('advisories') and not audit.get('vulnerabilities') and 'metadata' not in audit:
        print(f'{args.input} 既无 advisories 也无 vulnerabilities，npm audit 可能未真正执行：'
              f'{raw[:200]}', file=sys.stderr)
        return 2

    findings = convert(audit, lock_path=args.lock, versions=versions_from_lock(Path(args.lock)))
    Path(args.output).write_text(
        json.dumps(build_report(findings, args.scanner_version), ensure_ascii=False, indent=2), encoding='utf-8')

    summary = (audit.get('metadata') or {}).get('vulnerabilities') or {}
    expect = sum(int(summary.get(k, 0)) for k in BLOCKED_SUMMARY_KEYS)
    print(f"依赖扫描完成：npm audit 汇总={summary}；写出 {args.output}（{len(findings)} 条）")
    if expect and not any(v['severity'] in ('Critical', 'High', 'Medium') for v in findings):
        print(f'自检失败：npm audit 汇总报告 {expect} 个阻断级漏洞，但本脚本转出 0 条；'
              '说明报告格式解析分支缺失，禁止以"无漏洞"收尾。', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
