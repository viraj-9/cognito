"""Internal-only mapping from academic requirements to product modules.

This module is intentionally not imported by API routers and is never returned
to the browser. It exists for maintainers and internal acceptance tests only.
"""
INTERNAL_ALIGNMENT = {
    "CO301.1": "services.search: graph retrieval and ranked resource search",
    "CO301.2": "data/resources.json: structured concepts and prerequisite relations",
    "CO301.3": "services.text: Unicode validation, normalization and feature extraction",
    "CO301.4": "services.language_model: supervised language classifier and evaluation",
    "CO301.5": "services.recommendations: unsupervised resource clusters and validation metrics",
    "CO301.6": "services.language_model: small neural classifier; services.recommendations: bounded contextual bandit over explicit ratings",
}
