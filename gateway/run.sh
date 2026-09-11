#!/usr/bin/env bash
# One-shot Gateway installer for Linux/macOS.
# Usage: ./run.sh <platform-url> <registration-code> [gateway-name]
# Example: ./run.sh https://your-platform.example.com/api/v1 AT7F-9K2D-4B6M "Finance DB Gateway"
set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "Usage: ./run.sh <platform-url> <registration-code> [gateway-name]"
    echo "Get the platform URL and registration code from the Gateways screen."
    exit 1
fi

PLATFORM_URL="$1"
REG_CODE="$2"
GATEWAY_NAME="${3:-}"

cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
echo "Installing dependencies..."
pip install -q -r requirements.txt

if [[ -f .gateway_identity.json ]]; then
    echo "Already registered (.gateway_identity.json exists) — skipping registration."
else
    echo "Registering with the platform..."
    if [[ -z "$GATEWAY_NAME" ]]; then
        python -m gateway.register --url "$PLATFORM_URL" --code "$REG_CODE"
    else
        python -m gateway.register --url "$PLATFORM_URL" --code "$REG_CODE" --name "$GATEWAY_NAME"
    fi
fi

if [[ ! -f config.yaml ]]; then
    cp config.example.yaml config.yaml
    echo
    echo "Created config.yaml — edit it now: set your database connection(s) and"
    echo "the connection_id shown on the platform's Data Sources screen."
    echo
fi

echo
echo "Setup complete. To run the Gateway:"
echo "  source .venv/bin/activate"
echo "  python -m gateway.main --config config.yaml --loop"
echo "For production, schedule the one-pass form (no --loop) via cron/systemd instead."
