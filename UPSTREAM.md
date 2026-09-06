# Upstream and mirror policy

This repository mirrors selected container tags from:

- Source: `docker.io/metacubex/mihomo`
- Destination: `ghcr.io/k2kib4h2ysevdoq61a4i73i01bkw7hkp/mihomo`

## Tags

The workflow discovers stable semantic-version tags matching `vX.Y.Z` and synchronizes only the numerically newest one. Docker `latest`, `Alpha`, `Meta`, and `pr-*` tags are intentionally excluded.

The workflow resolves the newest `vX.Y.Z` tag to an immutable manifest-list digest and copies that multi-architecture image without rebuilding it. It verifies the destination digest and platform set before recording the mapping in `state/mirror.json`.

The destination retains the currently selected newest `vX.Y.Z` tag. The Docker `latest` tag and older releases are not synchronized by this repository.

## Attribution and metadata

This is an unofficial registry mirror. The image is copied from the upstream registry without source checkout or compilation. Upstream image metadata is preserved as-is; the labels in a container image should not be treated as a substitute for the upstream project's current licensing and attribution documents.

Upstream project: <https://github.com/MetaCubeX/mihomo>
Upstream container: <https://hub.docker.com/r/metacubex/mihomo>
