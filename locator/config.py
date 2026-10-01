"""Settings shared by the index build and the locator. Change a model here only."""

# Local embedding model (SPEC §5.1). Step up to "BAAI/bge-base-en-v1.5" only if
# the eval shows vector-side misses; then rebuild every index.
EMBED_MODEL = "BAAI/bge-small-en-v1.5"

# bge models expect this before a search query (not before the chunks they search).
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
