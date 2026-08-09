# Publishing Prepende Protocol

GitHub Releases is the only supported distribution channel for Prepende Protocol. The
canonical release record is a repository tag pinned to the verified commit plus an immutable GitHub release in
`stellastoneandsteel/prepende` containing:

- the built wheel;
- the source distribution; and
- `SHA256SUMS` covering those exact artifacts.

Prepende is not published to PyPI. This repository has no PyPI publishing workflow,
environment, trusted-publisher registration, API token, or package claim. A PyPI project
with a similar name is not an authoritative Prepende distribution.

## Release procedure

1. Run the full test suite and build the wheel and source distribution from the intended
   release commit.
2. Install the wheel into a clean environment and rerun the protocol tests from the
   installed package.
3. Compute `SHA256SUMS` over the final wheel and source distribution.
4. Create the release tag from the verified commit. Do not move or replace that tag.
5. Create a GitHub release and upload the two distributions plus `SHA256SUMS`.
6. Download the published assets into a clean directory and verify their checksums before
   announcing the release.

Published release assets are immutable. A correction uses a new version and a new tag; it
never replaces an artifact under an existing release.

## Consumer verification

Download the wheel and `SHA256SUMS` from the same GitHub release, verify the wheel against
the checksum file, and only then install it. Source checkouts and editable installs are for
development, not release distribution.
