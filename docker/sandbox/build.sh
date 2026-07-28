#!/bin/bash
# Build sandbox Python image
# Usage: ./build.sh [tag]

set -e

TAG="${1:-sandbox-python:3.10}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Building sandbox image: $TAG"
docker build -t "$TAG" "$SCRIPT_DIR"

echo "Image built successfully: $TAG"
docker images "$TAG" --format "Repository: {{.Repository}}, Tag: {{.Tag}}, Size: {{.Size}}"
