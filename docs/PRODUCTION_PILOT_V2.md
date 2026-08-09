# Prepende Protocol v2 production pilot gate

No production integration is part of the core release candidate. A later tenant pilot must
pass these gates in order:

1. Select one isolated tenant and one registered stream. Never pool tenants, models, or
   domains.
2. Start with synthetic records, then read-only shadow commitments for delivery estimates,
   quote assumptions, and conversion judgments.
3. Lock and receive a verifier-trusted anchor before showing or acting on a commitment.
4. Resolve from a separate system-of-record principal under an independently trusted key.
5. Preserve normal approval gates. Calibration never authorizes sends, payments, publishing,
   deployment, or customer-visible action by itself.
6. Require 100 externally resolved commitments in a single segregated domain corpus, clean
   chain and anchor receipts, acceptable deadline/forfeit rates, and confidence intervals
   before considering any autonomy increase.

Until those receipts exist, use `Prepende Protocol` for this library and `Prepende Brain` for
the separate memory/orchestration runtime. Do not cite the latter as production use of the
prediction protocol.
