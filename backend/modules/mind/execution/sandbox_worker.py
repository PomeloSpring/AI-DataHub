"""会话工具容器入口；仅依赖 Python 标准库，不载入平台配置或凭据。"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path("/workspace")
MAX_TEXT = 100_000


def resolve(path="."):
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    p = p.resolve()
    if p != ROOT and ROOT not in p.parents:
        raise PermissionError("路径超出当前会话目录")
    return p


def open_beneath(path, flags, create_dirs=False):
    """逐层通过目录描述符打开，拒绝竞态替换的符号链接和 magic link。"""
    parts = path.relative_to(ROOT).parts
    if not parts:
        raise ValueError("工具需要文件路径")
    directory = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            if create_dirs:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=directory)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        return os.open(parts[-1], flags | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    finally:
        os.close(directory)


def execute(name, args):
    if name == "bash":
        command = args.get("command", "")
        if not isinstance(command, str) or not command.strip() or len(command) > MAX_TEXT:
            raise ValueError("命令无效")
        timeout = min(max(int(args.get("timeout", 60)), 1), 120)
        result = subprocess.run(["/bin/sh", "-c", command], cwd=ROOT, capture_output=True,
                                text=True, timeout=timeout, env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp"})
        return {"stdout": result.stdout[:MAX_TEXT], "stderr": result.stderr[:MAX_TEXT],
                "exit_code": result.returncode, "truncated": len(result.stdout) > MAX_TEXT or len(result.stderr) > MAX_TEXT}
    p = resolve(args.get("path", "."))
    if name == "read":
        with os.fdopen(open_beneath(p, os.O_RDONLY), "r", encoding="utf-8", errors="replace") as f:
            data = f.read(MAX_TEXT + 1)
        return {"text": data[:MAX_TEXT], "truncated": len(data) > MAX_TEXT}
    if name in ("write", "edit"):
        if name == "edit":
            with os.fdopen(open_beneath(p, os.O_RDONLY), "r", encoding="utf-8") as f:
                text = f.read(200_001)
            if len(text) > 200_000:
                raise ValueError("文件超过编辑大小限制")
            old = args.get("old_string", "")
            if not old or text.count(old) != 1:
                raise ValueError("待替换内容必须唯一匹配")
            text = text.replace(old, args.get("new_string", ""), 1)
        else:
            text = args.get("content", "")
        if not isinstance(text, str) or len(text.encode()) > 200_000:
            raise ValueError("文件超过写入大小限制")
        fd = open_beneath(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, create_dirs=True)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        return {"written": True}
    if name in ("glob", "grep"):
        pattern = args.get("glob") or (args.get("pattern") if name == "glob" else "**/*") or "*"
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise PermissionError("检索模式不能超出当前会话")
        regex = re.compile(args.get("pattern", "")) if name == "grep" else None
        out = []
        for candidate in p.glob(pattern):
            target = resolve(str(candidate))
            if not target.is_file():
                continue
            if regex is None:
                out.append(str(candidate.relative_to(ROOT)))
            elif target.stat().st_size <= 200_000:
                with os.fdopen(open_beneath(target, os.O_RDONLY), "r", encoding="utf-8", errors="replace") as f:
                    for number, line in enumerate(f, 1):
                        if regex.search(line):
                            out.append(f"{candidate.relative_to(ROOT)}:{number}: {line[:500].rstrip()}")
                            if len(out) >= 100:
                                break
            if len(out) >= 100:
                break
        return {"matches": out, "limited": len(out) >= 100}
    raise PermissionError("沙箱不支持该工具")


def _drop_privileges():
    """bwrap 以 root 完成挂载/chdir 后，工具执行前降权到会话目录属主（对齐 docker --user 65534）。

    仅当 env 指定 ADH_DROP_UID/GID 且当前为 root 时生效（Docker 后端不设这些 env → 无影响）。
    降权后无法再提权，工具代码以非特权 uid 运行。
    """
    if os.geteuid() != 0:
        return
    uid, gid = os.environ.get("ADH_DROP_UID"), os.environ.get("ADH_DROP_GID")
    if uid is None or gid is None:
        return
    try:
        os.setgroups([])
        os.setgid(int(gid))
        os.setuid(int(uid))
    except (ValueError, OSError):
        pass


def _apply_rlimits():
    """按 env 施加资源上限（bwrap 后端用；Docker 后端不设这些 env → 全部跳过，行为不变）。

    RLIMIT_CPU 限 CPU 秒、RLIMIT_FSIZE 限写文件字节、RLIMIT_AS 限虚拟内存（仅在显式配置时，
    避免误伤 pandas/numpy 的大 VSZ）。真·每树内存/进程硬帽需 cgroup，由 Docker 后端承担。
    """
    import resource

    def _int(name):
        try:
            return int(os.environ.get(name, "") or 0)
        except ValueError:
            return 0
    cpu = _int("ADH_RLIMIT_CPU_S")
    if cpu > 0:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 5))
    fsize = _int("ADH_RLIMIT_FSIZE_MB")
    if fsize > 0:
        limit = fsize * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    mem = _int("ADH_RLIMIT_AS_MB")
    if mem > 0:
        limit = mem * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        except (ValueError, OSError):
            pass


if __name__ == "__main__":
    _drop_privileges()
    _apply_rlimits()
    try:
        payload = json.loads(sys.stdin.read(500_001))
        result = execute(payload["name"], payload["args"])
        print(json.dumps({"result": result}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        sys.exit(1)
