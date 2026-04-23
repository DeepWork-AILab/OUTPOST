#!/bin/bash
# Project OUTPOST | DeepWork AILab - Node Auditor v1.2 (2026-04)
# Целевая ОС: Ubuntu 24.04 LTS

set -e

echo -e "\n\e[1;34m[DeepWork AILab]\e[0m Инициализация аудита узла OUTPOST..."
sleep 1

# 1. Проверка системных ресурсов
echo -e "\n\e[1;32m[1/3] Анализ ресурсов:\e[0m"
OS=$(lsb_release -ds)
CORE_COUNT=$(nproc)
RAM_TOTAL=$(free -m | awk '/^Mem:/{print $2}')
DISK_TYPE=$(lsblk -d -o ROTA | sed -n '2p' | xargs) # 0 = SSD/NVMe, 1 = HDD

echo "- Система: $OS"
echo "- Ядра CPU: $CORE_COUNT"
echo "- RAM: ${RAM_TOTAL}MB"

if [[ "$DISK_TYPE" == "0" ]]; then echo "- Диск: Высокоскоростной (SSD/NVMe)"; else echo -e "- \e[1;31mДиск: HDD (ВНИМАНИЕ: Возможны задержки I/O)\e[0m"; fi

# 2. Проверка ядра на поддержку протоколов
echo -e "\n\e[1;32m[2/3] Сетевой стек (DPI-Resilience):\e[0m"
# Проверка наличия модулей WireGuard (база для AmneziaWG)
if lsmod | grep -q wireguard || modinfo wireguard >/dev/null 2>&1; then
    echo "- Модуль WireGuard: Доступен"
else
    echo "- Модуль WireGuard: Требуется установка заголовков ядра"
fi

# Проверка BBR (алгоритм ускорения трафика)
BBR_STATUS=$(sysctl net.ipv4.tcp_congestion_control | awk '{print $3}')
echo "- Алгоритм TCP: $BBR_STATUS"
if [[ "$BBR_STATUS" != "bbr" ]]; then echo "  \e[1;33m[!] Рекомендуется включить BBR для VLESS-Reality\e[0m"; fi

# 3. Проверка портов
echo -e "\n\e[1;32m[3/3] Доступность стандартных портов:\e[0m"
for PORT in 443 4500 4433; do
    if ss -tuln | grep -q ":$PORT "; then
        echo -e "- Порт $PORT: \e[1;31mЗАНЯТ\e[0m"
    else
        echo "- Порт $PORT: Свободен"
    fi
done

echo -e "\n\e[1;34m[Аудит завершен]\e[0m Узел готов к деплою логики FAILOVER."
