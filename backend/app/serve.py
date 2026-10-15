"""web 单进程启动器 —— web 入口绑定 services.conf 登记的监听口（Phase 4 进程终态）。

注册表已收敛为单一 web 端口 8000，故缺省即「一个进程、一个端口」；启动器本身仍按注册表
取端口（去重升序），不改代码即可临时多口调试。

用法:
    python -m backend.app.serve              # 生产/开发：单进程绑定注册表端口（当前 :8000）
    python -m backend.app.serve --reload     # 开发热重载（同端口）
    python -m backend.app.serve --ports 8000 # 显式指定端口（调试）

端口清单缺省读 backend/scripts/services.conf（唯一权威登记）；
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
    """从服务注册表读监听端口（name:module:port → port，去重升序）。

    注册表缺省只有 web:8000 一行；多行同端口（历史别名）也只会绑一次。
    """
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
        description="AI-DataHub web 入口启动器（单进程，端口取 services.conf）")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--ports", default="",
                        help="逗号分隔端口，缺省取 services.conf 登记的端口（当前 :8000）")
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
    print(f"[serve] web 入口监听: {args.host}:{ports}", flush=True)

    config = uvicorn.Config(
        "backend.app.main:app",
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
