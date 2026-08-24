FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    build-essential \
    cmake \
    ninja-build \
    python3 \
    python3-pip \
    clang \
    llvm \
    gcovr \
    pkg-config \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --break-system-packages \
    'conan>=2,<3' \
    'numpy>=1.26' \
    'tree-sitter==0.25.2' \
    'tree-sitter-python==0.25.0' \
    'tree-sitter-cpp==0.23.4'

WORKDIR /work
