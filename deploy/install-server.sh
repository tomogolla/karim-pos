#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Run this installer with sudo." >&2
    exit 2
fi
domain="${1:-}"
if [[ ! "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
    echo "Usage: sudo ./deploy/install-server.sh pos.example.com" >&2
    exit 2
fi
if ! id karimpos >/dev/null 2>&1; then
    echo "The karimpos system user does not exist." >&2
    exit 2
fi

readonly ROOT=/srv/karim-pos
readonly SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

apt-get update
apt-get install -y python3-venv python3-dev libpq-dev curl nginx certbot python3-certbot-nginx
usermod --shell /bin/bash karimpos
usermod --append --groups karimpos www-data
chown karimpos:karimpos "$ROOT"
chmod 0750 "$ROOT"
install -d -o karimpos -g karimpos -m 0750 "$ROOT/releases" "$ROOT/shared"

if [[ ! -f "$ROOT/shared/.env" ]]; then
    install -o karimpos -g karimpos -m 0600 "$ROOT/app/.env.example" "$ROOT/shared/.env"
    sed -i \
        -e "s/pos\.example\.com/$domain/g" \
        "$ROOT/shared/.env"
    echo "Created $ROOT/shared/.env. Fill its empty secrets before the first deployment."
fi

install -m 0644 "$SOURCE_DIR/karim-pos.service" /etc/systemd/system/karim-pos.service
install -m 0644 "$SOURCE_DIR/karim-pos-sync.service" /etc/systemd/system/karim-pos-sync.service
install -m 0644 "$SOURCE_DIR/karim-pos-sync.timer" /etc/systemd/system/karim-pos-sync.timer
if [[ ! -f /etc/nginx/sites-available/karim-pos ]]; then
    sed "s/POS_DOMAIN/$domain/g" "$SOURCE_DIR/nginx-karim-pos.conf" \
        > /etc/nginx/sites-available/karim-pos
else
    echo "Keeping the existing Karim POS nginx file (including any Certbot changes)."
fi
ln -sfn /etc/nginx/sites-available/karim-pos /etc/nginx/sites-enabled/karim-pos

cat > /etc/sudoers.d/karimpos-deploy <<'EOF'
karimpos ALL=(root) NOPASSWD: /usr/bin/systemctl restart karim-pos.service
EOF
chmod 0440 /etc/sudoers.d/karimpos-deploy
visudo -cf /etc/sudoers.d/karimpos-deploy

systemctl daemon-reload
nginx -t
systemctl restart nginx
echo "Server files installed. Configure .env, the CI SSH key, and GitHub secrets next."
