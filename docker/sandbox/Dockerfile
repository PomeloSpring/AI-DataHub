# Sandbox Python Image — Minimal isolated execution environment
# Build: docker build -t sandbox-python:3.10 .
#
# Design principles:
# - Minimal size (~100MB)
# - Non-root user
# - No system tools (no apt, no curl, no wget)
# - Only Python standard library
# - Dependencies installed dynamically at runtime via pip

FROM python:3.10-slim

# Remove package manager and unnecessary tools for security
RUN apt-get purge -y --auto-remove apt-utils \
    && rm -rf /var/lib/apt/lists/* /usr/bin/apt* /usr/bin/dpkg* \
    && rm -rf /usr/local/bin/pip*  # We'll use python -m pip

# Create non-root user
RUN useradd -m -s /bin/bash sandbox \
    && mkdir -p /work /tmp \
    && chown sandbox:sandbox /work /tmp

# Set working directory
WORKDIR /work

# Switch to non-root user
USER sandbox

# Default command
CMD ["python"]
