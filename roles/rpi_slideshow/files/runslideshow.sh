#!/bin/bash
set -euo pipefail

SLIDE_SECONDS=10
SLIDE_FOLDER="/home/pi/slideshow"
pushd "${SLIDE_FOLDER}"
while true; do
  feh \
    --auto-zoom \
    --fullscreen \
    --slideshow-delay ${SLIDE_SECONDS} \
    --hide-pointer \
    --auto-rotate
done
popd
