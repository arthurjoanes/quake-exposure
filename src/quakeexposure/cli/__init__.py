import argparse
from importlib.metadata import version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qe")
    parser.add_argument("--version", action="version", version=version("quake-exposure"))
    parser.parse_args(argv)
    parser.print_help()
    return 0
