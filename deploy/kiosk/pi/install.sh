#!/usr/bin/env bash
# Instalação StrongPrint num Raspberry Pi (corre no Pi, como root, via provision.sh).
# Reexecutável: reinstala/atualiza sem duplicar nada.
#
#   sudo KIOSK_USER=<utilizador do ambiente gráfico> SCREEN=tablet|pi bash install.sh < config.json
#
# SCREEN=tablet (predefinição): o ecrã é um tablet Android à parte; o Pi não abre browser.
# SCREEN=pi: monitor ligado ao Pi, que abre o ecrã em Chromium modo kiosk.
#
# config.json (gerado pelo servidor): pi_vpn_ip, printer_vpn_ip, printer_lan_ip,
# server_vpn_ip, server_public_key, endpoint, api_url, screen_url e (opcional) token.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
KIOSK_USER=${KIOSK_USER:-${SUDO_USER:-pi}}
PRINTER_IFACE=${PRINTER_IFACE:-}
SCREEN=${SCREEN:-tablet}
CONFIG=$(cat)
get() { python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get(sys.argv[2]) or "")' "$CONFIG" "$1"; }

PI_VPN_IP=$(get pi_vpn_ip); PRINTER_VPN_IP=$(get printer_vpn_ip); PRINTER_LAN_IP=$(get printer_lan_ip)
SERVER_VPN_IP=$(get server_vpn_ip); SERVER_PUB=$(get server_public_key); ENDPOINT=$(get endpoint)
API_URL=$(get api_url); SCREEN_URL=$(get screen_url); TOKEN=$(get token)
[[ -n $PI_VPN_IP && -n $SERVER_PUB && -n $ENDPOINT && -n $API_URL && -n $SCREEN_URL ]] || {
    echo "configuração incompleta" >&2; exit 1; }

echo "==> Pacotes"
export DEBIAN_FRONTEND=noninteractive
need=()
for pkg in wireguard-tools iptables python3; do dpkg -s "$pkg" >/dev/null 2>&1 || need+=("$pkg"); done
if [[ $SCREEN == pi ]]; then
    command -v chromium >/dev/null || command -v chromium-browser >/dev/null || need+=(chromium)
fi
if ((${#need[@]})); then apt-get update -q && apt-get install -y -q "${need[@]}"; fi

echo "==> Configuração"
install -d -m 755 /etc/strongprint
if [[ -f /etc/strongprint/agent.env ]]; then
    [[ -n $TOKEN ]] || TOKEN=$(sed -n 's/^DEVICE_TOKEN=//p' /etc/strongprint/agent.env)
    [[ -n $PRINTER_IFACE ]] || PRINTER_IFACE=$(sed -n 's/^PRINTER_IFACE=//p' /etc/strongprint/agent.env)
fi
[[ -n $TOKEN ]] || { echo "falta o token do dispositivo" >&2; exit 1; }
umask 077
cat > /etc/strongprint/agent.env <<ENV
API_URL=$API_URL
SCREEN_URL=$SCREEN_URL
DEVICE_TOKEN=$TOKEN
VPN_IP=$PI_VPN_IP
PRINTER_VPN_IP=$PRINTER_VPN_IP
PRINTER_LAN_IP=$PRINTER_LAN_IP
PRINTER_IFACE=$PRINTER_IFACE
ENV
# O ecrã (utilizador do ambiente gráfico) só lê o endereço e o token.
printf 'SCREEN_URL=%s\nDEVICE_TOKEN=%s\n' "$SCREEN_URL" "$TOKEN" > /etc/strongprint/screen.env
chown "root:$KIOSK_USER" /etc/strongprint/screen.env && chmod 640 /etc/strongprint/screen.env
umask 022

echo "==> VPN WireGuard ($PI_VPN_IP)"
[[ -f /etc/wireguard/wgsp.key ]] || { umask 077; wg genkey > /etc/wireguard/wgsp.key; umask 022; }
umask 077
cat > /etc/wireguard/wgsp.conf <<WG
# Gerido pela StrongPrint (deploy/kiosk/pi/install.sh)
[Interface]
Address = $PI_VPN_IP/32
PostUp = wg set %i private-key /etc/wireguard/wgsp.key
# Reaplicar o keepalive depois da chave faz o túnel ligar-se logo, sem esperar por tráfego.
PostUp = wg set %i peer $SERVER_PUB persistent-keepalive 25

[Peer]
PublicKey = $SERVER_PUB
Endpoint = $ENDPOINT
AllowedIPs = $SERVER_VPN_IP/32
PersistentKeepalive = 25
WG
umask 022
systemctl enable "wg-quick@wgsp" >/dev/null 2>&1
systemctl restart "wg-quick@wgsp"

echo "==> Encaminhamento VPN → impressora ($PRINTER_VPN_IP → ${PRINTER_LAN_IP:-por definir})"
install -m 755 "$HERE/strongprint-nat" /usr/local/sbin/strongprint-nat
install -m 644 "$HERE/strongprint-nat.service" /etc/systemd/system/strongprint-nat.service

echo "==> Agente (heartbeat + pen USB)"
install -d -m 755 /opt/strongprint-agent
install -m 755 "$HERE/agent.py" /opt/strongprint-agent/agent.py
install -m 644 "$HERE/strongprint-agent.service" /etc/systemd/system/strongprint-agent.service

HOME_DIR=$(getent passwd "$KIOSK_USER" | cut -d: -f6)
if [[ $SCREEN == pi ]]; then
    echo "==> Ecrã em modo kiosk (utilizador $KIOSK_USER)"
    install -m 755 "$HERE/strongprint-kiosk" /usr/local/bin/strongprint-kiosk
    # Um só mecanismo de arranque: labwc (Raspberry Pi OS Bookworm) ou autostart XDG (X11/LXDE).
    if [[ -d $HOME_DIR/.config/labwc || -f /etc/xdg/labwc/autostart ]]; then
        rm -f "$HOME_DIR/.config/autostart/strongprint-kiosk.desktop"
        install -d -o "$KIOSK_USER" -g "$KIOSK_USER" "$HOME_DIR/.config/labwc"
        touch "$HOME_DIR/.config/labwc/autostart"
        grep -q strongprint-kiosk "$HOME_DIR/.config/labwc/autostart" || \
            echo "/usr/local/bin/strongprint-kiosk &" >> "$HOME_DIR/.config/labwc/autostart"
        chown "$KIOSK_USER:$KIOSK_USER" "$HOME_DIR/.config/labwc/autostart"
    else
        install -d -o "$KIOSK_USER" -g "$KIOSK_USER" "$HOME_DIR/.config/autostart"
        install -m 644 -o "$KIOSK_USER" -g "$KIOSK_USER" "$HERE/strongprint-kiosk.desktop" \
            "$HOME_DIR/.config/autostart/strongprint-kiosk.desktop"
    fi
    # Ecrã sempre ligado.
    command -v raspi-config >/dev/null && raspi-config nonint do_blanking 1 || true

else
    echo "==> Ecrã: tablet à parte (o Pi não abre browser)"
    rm -f "$HOME_DIR/.config/autostart/strongprint-kiosk.desktop"
    [[ -f $HOME_DIR/.config/labwc/autostart ]] && sed -i '/strongprint-kiosk/d' "$HOME_DIR/.config/labwc/autostart"
    pkill -f /usr/local/bin/strongprint-kiosk >/dev/null 2>&1 || true
    pkill -u "$KIOSK_USER" -f 'chromium.*--kiosk' >/dev/null 2>&1 || true
fi

systemctl daemon-reload
systemctl enable --now strongprint-nat.service strongprint-agent.service >/dev/null 2>&1
systemctl restart strongprint-nat.service strongprint-agent.service
echo "==> Instalação concluída"
