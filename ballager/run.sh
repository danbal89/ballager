#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -e

export BALLAGER_DATA=/data
export BALLAGER_SHARE=/share
export BALLAGER_PORT=8099
export BALLAGER_LOGLEVEL="$(bashio::config 'log_level' 'info')"
if bashio::config.true 'direct_access'; then
  export BALLAGER_DIRECT_ACCESS=1
  bashio::log.warning "Direktzugriff über Port 8099 ist aktiv – ohne Anmeldung! Nur im vertrauenswürdigen Heimnetz verwenden."
fi

bashio::log.info "BALLager startet …"
cd /opt/ballager
exec python3 -m ballager
