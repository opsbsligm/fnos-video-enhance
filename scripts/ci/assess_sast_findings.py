#!/usr/bin/env python3
"""SAST/密钥扫描裁决门禁。

为什么需要它：GitLab 的 `allow_failure: false` 只约束**分析器脚本的退出码**，
分析器发现漏洞时照样退出 0、作业 success、漏洞看板也可以一条不显示。
实证：pipeline 980 / job 6268（semgrep）报出 24 条 finding（含 1 条 High），
作业 success，`/projects/19/vulnerabilities` 返回 0 条。也就是说在此之前的
"安全扫描全绿"并不代表没有高危问题被合并。

本门禁读取分析器 artifacts，自建裁决：
  Critical/High  → 默认阻断（fail）
  Medium         → 默认阻断（可用带理由的豁免条目放行）
  Low            → 报告不阻断；Unknown 默认阻断
  Info           → 报告不阻断（多为测试/验收脚本的 assert 类告警）
任何豁免必须在 scripts/ci/sast-triage.json 里逐条登记：指纹 + 理由 + 失效条件 +
到期日 + 复核人。条目过期即视为未豁免，强制重新审视；未使用的条目同样报错，
防止"豁免清单只增不减"变成第二个静默通道。
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

BLOCKING = {'critical', 'high', 'medium', 'unknown'}
SEVERITY_ORDER = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3, 'unknown': 4, 'info': 5}
REPORTS = ('gl-sast-report.json', 'gl-secret-detection-report.json', 'gl-dependency-scanning-report.json')

# 三类分析器均为必需输入；报告缺失、失败或结构异常一律拒绝合并。
REQUIRED_REPORTS = dict(zip(REPORTS, ('sast', 'secret_detection', 'dependency_scanning')))


def fingerprint(finding: dict) -> str:
    """稳定指纹：规则 + 文件 + 行号。分析器不提供 fingerprint 字段，
    自己构造；行号会随代码移动而变化，这正是我们想要的——代码一改就必须重新定性。"""
    ids = finding.get('identifiers') or []
    rule = ''
    for i in ids:
        if i.get('type') in ('semgrep_id', 'cve', 'gemnasium', 'osv_id'):
            rule = i.get('value') or ''
            break
    if not rule:
        rule = (ids[0].get('value') if ids else finding.get('check_id', 'unknown')) or 'unknown'
    loc = finding.get('location') or {}
    line = loc.get('start_line') or loc.get('line') or 0
    return f"{rule}@{loc.get('file', '?')}:{line}"


def load_findings(base: Path) -> list[dict]:
    out = []
    for name, scan_type in REQUIRED_REPORTS.items():
        p = base / name
        if not p.is_file():
            raise ValueError(f'分析器报告缺失：{name}')
        data = json.loads(p.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError(f'{name} 必须是报告对象')
        scan = data.get('scan')
        if not isinstance(scan, dict) or scan.get('status') != 'success' or scan.get('type') != scan_type:
            raise ValueError(f'{name} 扫描未成功或类型错误')
        items = data.get('vulnerabilities')
        if not isinstance(items, list):
            raise ValueError(f'{name} 缺少 vulnerabilities 数组')
        for item in items:
            if not isinstance(item, dict) or str(item.get('severity', '')).lower() not in SEVERITY_ORDER:
                raise ValueError(f'{name} 发现缺少有效严重度')
            if not isinstance(item.get('location'), dict) or not item['location'].get('file') or not item.get('identifiers'):
                raise ValueError(f'{name} 发现缺少位置或规则标识')
            out.append(dict(item, _report=name))
    return out


def load_triage(path: Path) -> dict:
    if not path.exists():
        return {}
    doc = json.loads(path.read_text(encoding='utf-8'))
    entries = doc.get('waivers')
    if not isinstance(entries, list):
        raise ValueError('豁免清单缺少 waivers 数组')
    result = {}
    for entry in entries:
        for field in ('fingerprint', 'reason', 'recheck', 'expires_at', 'reviewed_by'):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise ValueError(f'豁免缺少必填字段：{field}')
        if entry['reviewed_by'] != 'owner':
            raise ValueError('豁免须经 owner 裁决，并在 MR 绑定 HEAD 复核')
        datetime.date.fromisoformat(entry['expires_at'])
        fp = entry['fingerprint']
        if fp in result:
            raise ValueError(f'重复豁免：{fp}')
        result[fp] = entry
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='.', help='存放分析器报告产物的目录')
    ap.add_argument('--triage', default=str(Path(__file__).with_name('sast-triage.json')))
    ap.add_argument('--block', default=','.join(sorted(BLOCKING)),
                    help='阻断级别，逗号分隔（critical,high,medium,low,unknown,info）')
    args = ap.parse_args()

    block = {s.strip().lower() for s in args.block.split(',') if s.strip()}
    base = Path(args.dir)
    try:
        findings = load_findings(base)
        waivers = load_triage(Path(args.triage))
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f'无法裁决：{error}')
        return 1
    today = datetime.date.today()

    print(f"读取到 {len(findings)} 条扫描发现；阻断级别={sorted(block, key=lambda s: SEVERITY_ORDER.get(s, 9))}")
    used: set[str] = set()
    blockers: list[str] = []
    rows = []

    for f in findings:
        sev = (f.get('severity') or 'unknown').lower()
        fp = fingerprint(f)
        name = (f.get('name') or f.get('message') or '')[:58]
        if fp in waivers:
            used.add(fp)
            w = waivers[fp]
            expires = w.get('expires_at')
            expired = bool(expires) and datetime.date.fromisoformat(expires) < today
            status = '已豁免(过期!)' if expired else '已豁免'
            rows.append((SEVERITY_ORDER.get(sev, 9), sev, status, fp, name))
            if sev in block and expired:
                blockers.append(f"豁免已过期 {expires}：{sev} {fp} — {name}")
            elif sev in block:
                print(f"  豁免    {sev:8} {fp}  理由：{w.get('reason', '')[:110]}")
            continue
        rows.append((SEVERITY_ORDER.get(sev, 9), sev, '未处置' if sev in block else '不阻断', fp, name))
        if sev in block:
            blockers.append(f"{sev} {fp} — {name}")

    # 只增不减的豁免清单同样危险：条目对应的代码早已不存在时必须删除。
    stale = [k for k in waivers if k not in used]

    print()
    for _, sev, verdict, fp, name in sorted(rows):
        if verdict != '不阻断':
            print(f"[{verdict}] {sev} {fp} {name}")
    if not rows:
        print('（无发现）')

    rc = 0
    if blockers:
        print(f"\n阻断 {len(blockers)} 条：")
        for b in blockers:
            print('  ✗', b)
        print("\n处置二选一：修代码消除该发现；或在 scripts/ci/sast-triage.json 增加"
              "条目（fingerprint/reason/recheck/expires_at/reviewed_by），后者须由审查人认可。")
        rc = 1
    if stale:
        print(f"\n豁免清单存在已失效条目（当前扫描已不再报出，请删除）：")
        for k in stale:
            print('  !', k)
        rc = 1
    if rc == 0:
        print('\n安全裁决通过：无未处置的阻断级发现，豁免清单与当前扫描一致。')
    return rc


if __name__ == '__main__':
    sys.exit(main())
