#!/usr/bin/env bash
# Acrescenta (ou substitui) um kiosk na VPN. Corre no servidor, como root.
#   server-add-peer.sh <nome> <chave pública> <subrede, ex.: 10.77.3.0/24>
set -euo pipefail
IFACE=wgsp
CONF=/etc/wireguard/$IFACE.conf
NAME=$1 PUB=$2 SUBNET=$3

[[ $SUBNET =~ ^10\.77\.([0-9]{1,3})\.0/24$ ]] || { echo "subrede inválida: $SUBNET" >&2; exit 1; }

# Remove o bloco anterior deste kiosk (reinstalação) e qualquer peer com a mesma subrede.
python3 - "$CONF" "$NAME" "$SUBNET" <<'PY'
import re, sys
path, name, subnet = sys.argv[1:]
text = open(path).read()
blocks = re.split(r"(?m)^(?=# kiosk: |\[Peer\])", text)
keep = [b for b in blocks if not (f"# kiosk: {name}\n" in b or f"AllowedIPs = {subnet}" in b)]
open(path, "w").write("".join(keep).rstrip("\n") + "\n")
PY

for old in $(wg show "$IFACE" allowed-ips | awk -v s="$SUBNET" '$2==s {print $1}'); do
    wg set "$IFACE" peer "$old" remove
done

cat >> "$CONF" <<PEER

# kiosk: $NAME
[Peer]
PublicKey = $PUB
AllowedIPs = $SUBNET
PEER

wg set "$IFACE" peer "$PUB" allowed-ips "$SUBNET"
ip route replace "$SUBNET" dev "$IFACE"
echo "peer $NAME ($SUBNET) ativo"
