#!/usr/bin/env python3
"""版本一致性门禁：四处版本声明必须完全一致。

背景：0.3.7 发布时 src/www/index.html 的静态版本串曾被漏改，仍是 0.3.6，
运行态与包内清单不一致，只能事后热修。此门禁把"改版本只改一处"的约定
变成可执行检查：任何一处漂移即 CI 失败。
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def version_from_python() -> str:
    m = re.search(r"^VERSION\s*=\s*['\"]([^'\"]+)['\"]", read("src/version.py"), re.M)
    if not m:
        raise SystemExit("src/version.py 未找到 VERSION")
    return m.group(1)


def version_from_manifest() -> str:
    m = re.search(r"^version\s*=\s*(\S+)\s*$", read("packaging/manifest"), re.M)
    if not m:
        raise SystemExit("packaging/manifest 未找到 version")
    return m.group(1)


def version_from_package_json() -> str:
    return json.loads(read("package.json"))["version"]


def versions_from_html() -> list[tuple[str, str]]:
    """index.html 有三处版本注入：样式查询串、品牌脚注、脚本查询串。"""
    html = read("src/www/index.html")
    found = [
        ("style.css?v=", m.group(1))
        for m in re.finditer(r"style\.css\?v=([0-9][0-9.]*)-base", html)
    ] + [
        ("app.js?v=", m.group(1))
        for m in re.finditer(r"app\.js\?v=([0-9][0-9.]*)-js", html)
    ] + [
        ("品牌脚注", m.group(1))
        for m in re.finditer(r"版本\s*([0-9][0-9.]*)", html)
    ]
    if not found:
        raise SystemExit("src/www/index.html 未找到任何版本串")
    return found


def pipeline_versions_declared() -> tuple[list[str], list[str]]:
    text = read("src/version.py")
    m = re.search(r"^PIPELINE_VERSIONS\s*=\s*\(([^)]*)\)", text, re.M)
    if not m:
        raise SystemExit("src/version.py 未找到 PIPELINE_VERSIONS")
    declared = re.findall(r"['\"]([0-9][0-9.]*)['\"]", m.group(1))
    return declared, text


def main() -> int:
    version = version_from_python()
    checks = [
        ("packaging/manifest", version_from_manifest()),
        ("package.json", version_from_package_json()),
    ]
    for where, seen in versions_from_html():
        checks.append((f"src/www/index.html:{where}", seen))

    bad = [(where, seen) for where, seen in checks if seen != version]
    for where, seen in checks:
        print(f"{'OK  ' if seen == version else 'FAIL'} {where} = {seen}")

    # 当前版本必须已登记为可续跑 pipeline，否则升级后自己的任务会被判为旧版。
    declared, _ = pipeline_versions_declared()
    if version not in declared:
        print(f"FAIL src/version.py:PIPELINE_VERSIONS 缺 {version}（现为 {declared}）")
        bad.append(("src/version.py:PIPELINE_VERSIONS", "<missing>"))
    else:
        print(f"OK   src/version.py:PIPELINE_VERSIONS 含 {version}")

    if bad:
        print(f"\n版本不一致：基准 src/version.py={version}，{len(bad)} 处漂移。")
        return 1
    print(f"\n全部版本声明一致：{version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
