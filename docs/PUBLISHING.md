# Publishing Prepende Protocol

Production package publication uses PyPI trusted publishing. No long-lived PyPI API token
belongs in this repository, a local environment file, or a GitHub secret.

The PyPI publisher registration must match exactly:

- PyPI project: `prepende-protocol`
- GitHub owner: `stellastoneandsteel`
- GitHub repository: `prepende`
- Workflow: `publish-pypi.yml`
- Environment: `pypi`

For the first release, create a pending trusted publisher in the authenticated PyPI account
before dispatching the workflow. The GitHub release must already contain the wheel, sdist,
and `SHA256SUMS`. The workflow downloads those published assets, verifies their exact
digests, and exchanges GitHub's short-lived OIDC identity directly with PyPI.

Dispatch `publish-pypi` with the existing release tag. PyPI versions are immutable: never
rerun against a version that PyPI already accepted, and never replace a GitHub release asset
after publication.
