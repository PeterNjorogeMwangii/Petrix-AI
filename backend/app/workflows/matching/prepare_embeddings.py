"""Run from backend: python -m app.workflows.matching.prepare_embeddings."""
from app.core.config import Settings
from app.workflows.matching.embeddings import cache_directory


def main():
    settings = Settings()
    if settings.embedding_provider != "fastembed":
        print("The configured embedding provider is remote; no local model download is required.")
        return 0
    print("Preparing the configured local embedding model. The first download may take several minutes.")
    try:
        from fastembed import TextEmbedding
        model = TextEmbedding(model_name=settings.embedding_model, cache_dir=str(cache_directory(settings)),
            threads=2, providers=["CPUExecutionProvider"])
        vector = next(iter(model.embed(["Petrix AI intent matching"])))
        print("Embedding model ready ({} dimensions). Restart the backend if it is running.".format(len(vector)))
        return 0
    except Exception:
        print("Could not prepare the model. Check its name, internet access, and cache directory, then retry.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
