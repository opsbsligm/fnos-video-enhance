"""目录句柄约束：拒绝符号链接和路径逃逸，不依赖前端约束。"""
import os
import stat
from pathlib import Path

EXCLUDED = ()  # 2026-10-07 内容名过滤全部解除：不再按名称/目录/片源屏蔽

def content_allowed(path):
    return not any(x in str(path).casefold() for x in EXCLUDED)

def open_beneath(root, path, directory=False):
    root, path = Path(root), Path(path)
    if not root.is_absolute() or not path.is_absolute():
        raise PermissionError('仅允许绝对路径')
    if '..' in root.parts or '..' in path.parts or not content_allowed(path):
        raise PermissionError('路径不允许')
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        raise PermissionError('路径超出授权目录')
    # 连根路径也逐级 O_NOFOLLOW，避免授权后目录被替换成软链接。
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        components = root.parts[1:] + parts
        for i, part in enumerate(components):
            # 祖先目录只需要穿越，不读取未授权的父目录列表。
            flags = os.O_NOFOLLOW
            if i < len(components) - 1:
                flags |= getattr(os, 'O_PATH', os.O_RDONLY) | os.O_DIRECTORY
            else:
                flags |= os.O_RDONLY
                if directory: flags |= os.O_DIRECTORY
                else: flags |= os.O_NONBLOCK
            new = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = new
        if not directory and not stat.S_ISREG(os.fstat(fd).st_mode):
            raise PermissionError('仅允许普通视频文件，拒绝管道或设备')
        return fd
    except BaseException:
        os.close(fd)
        raise

def matching_root(path, roots):
    p = Path(path)
    for root in sorted(roots, key=len, reverse=True):
        try:
            p.relative_to(root)
            return root
        except ValueError:
            continue
    raise PermissionError('未获应用目录授权')
