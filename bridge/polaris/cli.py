from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from pathlib import Path

from . import config, mapping, pipeline, storage
from .matching import list_candidate_dirs, normalize_name


def cmd_status(_args: argparse.Namespace) -> int:
    config.ensure_dirs()
    cfg = config.load_config()
    store = mapping.load_store()

    inbox_count = len(list(config.INBOX_DIR.glob("*.json")))
    processed_count = len(list(config.PROCESSED_DIR.glob("*.json")))
    pending_dirs = [d for d in config.PENDING_DIR.glob("*") if d.is_dir() and any(d.glob("*.json"))]

    ok_projects = [m for m in store.projects.values() if m.status == "ok"]
    conv_count = 0
    msg_count = 0
    for m in ok_projects:
        p = Path(m.local_path)
        for source in storage.SOURCES:
            conv_count += len(list(storage.conversations_dir(p, source).glob("*.json"))) if p.is_dir() else 0
            msg_count += len(list(storage.messages_dir(p, source).glob("*.json"))) if p.is_dir() else 0

    print("Polaris Status")
    print()
    print(f"Inbox:               {inbox_count}")
    print(f"Processed events:    {processed_count}")
    print(f"Known projects:      {len(ok_projects)}")
    print(f"Pending projects:    {len(pending_dirs)}")
    print(f"Known conversations: {conv_count}")
    print(f"Known messages:      {msg_count} (inkl. Revisionen)")
    print(f"Last processing:     {pipeline.last_run() or 'noch nie'}")
    print()
    print(f"Project roots:       {', '.join(cfg.project_roots) or '(keine konfiguriert)'}")
    if not cfg.project_roots:
        print("  Eintragen: polaris roots add <Ordner, in dem die Projektordner liegen>")
    return 0


def _mappings_under(root: Path, store: mapping.ProjectStore) -> list[str]:
    hits = []
    for pid, m in store.projects.items():
        lp = Path(m.local_path).expanduser()
        if root in lp.parents or (root.exists() and root.resolve() in lp.resolve().parents):
            hits.append(f"{m.chatgpt_name or pid} -> {lp}")
    return hits


def cmd_roots(args: argparse.Namespace) -> int:
    if args.action in ("add", "remove") and not args.path:
        print(f"Fehler: polaris roots {args.action} <Ordner>", file=sys.stderr)
        return 2
    if args.action == "add":
        try:
            root = config.add_project_root(args.path)
        except ValueError as e:
            print(f"Fehler: {e}", file=sys.stderr)
            return 1
        print(f"Eingetragen: {root}")
        print("Unterordner erscheinen jetzt im Extension-Popup zur Zuordnung.")
        return 0
    if args.action == "remove":
        target = Path(args.path).expanduser()
        hits = _mappings_under(target, mapping.load_store())
        if hits and not args.force:
            print(f"Fehler: {len(hits)} Zuordnung(en) zeigen in {target}; ohne diesen Eintrag "
                  "schreibt die Bridge dort nichts mehr:", file=sys.stderr)
            for h in hits:
                print(f"  {h}", file=sys.stderr)
            print("Erst umhaengen (polaris map --force) oder mit --force entfernen.", file=sys.stderr)
            return 1
        try:
            config.remove_project_root(args.path)
        except ValueError as e:
            print(f"Fehler: {e}", file=sys.stderr)
            return 1
        print(f"Entfernt: {target}")
        return 0
    cfg = config.load_config()
    if not cfg.project_roots:
        print("Keine project_roots konfiguriert. Eintragen: polaris roots add <Ordner>")
        return 1
    for r in cfg.project_root_paths:
        state = f"{len(list_candidate_dirs([r]))} Projektordner" if r.is_dir() else "FEHLT"
        print(f"{r}  ({state})")
    return 0


def cmd_projects(_args: argparse.Namespace) -> int:
    store = mapping.load_store()
    if not store.projects:
        print("Keine bekannten Projekt-Mappings.")
        return 0
    for pid, m in store.projects.items():
        print(f"{pid}")
        print(f"  chatgpt_name    : {m.chatgpt_name}")
        print(f"  local_path      : {m.local_path}")
        print(f"  mapping_method  : {m.mapping_method}")
        print(f"  confidence      : {m.confidence:.2f}")
        print(f"  status          : {m.status}")
        print()
    return 0


def cmd_pending(_args: argparse.Namespace) -> int:
    cfg = config.load_config()
    candidates = list_candidate_dirs(cfg.project_root_paths)
    any_pending = False

    for project_dir in sorted(config.PENDING_DIR.glob("*")):
        files = sorted(project_dir.glob("*.json"))
        if not files:
            continue
        any_pending = True
        project_id = project_dir.name
        try:
            sample = json.loads(files[0].read_text(encoding="utf-8"))
            project_name = sample.get("project", {}).get("name", "?")
        except (json.JSONDecodeError, OSError):
            project_name = "?"

        print("Unknown ChatGPT project")
        print()
        print("ID:")
        print(project_id)
        print()
        print("Name:")
        print(project_name)
        print()
        print(f"Events wartend: {len(files)}")

        scored = sorted(
            (
                (
                    difflib.SequenceMatcher(
                        None, normalize_name(project_name), normalize_name(c.name)
                    ).ratio(),
                    c,
                )
                for c in candidates
            ),
            key=lambda t: t[0],
            reverse=True,
        )[:3]
        if scored:
            print("Possible matches:")
            for score, path in scored:
                print(f"  {path.name}  (score {score:.2f}, path: {path})")
        print()

    if not any_pending:
        print("Keine Pending-Projekte.")
    return 0


def cmd_map(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    local_path = Path(args.path).expanduser().resolve()

    if not local_path.is_dir():
        print(f"Fehler: {local_path} existiert nicht oder ist kein Verzeichnis.", file=sys.stderr)
        return 1

    if not mapping.is_path_safe(local_path, cfg.project_root_paths):
        print(
            "Fehler: Zielpfad liegt ausserhalb der konfigurierten project_roots "
            f"({', '.join(cfg.project_roots)}). Abgebrochen.",
            file=sys.stderr,
        )
        return 1

    try:
        m = mapping.manual_map(args.project_id, local_path, confirm=getattr(args, "force", False))
    except mapping.NeedsConfirmation as exc:
        print(f"Nicht zugeordnet: {args.project_id} {exc}. Mit --force bewusst uebernehmen.", file=sys.stderr)
        return 1
    print(f"Gemappt: {args.project_id} -> {m.local_path}")

    counts = pipeline.reprocess_pending_for_project(args.project_id)
    print(f"Nachverarbeitet: {counts}")
    return 0


def cmd_unmap(args: argparse.Namespace) -> int:
    if mapping.unmap(args.project_id):
        print(f"Mapping fuer {args.project_id} entfernt.")
        return 0
    print(f"Kein Mapping fuer {args.project_id} gefunden.", file=sys.stderr)
    return 1


def cmd_import_export(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone

    from .export_import import import_export

    since = None
    if args.since:
        since = datetime.fromisoformat(args.since)
        if since.tzinfo is None:
            since = since.astimezone(timezone.utc)
    from .export_import import ImportBusyError

    try:
        report = import_export(Path(args.source).expanduser(), since=since, dry_run=args.dry_run)
    except ImportBusyError as exc:
        print(str(exc), file=sys.stderr)
        return 75
    prefix = "(Probelauf) " if args.dry_run else ""
    print(f"{prefix}Projekt-Unterhaltungen im Export: {report.conversations}")
    for name, n in sorted(report.imported.items()):
        print(f"{prefix}{name}: {n} Nachrichten {'wuerden importiert' if args.dry_run else 'importiert'}")
    print(f"bereits vorhanden (uebersprungen): {report.existing}")
    if report.conflicts:
        print(f"KONFLIKT: {report.conflicts} Nachricht(en) liegen schon in einem anderen Projekt/Chat - nicht importiert")
    if since:
        print(f"vor {args.since} (uebersprungen): {report.skipped_before_since}")
    for pid, n in sorted(report.unmapped.items()):
        print(f"OHNE ZUORDNUNG: {pid} ({n} Nachrichten) -> polaris map {pid} <Projektordner>")
    return 0


def cmd_archive_export(args: argparse.Namespace) -> int:
    from .export_archive import ARCHIVE_DIR, archive_export

    report = archive_export(
        Path(args.source).expanduser(),
        projects=set(args.project) if args.project else None,
        since=args.since,
        min_turns=args.min_turns,
        dry_run=args.dry_run,
    )
    prefix = "(Probelauf) " if args.dry_run else ""
    for name in sorted(report.chats):
        print(f"{prefix}{name}: {report.chats[name]} Chats, {report.files.get(name, 0)} Dateien "
              f"-> {name}/{ARCHIVE_DIR}/")
    print(f"schon im Archiv (uebersprungen): {report.skipped_existing}")
    print(f"ausserhalb der Filter: {report.skipped_filter}")
    print(f"Abzweigungen: {report.deduplicated_turns} schon abgelegte Nachrichten nicht wiederholt")
    print(f"Dateien nur vermerkt (Archive): {report.noted_only}, im Projektordner verlinkt: {report.linked_local}, "
          f"weder im Export noch im Projekt: {report.missing_files}")
    return 0


def cmd_conflicts(args: argparse.Namespace) -> int:
    from . import conflicts

    if getattr(args, "resolve_chat", None):
        if args.keep not in ("existing", "rejected"):
            print("--keep existing|rejected angeben", file=sys.stderr)
            return 2
        try:
            results = conflicts.resolve_conversation(args.resolve_chat, args.keep)
        except conflicts.ResolveError as exc:
            print(f"Nicht geloest: {exc}", file=sys.stderr)
            return 1
        failed = 0
        for name, res in results:
            if isinstance(res, str):
                failed += 1
                print(f"  NICHT geloest {name}: {res}")
            elif res.hint:
                print(f"  {name}: ACHTUNG: {res.hint}")
        print(f"{len(results) - failed} von {len(results)} Konflikt(en) des Chats geloest "
              f"({'archivierte' if args.keep == 'existing' else 'abgewehrte'} Kopie behalten).")
        return 1 if failed else 0
    if args.resolve:
        if args.keep not in ("existing", "rejected"):
            print("--keep existing|rejected angeben", file=sys.stderr)
            return 2
        try:
            res = conflicts.resolve(args.resolve, args.keep)
        except conflicts.ResolveError as exc:
            print(f"Nicht geloest: {exc}", file=sys.stderr)
            return 1
        print(f"Geloest ({'archivierte' if args.keep == 'existing' else 'abgewehrte'} Kopie behalten).")
        for q in res.quarantined:
            print(f"  Quarantaene: {q}")
        if res.hint:
            print(f"  ACHTUNG: {res.hint}")
        return 0
    items = conflicts.list_conflicts()
    if not items:
        print("Keine Konflikte.")
        return 0
    for c in items:
        print(f"{c.name}")
        print(f"  Nachricht {c.message_id}: \"{c.snippet}\"")
        print(f"  archiviert in {c.existing_folder} (Chat {c.existing_conversation_id})")
        print(f"  abgewehrt fuer {c.rejected_folder} (Chat {c.rejected_conversation_id})")
    print("Loesen: polaris conflicts --resolve <Datei> --keep existing|rejected")
    print("  oder alle eines Chats (z. B. in ChatGPT verschoben): polaris conflicts --resolve-chat <Chat-ID> --keep ...")
    return 1


def cmd_held_back(args: argparse.Namespace) -> int:
    if args.clear is not None:
        n = pipeline.clear_held_back(None if args.clear == "all" else args.clear)
        print(f"{n} Eintrag/Eintraege entfernt.")
        return 0
    data = pipeline._load_held()
    if not data:
        print("Keine zurueckgehaltenen Antworten.")
        return 0
    for key, e in sorted(data.items()):
        print(f"{key}: {e.get('title')!r} seit {e.get('since')} (zuletzt {e.get('last_seen')})")
    print("Normalfall: Chat oeffnen oder im Popup 'Sync now'. Chat geloescht/unwichtig: "
          "polaris held-back --clear <projekt/chat>|all")
    return 1


def cmd_process(_args: argparse.Namespace) -> int:
    counts = pipeline.process_inbox()
    print(" ".join(f"{k}={counts.get(k, 0)}" for k in ("processed", "pending", "failed", "retry")))
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    # Die Logik liegt in update.sh neben install.sh (git + install.sh).
    script = Path(__file__).resolve().parents[2] / "update.sh"
    argv = [str(script)] + (["--yes"] if args.yes else []) + (["--check"] if args.check else [])
    os.execv(str(script), argv)
    return 2  # nicht erreicht


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="polaris")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Ueberblick ueber Inbox, Projekte, Pending").set_defaults(
        func=cmd_status
    )
    sub.add_parser("projects", help="Bekannte Projekt-Mappings auflisten").set_defaults(
        func=cmd_projects
    )
    sub.add_parser("pending", help="Unbekannte ChatGPT-Projekte auflisten").set_defaults(
        func=cmd_pending
    )

    roots_p = sub.add_parser("roots", help="Ordner verwalten, unter denen die Projektordner liegen")
    roots_p.add_argument("action", nargs="?", choices=["list", "add", "remove"], default="list")
    roots_p.add_argument("path", nargs="?")
    roots_p.add_argument("--force", action="store_true", help="remove: auch wenn Zuordnungen darauf zeigen")
    roots_p.set_defaults(func=cmd_roots)

    map_p = sub.add_parser("map", help="ChatGPT-Projekt manuell auf lokalen Pfad mappen")
    map_p.add_argument("project_id")
    map_p.add_argument("path")
    map_p.add_argument("--force", action="store_true",
                       help="bestehende Zuordnung umhaengen bzw. Ordner mit anderem Projekt teilen")
    map_p.set_defaults(func=cmd_map)

    unmap_p = sub.add_parser("unmap", help="Mapping entfernen")
    unmap_p.add_argument("project_id")
    unmap_p.set_defaults(func=cmd_unmap)

    sub.add_parser("process", help="Inbox einmalig verarbeiten").set_defaults(func=cmd_process)
    upd = sub.add_parser("update", help="Polaris aktualisieren (git-Checkout, wie omarchy plugin update)")
    upd.add_argument("--yes", "-y", action="store_true", help="ohne Nachfrage")
    upd.add_argument("--check", action="store_true", help="nur nachsehen (Exit 0 = Update da, 1 = aktuell)")
    upd.set_defaults(func=cmd_update)

    held = sub.add_parser("held-back", help="Chats mit noch nicht uebernommener letzter Antwort")
    held.add_argument("--clear", metavar="SCHLUESSEL|all", help="Eintrag entfernen (z. B. geloeschter Chat)")
    held.set_defaults(func=cmd_held_back)

    conf = sub.add_parser("conflicts", help="Abgewehrte Vermischungen anzeigen und klaeren")
    conf.add_argument("--resolve", metavar="DATEI", help="Konfliktdatei (aus der Liste)")
    conf.add_argument("--resolve-chat", metavar="CHAT_ID", help="alle Konflikte eines Chats (z. B. verschoben)")
    conf.add_argument("--keep", choices=["existing", "rejected"],
                      help="existing = archivierte Kopie ist richtig, rejected = abgewehrte ist richtig")
    conf.set_defaults(func=cmd_conflicts)

    imp = sub.add_parser("import-export", help="Offiziellen ChatGPT-Datenexport (ZIP) importieren")
    imp.add_argument("source", help="Export-ZIP, entpackter Ordner oder conversations.json")
    imp.add_argument("--since", help="nur Nachrichten ab diesem Datum (YYYY-MM-DD)")
    imp.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts schreiben")
    imp.set_defaults(func=cmd_import_export)

    arc = sub.add_parser("archive-export",
                         help="Chats aus dem entpackten Datenexport lesbar ablegen (ohne Kuration)")
    arc.add_argument("source", help="entpackter Export-Ordner")
    arc.add_argument("--project", action="append", help="nur dieser Projektordner (Name, mehrfach moeglich)")
    arc.add_argument("--since", help="nur Chats mit letzter Nachricht ab diesem Datum (YYYY-MM-DD)")
    arc.add_argument("--min-turns", type=int, default=1, help="nur Chats mit mindestens so vielen Nachrichten")
    arc.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts schreiben")
    arc.set_defaults(func=cmd_archive_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
