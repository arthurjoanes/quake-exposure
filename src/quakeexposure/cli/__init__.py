import argparse
import json
import logging
import os
import secrets
import sys
from datetime import UTC, date, datetime
from importlib.metadata import version
from pathlib import Path

SECRET_NAMES = ("postgres", "qe_owner", "qe_pipeline", "qe_api_read")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qe")
    parser.add_argument("--version", action="version", version=version("quake-exposure"))
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("secrets", help="cria as senhas em .secrets/ (as que faltarem)")
    p.add_argument("--dir", type=Path, default=Path(".secrets"))
    sub.add_parser("migrate", help="aplica as migrações (papel qe_owner)")
    sub.add_parser("collect", help="roda o coletor do USGS sem parar")
    p = sub.add_parser("backfill", help="busca no FDSN dia a dia, com cursor por dia")
    p.add_argument("--from", dest="first", type=date.fromisoformat, required=True)
    p.add_argument("--to", dest="last", type=date.fromisoformat, required=True)
    sub.add_parser("replay", help="grava as capturas reais do manifesto como REPLAY")
    p = sub.add_parser("status", help="idade das fontes, quarentena e pendências")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("watch", help="watchlist de eventos que sempre entram no cálculo")
    p.add_argument("action", choices=["add", "remove", "list"])
    p.add_argument("event_id", nargs="?")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "secrets":
        return _secrets(args.dir)

    from quakeexposure.db import connect

    if args.command == "migrate":
        from quakeexposure.migrate import migrate

        with connect(user=os.environ.get("QE_DB_USER", "qe_owner")) as conn:
            applied = migrate(conn)
        print(f"migrações aplicadas: {len(applied)}")
        return 0
    if args.command == "status":
        from quakeexposure.status import format_report, report

        with connect() as conn:
            r = report(conn, datetime.now(UTC))
        print(json.dumps(r, indent=2) if args.json else format_report(r))
        return 0
    if args.command == "watch":
        return _watch(args.action, args.event_id)

    from quakeexposure.ingestion.blobs import BlobStore

    blobs = BlobStore(Path(os.environ.get("QE_BLOB_DIR", "data/blobs")))
    if args.command == "replay":
        from quakeexposure.ingestion.replay import replay

        with connect() as conn:
            done = replay(conn, blobs, datetime.now(UTC))
        print(
            f"capturas gravadas: {done.stored}, já existiam: {done.already},"
            f" sem arquivo: {done.missing}"
        )
        return 0

    from quakeexposure.ingestion.collector import Collector, run_forever
    from quakeexposure.ingestion.http import UsgsClient

    client = UsgsClient()
    if args.command == "backfill":
        with connect() as conn:
            total = Collector(conn, client, blobs).backfill(args.first, args.last)
        print(f"revisões novas: {total}")
        return 0
    run_forever(lambda: Collector(connect(), client, blobs))
    return 0


def _secrets(directory: Path) -> int:
    directory.mkdir(exist_ok=True)
    for name in SECRET_NAMES:
        path = directory / name
        if not path.exists():
            path.write_text(secrets.token_urlsafe(24), "utf-8")
            print(f"criado {path}")
    return 0


def _watch(action: str, event_id: str | None) -> int:
    from quakeexposure.db import connect
    from quakeexposure.ingestion import store
    from quakeexposure.ingestion.projection import reconsult_url

    if action != "list" and not event_id:
        print("informe o eventid", file=sys.stderr)
        return 2
    with connect() as conn:
        if action == "add" and event_id:
            store.watch(conn, event_id, reconsult_url(event_id), datetime.now(UTC))
        elif action == "remove":
            conn.execute("DELETE FROM control.watchlist WHERE provider_event_id = %s", (event_id,))
        else:
            for (eid,) in conn.execute(
                "SELECT provider_event_id FROM control.watchlist ORDER BY 1"
            ):
                print(eid)
    return 0
