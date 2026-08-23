# Prepende public core boundary

The public repository contains two independently versioned projects:

- the authoritative Prepende Protocol at the repository root;
- the product-neutral brain runtime under `brain/`.

The brain runtime never vendors a protocol implementation. Its operational
status command accepts an explicit Protocol repository and verifies Protocol v2
in an isolated process. The retired embedded v0.2 package cannot satisfy that
check and is not part of the reviewed public inventory.

Public brain snapshots are produced only by the default-deny clone exporter
from an exact reviewed Git index. The snapshot contains no source history,
operator vault, runtime index, local database, graph projection, environment,
secret, tenant material, product-specific adapter, operational receipt,
recovery artifact, deployment state, or machine path.

The private repository is an overlay. It may contain tenant configuration,
operator receipts, recovery evidence, and private adapters, but it must pin the
public brain to one exact public commit. A public snapshot is accepted only
after the privacy scan, inventory check, license review, and runtime parity
suite all pass.
