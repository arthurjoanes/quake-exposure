#!/bin/sh
set -eu
curl --fail --silent --show-error -X PUT \
  -H 'Content-Type: application/json' \
  --data-binary @/config/portfolio-connector.json \
  http://connect:8083/connectors/portfolio/config
