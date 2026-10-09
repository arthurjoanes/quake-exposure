import argparse
import json
import logging
import os
import secrets
import sys
from datetime import UTC, date, datetime
from importlib.metadata import version
from pathlib import Path

SECRET_NAMES = (
    "postgres",
    "qe_owner",
    "qe_pipeline",
    "qe_api_read",
    "source_postgres",
    "qe_source_owner",
    "qe_source_writer",
    "qe_debezium",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qe")
    parser.add_argument("--version", action="version", version=version("quake-exposure"))
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("secrets", help="cria as senhas em .secrets/ (as que faltarem)")
    p.add_argument("--dir", type=Path, default=Path(".secrets"))
    sub.add_parser("migrate", help="aplica as migrações (papel qe_owner)")
    sub.add_parser("source-migrate", help="aplica as migrações da origem")
    sub.add_parser("cdc", help="consome as transações da carteira pelo Kafka")
    p = sub.add_parser("bootstrap", help="semeia a origem e aguarda a carteira pelo CDC")
    p.add_argument("--assets", type=int, default=10_000)
    p.add_argument("--seed", type=int, default=42)
    p = sub.add_parser("erase", help="bloqueia e exclui um titular, com recibo")
    p.add_argument("subject_token")
    p = sub.add_parser("backup", help="pg_dump das duas bases (serviço maintenance)")
    p.add_argument(
        "--directory", type=Path, default=Path(os.environ.get("QE_BACKUP_DIR", "backups"))
    )
    p = sub.add_parser("restore-verify", help="restaura em qe_restore e reaplica o ledger atual")
    p.add_argument("directory", type=Path)
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
    p = sub.add_parser("portfolio", help="comandos da carteira na origem PostgreSQL")
    p.add_argument("action", choices=["seed", "correct"])
    p.add_argument("--assets", type=int, default=10_000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--tenant")
    p.add_argument("--interval")
    p.add_argument("--lon", type=float)
    p.add_argument("--lat", type=float)
    p.add_argument("--command-id")
    sub.add_parser("worker", help="calcula e publica a exposição sem parar")
    p = sub.add_parser("explain", help="vetor de entrada e o que mudou desde a publicação anterior")
    p.add_argument("event_id")
    p.add_argument("--tenant", required=True)
    p.add_argument("--mode", choices=["RADIUS_TRIAGE", "SHAKEMAP_MMI"], required=True)
    p.add_argument("--scope", choices=["LIVE", "REPLAY", "FAULT"], default="LIVE")
    p = sub.add_parser("api", help="API somente leitura e a página")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    sub.add_parser("demo", help="demonstração: replay, revisão FAULT e job velho recusado")
    p = sub.add_parser("verify-demo", help="roda os cinco passos e grava as asserções em JSON")
    p.add_argument("--commit", required=True)
    p.add_argument("--output", type=Path, default=Path("docs/results/verify-demo.json"))
    p = sub.add_parser("bench", help="tempo de revisão aplicada até publicação (worker rodando)")
    p.add_argument("--rounds", type=int, default=5)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "secrets":
        return _secrets(args.dir)
    if args.command == "source-migrate":
        from quakeexposure.portfolio.source import connect_source, migrate_source

        with connect_source(user=os.environ.get("QE_SOURCE_DB_USER", "qe_source_owner")) as conn:
            applied = migrate_source(conn)
        print(f"migrações da origem aplicadas: {len(applied)}")
        return 0

    from quakeexposure.db import connect

    if args.command in ("erase", "backup", "restore-verify"):
        from uuid import UUID

        from quakeexposure.portfolio.source import connect_source
        from quakeexposure.privacy.backup import backup, restore_verify
        from quakeexposure.privacy.erasure import erase
        from quakeexposure.privacy.ledger import Ledger

        if args.command == "restore-verify":
            password = os.environ.get("QE_POSTGRES_PASSWORD_FILE")
            with connect(user="postgres", password_file=password) as admin:
                verified = restore_verify(admin, args.directory, Ledger())
            print(json.dumps(verified, indent=2, ensure_ascii=False))
        else:
            with connect() as platform, connect_source() as source:
                if args.command == "erase":
                    receipt = erase(platform, source, UUID(args.subject_token))
                    print(json.dumps(receipt, indent=2, ensure_ascii=False))
                else:
                    print(backup(platform, source, args.directory))
        return 0

    if args.command == "cdc":
        from quakeexposure.cdc.consumer import run_forever as run_cdc

        with connect() as conn:
            run_cdc(conn)
        return 0
    if args.command == "bootstrap":
        from quakeexposure.bootstrap import bootstrap

        bootstrap(assets=args.assets, seed=args.seed)
        return 0

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
    if args.command == "api":
        import uvicorn

        uvicorn.run("quakeexposure.api:app", host=args.host, port=args.port)
        return 0
    if args.command == "portfolio":
        from dataclasses import asdict
        from uuid import UUID

        from quakeexposure.portfolio.source import (
            CommandResult,
            SeedResult,
            connect_source,
            correct,
            seed,
        )

        result: SeedResult | CommandResult
        with connect_source() as conn:
            if args.action == "seed":
                result = seed(conn, count=args.assets, seed=args.seed)
            else:
                if any(x is None for x in (args.tenant, args.interval, args.lon, args.lat)):
                    parser.error("correct exige --tenant, --interval, --lon e --lat")
                result = correct(
                    conn,
                    args.tenant,
                    args.interval,
                    lon=args.lon,
                    lat=args.lat,
                    command_id=UUID(args.command_id) if args.command_id else None,
                )
        print(json.dumps(asdict(result), default=str, ensure_ascii=False))
        return 0
    if args.command == "explain":
        return _explain(args)

    from quakeexposure.ingestion.blobs import BlobStore

    blobs = BlobStore(Path(os.environ.get("QE_BLOB_DIR", "data/blobs")))
    if args.command == "verify-demo":
        from quakeexposure.verification import verify_demo

        with connect() as conn:
            verify_demo(conn, connect, blobs, commit=args.commit, output=args.output)
        print(f"resultado salvo: {args.output}")
        return 0
    if args.command == "replay":
        from quakeexposure.ingestion.replay import replay

        with connect() as conn:
            done = replay(conn, blobs, datetime.now(UTC))
        print(
            f"capturas gravadas: {done.stored}, já existiam: {done.already},"
            f" sem arquivo: {done.missing}"
        )
        return 0

    if args.command == "worker":
        from quakeexposure.exposure import worker

        worker.run_forever(lambda: (connect(), blobs))
        return 0
    if args.command in ("demo", "bench"):
        from quakeexposure.exposure import demo

        with connect() as conn:
            if args.command == "demo":
                demo.demo(conn, connect, blobs)
            else:
                demo.bench(conn, blobs, rounds=args.rounds)
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


def _explain(args: argparse.Namespace) -> int:
    from quakeexposure.db import connect
    from quakeexposure.exposure.explain import diff, format_diff, publications

    with connect() as conn:
        row = conn.execute(
            "SELECT earthquake_uid FROM quake.alias WHERE stream_scope = %s AND provider_id = %s",
            (args.scope, args.event_id),
        ).fetchone()
        if row is None:
            print(f"evento {args.event_id} não encontrado no escopo {args.scope}", file=sys.stderr)
            return 1
        current, previous = publications(conn, args.tenant, row[0], args.scope, args.mode)
        if current is None:
            print("sem publicação para este evento, tenant e modo", file=sys.stderr)
            return 1
        print(format_diff(diff(conn, previous, current)))
    return 0
