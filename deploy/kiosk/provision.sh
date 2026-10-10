#!/usr/bin/env bash
# Instala (ou reinstala) um Raspberry Pi de kiosk StrongPrint. Corre no computador do admin.
#
#   deploy/kiosk/provision.sh <PI> <utilizador@ip-do-pi> [--printer-lan-ip IP] [--kiosk-user U] [--rotate-token]
#   ex.: deploy/kiosk/provision.sh IMETRO-S01-K01-PI julio@192.168.1.238 --printer-lan-ip 192.168.1.50
#
# Requisitos: acesso SSH por chave ao Pi (ssh-copy-id) e ao servidor; o Pi tem de
# existir no admin (Edge devices) associado à impressora do kiosk.
# Faz: chave WireGuard no Pi → regista-o no servidor (slot na VPN, dados no admin,
# token) → peer no servidor → instala VPN, encaminhamento para a impressora,
# agente e ecrã em modo kiosk → verifica. O token nunca aparece no ecrã.
set -euo pipefail

SERVER=${SERVER:-root@173.212.219.248}
APP_DIR=/opt/strongprint
DEVICE=${1:?nome do Pi, ex. IMETRO-S01-K01-PI}
TARGET=${2:?utilizador@ip do Pi}
shift 2
PRINTER_LAN_IP="" ROTATE=0 KIOSK_USER=""
while (($#)); do
    case $1 in
        --printer-lan-ip) PRINTER_LAN_IP=$2; shift 2 ;;
        --kiosk-user) KIOSK_USER=$2; shift 2 ;;
        --rotate-token) ROTATE=1; shift ;;
        *) echo "opção desconhecida: $1" >&2; exit 1 ;;
    esac
done
PI_USER=${TARGET%@*} PI_HOST=${TARGET#*@}
KIOSK_USER=${KIOSK_USER:-$PI_USER}  # utilizador da sessão gráfica (ex.: pi)
HERE=$(cd "$(dirname "$0")" && pwd)
SSH_PI=(ssh -o BatchMode=yes -o ConnectTimeout=10 "$TARGET")
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT; chmod 700 "$TMP"

echo "==> 1/6 Acesso ao Pi $TARGET"
"${SSH_PI[@]}" 'sudo -n true' || { echo "o utilizador precisa de sudo sem password" >&2; exit 1; }

echo "==> 2/6 Chave WireGuard do Pi"
"${SSH_PI[@]}" 'command -v wg >/dev/null || (sudo apt-get update -q && sudo apt-get install -y -q wireguard-tools) >/dev/null'
PUB=$("${SSH_PI[@]}" 'sudo sh -c "umask 077; [ -f /etc/wireguard/wgsp.key ] || wg genkey > /etc/wireguard/wgsp.key; wg pubkey < /etc/wireguard/wgsp.key"')
HAS_TOKEN=$("${SSH_PI[@]}" 'sudo test -s /etc/strongprint/agent.env && echo yes || echo no')

echo "==> 3/6 Registo no servidor (admin)"
ARGS=(--public-key "$PUB" --lan-ip "$PI_HOST" --ssh-user "$PI_USER")
[[ -n $PRINTER_LAN_IP ]] && ARGS+=(--printer-lan-ip "$PRINTER_LAN_IP")
[[ $HAS_TOKEN == no || $ROTATE == 1 ]] && ARGS+=(--issue-token)
ssh -o BatchMode=yes "$SERVER" "cd $APP_DIR && docker compose -f docker-compose.prod.yml exec -T web \
    python manage.py provision_pi $(printf '%q ' "$DEVICE" "${ARGS[@]}")" | tail -1 > "$TMP/config.json"
python3 - "$TMP/config.json" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
print(f"    kiosk {c['kiosk']} · slot {c['slot']} · Pi {c['pi_vpn_ip']} · impressora {c['printer_vpn_ip']}"
      f" → {c.get('printer_lan_ip') or 'IP local por definir'} · token {'novo' if c.get('token') else 'mantido'}")
PY
SUBNET=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["subnet"])' "$TMP/config.json")
PI_VPN_IP=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["pi_vpn_ip"])' "$TMP/config.json")

echo "==> 4/6 Peer no servidor ($SUBNET)"
ssh -o BatchMode=yes "$SERVER" "bash $APP_DIR/deploy/vpn/server-add-peer.sh $(printf '%q ' "$DEVICE" "$PUB" "$SUBNET")"

echo "==> 5/6 Instalação no Pi"
COPYFILE_DISABLE=1 tar --no-xattrs -C "$HERE/pi" -czf "$TMP/pi.tgz" . 2>/dev/null \
    || COPYFILE_DISABLE=1 tar -C "$HERE/pi" -czf "$TMP/pi.tgz" .
scp -q -o BatchMode=yes "$TMP/pi.tgz" "$TARGET:/tmp/strongprint-pi.tgz"
"${SSH_PI[@]}" "rm -rf /tmp/strongprint-pi && mkdir -p /tmp/strongprint-pi && tar -C /tmp/strongprint-pi -xzf /tmp/strongprint-pi.tgz && rm /tmp/strongprint-pi.tgz"
"${SSH_PI[@]}" "sudo KIOSK_USER=$(printf '%q' "$KIOSK_USER") bash /tmp/strongprint-pi/install.sh; rm -rf /tmp/strongprint-pi" < "$TMP/config.json"

echo "==> 6/6 Verificação"
sleep 5
ssh -o BatchMode=yes "$SERVER" "ping -c 2 -W 3 $PI_VPN_IP >/dev/null && echo '    VPN: servidor chega ao Pi ($PI_VPN_IP)' || echo '    VPN: SEM RESPOSTA de $PI_VPN_IP'"
"${SSH_PI[@]}" 'systemctl is-active strongprint-agent wg-quick@wgsp strongprint-nat | paste -sd " " | sed "s/^/    serviços (agente, vpn, nat): /"'
echo "Concluído. O ecrã abre no próximo arranque da sessão gráfica (ou: sudo reboot)."
