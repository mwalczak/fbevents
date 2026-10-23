"""Command line: python -m fbevents {login,run,test-email}."""

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from .config import Config
from .state import State


def main(argv=None):
    p = argparse.ArgumentParser(prog="fbevents", description="Daily email of new Facebook events.")
    p.add_argument("--config", default=os.environ.get("FBEVENTS_CONFIG", "config.toml"))
    p.add_argument("--state", default=os.environ.get("FBEVENTS_STATE", "state"))
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="log in to Facebook in a visible browser and save the session")
    r = sub.add_parser("run", help="collect new events and email them")
    r.add_argument("--dry-run", action="store_true", help="print the digest; send and remember nothing")
    r.add_argument("--no-delay", action="store_true", help="skip the random wait before starting")
    r.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS)
    sub.add_parser("test-email", help="send a test email")
    args = p.parse_args(argv)

    state = State(args.state)
    handlers = [logging.StreamHandler(sys.stderr),
                RotatingFileHandler(state.log, maxBytes=1_000_000, backupCount=2)]
    logging.basicConfig(level=logging.WARNING, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("fbevents").setLevel(logging.DEBUG if args.verbose else logging.INFO)

    if args.cmd == "login":
        from .browser import interactive_login
        cfg = Config.load(args.config, required=False)
        interactive_login(cfg, state.auth)
        print(f"\nSaved {state.auth}. Treat it like a password.\n"
              "To use it on the server, paste its contents into the FB_AUTH_JSON secret on GitHub\n"
              "(Settings -> Secrets and variables -> Actions), then run 'Update settings'.")
        return 0

    cfg = Config.load(args.config)
    if args.cmd == "test-email":
        from .digest import send
        send(cfg, "Facebook events: test", "Email from fbevents works.")
        print("Sent to " + ", ".join(cfg.mail_to))
        return 0

    from .run import run
    try:
        return run(cfg, state, dry_run=args.dry_run, delay=not args.no_delay,
                   auth_seed=os.environ.get("FBEVENTS_AUTH_SEED"))
    finally:
        log_memory_peak()


def log_memory_peak(cgroup="/sys/fs/cgroup"):
    """In the container: the most memory the run (browser included) used, against its limit."""
    try:
        peak = int(open(f"{cgroup}/memory.peak").read())
        limit = open(f"{cgroup}/memory.max").read().strip()
    except (OSError, ValueError):
        return  # not in a container with cgroup v2
    mib = lambda b: f"{int(b) // 2**20} MiB"  # noqa: E731
    logging.getLogger("fbevents").info(
        "memory peak: %s of %s", mib(peak), "no limit" if limit == "max" else mib(limit))


if __name__ == "__main__":
    sys.exit(main())
