# Mihomo GHCR mirror

Unofficial, digest-preserving mirror of the newest stable Mihomo container release.

## Pull

```bash
docker pull ghcr.io/k2kib4h2ysevdoq61a4i73i01bkw7hkp/mihomo:vX.Y.Z
```

The workflow discovers all `vX.Y.Z` tags and mirrors only the numerically newest one. It does not follow Docker's `latest` tag, `Alpha`, `Meta`, or `pr-*` tags.

## How it updates

A GitHub Actions workflow runs once per day and:

1. Reads Docker Hub's tag API.
2. Selects the numerically newest `vX.Y.Z` tag.
3. Resolves it to a source manifest-list digest.
4. Copies only that release to GHCR with `skopeo --all --preserve-digests` when the digest changes.
5. Verifies the destination digest and expected platforms.
6. Commits the verified source/destination mapping to `state/mirror.json`.

The workflow also refreshes `maintenance/heartbeat.txt` after a successful check when the repository has been inactive for the configured interval. It does not create empty commits after every unchanged check.

## Upstream

- Source image: `docker.io/metacubex/mihomo`
- Source project: <https://github.com/MetaCubeX/mihomo>
- Source tags: <https://hub.docker.com/r/metacubex/mihomo/tags>

This repository is not affiliated with or endorsed by MetaCubeX. See [UPSTREAM.md](UPSTREAM.md) for the exact mirror policy and metadata caveat.
