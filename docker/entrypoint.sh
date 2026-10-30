#!/bin/sh
# First start: create the config and the starter vault. An existing config is never touched.
set -e
DATA="${KOKPIT_DATA:-/data}"
CONF="${KOKPIT_CONFIG:-$DATA/kokpit.toml}"
if [ ! -f "$CONF" ]; then
    echo "kokpit: first start, writing $CONF and the starter vault"
    kokpit --config "$CONF" init --vault "$DATA/vault" --db "$DATA/kokpit.db"
fi
exec "$@"
