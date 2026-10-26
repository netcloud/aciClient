# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-10-26

### Added

- Typed, thread-safe `AciClient` for direct APIC and Nexus Dashboard gateway
  access, with password and certificate authentication.
- Structured queries, APIC filter helpers, generic response models, explicit
  pagination, and connection-pool limits.
- Subscription handles, snapshot job monitoring, and `session_token` for
  external WebSocket integrations.
- Injectable HTTP transports and deterministic pytest helpers, with expanded
  examples and offline/live APIC testing guides.
- A runnable README example testing an automation function with a reusable
  pytest fixture.

### Changed

- Requires Python 3.12 or newer.
- Modern clients verify TLS by default, use `cryptography` for certificate
  signing, and exclude secure fields from snapshots by default.
- Authentication sessions and response decoding now live in dedicated internal
  modules, keeping the public client focused on HTTP routing and operations.
- Snapshot waits and pagination deadlines include authentication and HTTP
  requests, cap phase timeouts, and reject late results.

### Fixed

- Require patched certifi, urllib3, and cryptography releases in installed
  distributions, with a compatible pyOpenSSL minimum.
- Redact APIC shared keys and nested credential fields in test request history,
  including credential queries and Nexus Dashboard proxy paths.
- Raise `ResponseDecodeError` for malformed managed objects, authentication,
  login-domain, and subscription responses.
- Include guides and examples in source distributions and use repository links
  for documentation rendered on PyPI.

### Deprecated

- `ACI` and `ACICert` emit `LegacyAPIWarning`; their existing interfaces remain
  compatible in 2.0.

## [1.7] - 2025-12

### Changed

- Updated project dependencies.

## [1.6] - 2024-06

### Changed

- Improved subscription support.

## [1.5] - 2023-01

### Changed

- Updated project dependencies.

## [1.3] - 2021-06

### Added

- Added snapshot creation.

## [1.0.0] - 2020-11

### Changed

- Prepared the project for open-source distribution.

## [0.x] - 2020-11

### Added

- Initial release.
