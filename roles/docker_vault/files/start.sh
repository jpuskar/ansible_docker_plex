#!/bin/bash
set -euo pipefail

vault \
  server \
    -config=/vault.json
