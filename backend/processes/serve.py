"""web 单进程启动器 —— 合并入口在全部契约端口各绑一个 socket（Phase 4 进程终态）。

用法:
    python -m backend.processes.serve              # 生产/开发：单进程绑定 services.conf 全部端口
    python -m backend.processes.serve --reload     # 开发热重载（同为多端口，vite 代理无需改动）
    python -m backend.processes.serve --ports 8001 # 单端口调试

端口清单缺省读 backend/scripts/services.conf（唯一权威登记，对外端口不变）；
PID/日志由 start-all.sh 纳管（pids/web.pid、logs/web.log）。
"""

import argparse
import socket
import sys
from pathlib import Path

import uvicorn
from uvicorn.supervisors import ChangeReload

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SERVICES_CONF = PROJECT_ROOT / "backend" / "scripts" / "services.conf"


def service_ports() -> list:
    """从服务注册表读全部契约端口（name:module:port → port）。"""
    ports = []
    for line in SERVICES_CONF.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        ports.append(int(line.rsplit(":", 1)[1]))
    return sorted(set(ports))


def _bind(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(2048)
    return sock


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AI-DataHub web 合并入口启动器（单进程多端口）")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--ports", default="",
                        help="逗号分隔端口，缺省取 services.conf 全部契约端口")
    parser.add_argument("--reload", action="store_true", help="开发热重载")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    ports = ([int(p) for p in args.ports.split(",") if p.strip()]
             if args.ports else service_ports())

    socks = []
    for port in ports:
        try:
            socks.append(_bind(args.host, port))
        except OSError as e:
            print(f"[serve] 端口 {port} 绑定失败: {e}", file=sys.stderr)
            return 1
    print(f"[serve] web 合并入口监听: {args.host}:{ports}", flush=True)

    config = uvicorn.Config(
        "backend.processes.main:app",
        host=args.host,
        log_level=args.log_level,
        reload=args.reload,
    )
    server = uvicorn.Server(config)
    if args.reload:
        # 与 uvicorn CLI 同构：reload 由父进程监管，socket 传入子进程继续绑定
        ChangeReload(config, target=server.run, sockets=socks).run()
    else:
        server.run(sockets=socks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
