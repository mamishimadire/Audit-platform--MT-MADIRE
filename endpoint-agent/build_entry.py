"""PyInstaller entry point — kept outside the `endpoint_agent` package
itself so `from endpoint_agent.cli import main` resolves the package
normally."""
from endpoint_agent.cli import main

if __name__ == "__main__":
    main()
