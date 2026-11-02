#!/usr/bin/env bash
set -Eeuo pipefail

readonly ROOT=/srv/karim-pos
readonly REPOSITORY="$ROOT/app"
readonly RELEASES="$ROOT/releases"
readonly SHARED="$ROOT/shared"
readonly CURRENT="$ROOT/current"
readonly RELEASE_SHA="${1:-}"

if [[ ! "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Usage: $0 <40-character commit SHA>" >&2
    exit 2
fi
if [[ "$(id -un)" != "karimpos" ]]; then
    echo "Run this script as the karimpos user." >&2
    exit 2
fi
if [[ ! -f "$SHARED/.env" ]]; then
    echo "Missing $SHARED/.env; finish the production configuration first." >&2
    exit 2
fi

mkdir -p "$RELEASES"
git -C "$REPOSITORY" cat-file -e "$RELEASE_SHA^{commit}"
git -C "$REPOSITORY" merge-base --is-ancestor "$RELEASE_SHA" origin/prod-hardening

release="$RELEASES/$RELEASE_SHA"
if [[ -e "$release" ]]; then
    git -C "$REPOSITORY" worktree remove --force "$release"
fi
git -C "$REPOSITORY" worktree add --detach "$release" "$RELEASE_SHA"
ln -s "$SHARED/.env" "$release/.env"

python3 -m venv "$release/venv"
"$release/venv/bin/python" -m pip install \
    --disable-pip-version-check --no-input -r "$release/requirements.txt"

cd "$release"
"$release/venv/bin/python" manage.py check --deploy
"$release/venv/bin/python" manage.py migrate --noinput
"$release/venv/bin/python" manage.py collectstatic --noinput --clear

previous=""
if [[ -L "$CURRENT" ]]; then
    previous="$(readlink -f "$CURRENT")"
fi
next_link="$ROOT/.current-$RELEASE_SHA"
ln -s "$release" "$next_link"
mv -Tf "$next_link" "$CURRENT"

rollback() {
    trap - ERR
    echo "Health check failed; restoring the previous application release." >&2
    if [[ -n "$previous" && -d "$previous" ]]; then
        rollback_link="$ROOT/.current-rollback"
        rm -f "$rollback_link"
        ln -s "$previous" "$rollback_link"
        mv -Tf "$rollback_link" "$CURRENT"
        sudo /usr/bin/systemctl restart karim-pos.service
    else
        rm -f "$CURRENT"
    fi
}
trap rollback ERR

sudo /usr/bin/systemctl restart karim-pos.service
for attempt in {1..15}; do
    if curl --fail --silent --show-error \
        -H "Host: $("$release/venv/bin/python" -c 'from django.conf import settings; print(settings.ALLOWED_HOSTS[0])')" \
        "http://127.0.0.1:8765/healthz" >/dev/null; then
        trap - ERR
        echo "Deployed $RELEASE_SHA successfully."
        break
    fi
    if [[ "$attempt" -eq 15 ]]; then
        false
    fi
    sleep 2
done

# Keep the five newest complete releases. Validate every path before removal.
while IFS= read -r stale; do
    [[ "$stale" == "$RELEASES/"* ]] || continue
    [[ "$stale" != "$release" ]] || continue
    git -C "$REPOSITORY" worktree remove --force "$stale"
done < <(find "$RELEASES" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
    | sort -nr | tail -n +6 | cut -d' ' -f2-)
git -C "$REPOSITORY" worktree prune
