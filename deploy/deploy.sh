#!/usr/bin/env bash
# Deploy do StrongPrint para produção.
#
#   deploy/deploy.sh            # envia o commit atual (HEAD) e reinicia
#   deploy/deploy.sh --bootstrap  # primeira vez: segredos, nginx e HTTPS
#
# Requer acesso SSH por chave a $SERVER. Não usa o GitHub no servidor: o código
# vai por `git archive`, por isso só segue o que está commitado.
set -euo pipefail

SERVER="${SERVER:-root@173.212.219.248}"
APP_DIR="/opt/strongprint"
DOMAIN="strongprint.it.ao"
# Todos os nomes servidos (certificado único). O nginx do servidor só é tocado no
# site "$DOMAIN" — os outros sites (OpenClaw, etc.) nunca são alterados.
DOMAINS="${DOMAINS:-$DOMAIN www.$DOMAIN client.$DOMAIN administ.$DOMAIN api.$DOMAIN app.$DOMAIN}"
CERT_EMAIL="${CERT_EMAIL:-juliochilela@hotmail.com}"

cd "$(git rev-parse --show-toplevel)"

if [[ -n "$(git status --porcelain)" ]]; then
    echo "Há alterações por commitar; o deploy envia apenas o HEAD." >&2
fi

REV="$(git rev-parse --short HEAD)"
echo "==> A enviar $REV para $SERVER:$APP_DIR"
ssh "$SERVER" "mkdir -p $APP_DIR"
git archive --format=tar HEAD | ssh "$SERVER" "tar -x -C $APP_DIR && echo $REV > $APP_DIR/REVISION"

BOOTSTRAP=0
[[ "${1:-}" == "--bootstrap" ]] && BOOTSTRAP=1

if [[ $BOOTSTRAP == 1 ]]; then
    echo "==> Bootstrap: Docker e segredos"
    ssh "$SERVER" APP_DIR="$APP_DIR" DOMAIN="$DOMAIN" CERT_EMAIL="$CERT_EMAIL" bash -s <<'REMOTE'
set -euo pipefail
cd "$APP_DIR"
if ! docker compose version >/dev/null 2>&1; then
    apt-get update -q && apt-get install -y -q docker.io docker-compose-v2
    systemctl enable --now docker
fi
rand() { python3 -c "import secrets; print(secrets.token_urlsafe($1))"; }

if [[ ! -f .env ]]; then
    DB_PASS="$(rand 24)"; S3_KEY="sp$(rand 9)"; S3_SECRET="$(rand 32)"
    cat > .env.db <<EOF
POSTGRES_DB=strongprint
POSTGRES_USER=strongprint
POSTGRES_PASSWORD=$DB_PASS
EOF
    cat > .env <<EOF
DJANGO_SETTINGS_MODULE=config.settings.prod
DJANGO_SECRET_KEY=$(rand 50)
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=$DOMAIN,www.$DOMAIN,127.0.0.1,localhost
DJANGO_CSRF_TRUSTED_ORIGINS=https://$DOMAIN,https://www.$DOMAIN
DATABASE_URL=postgres://strongprint:$DB_PASS@db:5432/strongprint
REDIS_URL=redis://redis:6379/0
S3_ENDPOINT_URL=http://s3:8333
S3_PUBLIC_ENDPOINT_URL=https://$DOMAIN
S3_ACCESS_KEY_ID=$S3_KEY
S3_SECRET_ACCESS_KEY=$S3_SECRET
S3_BUCKET=strongprint-jobs
S3_REGION=us-east-1
PRINTER_GATEWAY=fake
JOB_SESSION_TIMEOUT_MINUTES=10
FILE_RETENTION_HOURS=24
EOF
    cat > s3.json <<EOF
{"identities":[{"name":"app","credentials":[{"accessKey":"$S3_KEY","secretKey":"$S3_SECRET"}],
  "actions":["Admin","Read","Write","List","Tagging"]}]}
EOF
    echo "Segredos gerados em $APP_DIR (.env, .env.db, s3.json)."
fi
# A pasta só é acessível ao root; o s3.json tem de ser legível pelo utilizador
# do contentor SeaweedFS (não corre como root).
chmod 700 "$APP_DIR"
chmod 600 .env .env.db
chmod 644 s3.json
REMOTE
fi

echo "==> Build e arranque"
ssh "$SERVER" "cd $APP_DIR && docker compose -f docker-compose.prod.yml up -d --build --remove-orphans && docker image prune -f >/dev/null"

echo "==> Verificação"
ssh "$SERVER" "for i in \$(seq 1 30); do curl -sf http://127.0.0.1:8100/healthz && exit 0; sleep 2; done; \
    docker compose -f $APP_DIR/docker-compose.prod.yml logs --tail 40 web; exit 1"
echo

SERVER_IP="${SERVER#*@}"
if [[ $BOOTSTRAP == 1 ]] && ! dig +short @1.1.1.1 "$DOMAIN" A | grep -qx "$SERVER_IP"; then
    echo "==> DNS de $DOMAIN ainda não aponta para $SERVER_IP: nginx/HTTPS adiados."
    echo "    Volte a correr 'deploy/deploy.sh --bootstrap' quando o DNS estiver ativo."
elif [[ $BOOTSTRAP == 1 ]]; then
    # Só nomes que apontam para este servidor: um nome errado faria falhar o certificado todo.
    READY=""
    for d in $DOMAINS; do
        if dig +short @1.1.1.1 "$d" A | grep -qx "$SERVER_IP"; then READY="$READY $d"; else
            echo "    (ignorado: $d não aponta para $SERVER_IP — ex.: proxy Cloudflare ligado)"; fi
    done
    DOMAINS="${READY# }"
    echo "==> Bootstrap: nginx e HTTPS para: $DOMAINS"
    ssh "$SERVER" DOMAIN="$DOMAIN" DOMAINS="$DOMAINS" CERT_EMAIL="$CERT_EMAIL" APP_DIR="$APP_DIR" bash -s <<'REMOTE'
set -euo pipefail
cp "$APP_DIR/deploy/nginx-strongprint.it.ao.conf" "/etc/nginx/sites-available/$DOMAIN"
ln -sf "/etc/nginx/sites-available/$DOMAIN" "/etc/nginx/sites-enabled/$DOMAIN"
nginx -t
systemctl reload nginx
args=()
for d in $DOMAINS; do args+=(-d "$d"); done
certbot --nginx --non-interactive --agree-tos --redirect --expand --cert-name "$DOMAIN" \
    -m "$CERT_EMAIL" "${args[@]}"
REMOTE
fi

echo "Deploy de $REV concluído: https://$DOMAIN/admin/"
