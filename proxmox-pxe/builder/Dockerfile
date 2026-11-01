FROM --platform=linux/amd64 debian:trixie-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl xorriso whois git make gcc libc6-dev binutils perl liblzma-dev \
 && curl -fsSL https://enterprise.proxmox.com/debian/proxmox-archive-keyring-trixie.gpg \
      -o /usr/share/keyrings/proxmox-archive-keyring.gpg \
 && printf 'Types: deb\nURIs: http://download.proxmox.com/debian/pve\nSuites: trixie\nComponents: pve-no-subscription\nSigned-By: /usr/share/keyrings/proxmox-archive-keyring.gpg\n' \
      > /etc/apt/sources.list.d/proxmox.sources \
 && apt-get update \
 && apt-get install -y --no-install-recommends proxmox-auto-install-assistant \
 && rm -rf /var/lib/apt/lists/*
