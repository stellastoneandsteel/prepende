# Prepende brain runtime

This directory is the product-neutral Prepende brain runtime. In the public
repository it lives under `brain/`; the authoritative Prepende Protocol
remains at the repository root.

Run repository brain operations with:

```sh
./bin/prepende --help
```

The runtime is local-first and default-deny. It contains no owner vault,
runtime database, tenant corpus, deployment state, credentials, or private Git
history. Customer installations must still use the reviewed export and
bootstrap process documented in `docs/CUSTOMER_SAFE_CLONE.md`.

The `bin/engram` command is a temporary compatibility alias. New integrations
must use `bin/prepende`.
