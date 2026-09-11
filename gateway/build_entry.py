"""PyInstaller entry point — kept outside the `gateway` package itself so
`from gateway.cli import main` resolves the package normally."""
from gateway.cli import main

if __name__ == "__main__":
    main()
