"""One-shot Compose initializer; exits nonzero when schema or replication fails."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.local import load_config
from replication.bootstrap import setup
from scripts.seed import seed

if __name__ == "__main__":
    configuration = load_config(".local/compose-config.json")
    setup(configuration)
    seed(configuration)
