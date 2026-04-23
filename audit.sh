#!/bin/bash
# Project OUTPOST | DeepWork AILab - Node Auditor v1.2 (2026-04)
set -e
echo -e "\n\e[1;34m[DeepWork AILab]\e[0m Инициализация OUTPOST..."
OS=$(lsb_release -ds 2>/dev/null || uname -o)
echo "- Система: $OS"
echo "- Ядра CPU: $(nproc)"
echo "- RAM: $(free -m | awk '/^Mem:/{print $2}')MB"
echo -e "\n\e[1;32m[ZTNA Device Trust ID]:\e[0m"
MACHINE_ID=$(cat /etc/machine-id 2>/dev/null || echo "LOCAL_DEMO_ID")
NODE_FINGERPRINT=$(echo "$MACHINE_ID-OUTPOST-2026" | sha256sum | awk '{print $1}' | cut -c1-16)
echo "- ID: $NODE_FINGERPRINT"
echo -e "\n\e[1;34m[Аудит завершен]\e[0m"
