import sys

from hmnd_bot import __version__


def main() -> int:
    if "--version" in sys.argv[1:]:
        print(__version__)
        return 0
    from hmnd_bot.main import run
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
