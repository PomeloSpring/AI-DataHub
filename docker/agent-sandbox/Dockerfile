# python_runtime 运行时环境 — 本地会话沙箱（SessionToolSandbox）镜像
# ─────────────────────────────────────────────────────────────
# 运行期容器以 --network=none / --read-only 执行，无法 pip 安装，
# 故所有基础包在构建期（有网）固化到系统 site-packages；
# 入口用 `python -I`（isolated）只忽略 user-site 与 PYTHON* 环境变量，
# 系统 site-packages 仍可正常 import，因此预装包在沙箱内可用。
# 基础镜像用本地已有的 3.11-slim（原 3.10-slim 在国内网络拉取超时；requirements 版本矩阵兼容 3.11，scipy 1.10.1 不支持 3.12 故不用 3.12-slim）
FROM python:3.11-slim

# 构建容器是干净镜像，不继承宿主机 /etc/pip.conf；显式指定国内镜像源，
# 否则默认 pypi.org 在当前网络下极慢且易超时（表现为 "versions: none"）。
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    PIP_TIMEOUT=120 \
    PIP_RETRIES=10

# 构建期预装基础科学计算 / 表格 / 可视化包（清单见 requirements.txt）
COPY docker/agent-sandbox/requirements.txt /tmp/python-runtime-requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r /tmp/python-runtime-requirements.txt \
 && rm -f /tmp/python-runtime-requirements.txt

COPY backend/modules/mind/execution/sandbox_worker.py /opt/agent/sandbox_worker.py
# 沙箱以 uid 65534 运行无家目录，固定 matplotlib 缓存到可写 tmpfs，避免导入告警/变慢
ENV MPLCONFIGDIR=/tmp/matplotlib
USER 65534:65534
WORKDIR /workspace
ENTRYPOINT ["python", "-I", "/opt/agent/sandbox_worker.py"]
