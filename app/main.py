# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
# Project: DeepSeek Remote Bridge
"""Process entry point for the local bridge."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from pathlib import Path

from .config import config_problems, load_config
from .state import BridgeState
from .server import serve


def _banner(config, state) -> None:
    print(f"DeepSeek Remote Bridge {config.version}", flush=True)
    print(f"  provider   : {config.provider_id} ({config.display_name})", flush=True)
    print(f"  runtime    : {config.dsh_exe}", flush=True)
    print(f"  route      : {config.provider} / {config.model}", flush=True)
    print(f"  preset     : {config.permission_preset}", flush=True)
    print(f"  base path  : {config.normalized_base_path}", flush=True)
    print(f"  state home : {config.home}", flush=True)
    print(f"  listening  : {config.external_url()}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DeepSeek Remote Bridge")
    parser.add_argument("--host", default=None, help="bind address (default from config)")
    parser.add_argument("--port", type=int, default=None, help="listen port (default from config)")
    parser.add_argument("--base-path", default=None, help="URL prefix, for example /dsh/")
    parser.add_argument("--check", action="store_true", help="start, self-check, then exit")
    args = parser.parse_args(argv)

    config = load_config()
    if args.host:
        config = _with(config, host=args.host)
    if args.port:
        config = _with(config, port=args.port)
    if args.base_path:
        config = _with(config, base_path=args.base_path)

    problems = config_problems(config)
    if problems:
        print("DeepSeek Remote Bridge cannot start yet:", file=sys.stderr, flush=True)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr, flush=True)
        print(f"  config file: {config.root / 'config' / '.env'}", file=sys.stderr, flush=True)
        return 2

    state = BridgeState(config)
    _banner(config, state)

    def warm() -> None:
        started = time.time()
        try:
            state.warm_runtime()
            print(f"  runtime ready in {time.time() - started:.1f}s", flush=True)
        except Exception as error:  # noqa: BLE001 - the page reports this
            print(f"  runtime failed to start: {error}", flush=True)

    threading.Thread(target=warm, name="warm-runtime", daemon=True).start()
    server = serve(config, state)

    if args.check:
        time.sleep(3.0)
        health = state.health()
        print("  health:", health["ok"], "adapter:", health["adapter"]["ok"], flush=True)
        server.shutdown()
        state.shutdown()
        return 0 if health["adapter"]["ok"] else 1

    stop = threading.Event()

    def _graceful(signum, frame):  # noqa: ANN001 - signal signature
        stop.set()

    try:
        signal.signal(signal.SIGINT, _graceful)
        signal.signal(signal.SIGTERM, _graceful)
    except (ValueError, OSError):
        pass

    try:
        while not stop.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    print("stopping…", flush=True)
    server.shutdown()
    state.shutdown()
    return 0


def _with(config, **changes):
    from dataclasses import replace

    return replace(config, **changes)


if __name__ == "__main__":
    sys.exit(main())
