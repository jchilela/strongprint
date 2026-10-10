#!/usr/bin/env bash
# Central WireGuard do StrongPrint no servidor (corre no servidor, como root).
# Reexecutável: não altera nada se já estiver configurado.
#
# Rede: 10.77.0.0/16 — servidor 10.77.0.1; kiosk N usa 10.77.N.0/24
# (Pi 10.77.N.1, impressora 10.77.N.2). Interface "wgsp", porta 51820/udp.
# Não toca em nenhuma outra configuração do servidor.
set -euo pipefail

IFACE=wgsp
PORT=51820
CONF=/etc/wireguard/$IFACE.conf
APP_DIR=/opt/strongprint

command -v wg >/dev/null || { apt-get update -q && apt-get install -y -q wireguard-tools; }
umask 077
mkdir -p /etc/wireguard
[[ -f /etc/wireguard/$IFACE.key ]] || wg genkey > /etc/wireguard/$IFACE.key
wg pubkey < /etc/wireguard/$IFACE.key > /etc/wireguard/$IFACE.pub

if [[ ! -f $CONF ]]; then
    cat > "$CONF" <<CONFEOF
# Gerido por /opt/strongprint/deploy/vpn — os [Peer] são acrescentados por server-add-peer.sh
[Interface]
Address = 10.77.0.1/16
ListenPort = $PORT
PostUp = wg set %i private-key /etc/wireguard/$IFACE.key
# Os contentores Docker chegam aos kiosks com o IP do servidor na VPN.
PostUp = iptables -t nat -A POSTROUTING -s 172.16.0.0/12 -o %i -j MASQUERADE
PostDown = iptables -t nat -D POSTROUTING -s 172.16.0.0/12 -o %i -j MASQUERADE
CONFEOF
fi

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
    ufw status | grep -q "$PORT/udp" || ufw allow "$PORT/udp" comment "StrongPrint WireGuard" >/dev/null
fi

systemctl enable --now "wg-quick@$IFACE" >/dev/null 2>&1 || systemctl restart "wg-quick@$IFACE"

# A aplicação precisa da chave pública do servidor para configurar os Pi.
PUB=$(cat /etc/wireguard/$IFACE.pub)
if [[ -f $APP_DIR/.env ]]; then
    if grep -q "^VPN_SERVER_PUBLIC_KEY=" "$APP_DIR/.env"; then
        sed -i "s|^VPN_SERVER_PUBLIC_KEY=.*|VPN_SERVER_PUBLIC_KEY=$PUB|" "$APP_DIR/.env"
    else
        echo "VPN_SERVER_PUBLIC_KEY=$PUB" >> "$APP_DIR/.env"
    fi
fi
echo "WireGuard $IFACE ativo · chave pública $PUB"
