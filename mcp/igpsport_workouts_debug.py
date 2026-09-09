#!/usr/bin/env python
"""Diagnostic : que renvoie reellement le connecteur iGPSport pour les seances ?

Lecture seule, ne supprime ni ne cree rien.

Contexte : sur le serveur international, `list_workouts()` renvoie une liste
vide alors que les seances existent bien. push_program.py s'en sert pour
supprimer les anciennes avant de repousser, donc la suppression ne fait rien
et les doublons s'empilent a chaque push.

Ce script cherche par ou lister les seances pour de bon : il essaie les
signatures plausibles de list_workouts, puis inspecte le service a la
recherche d'une autre methode ou d'un client HTTP utilisable.

Usage :
    set -a; source ~/mcp/secrets/igpsport.env; set +a
    ~/mcp/igpsport-mcp/.venv/bin/python mcp/igpsport_workouts_debug.py
"""
import inspect
import json

from igpsport_mcp.tools._service import IGPSportService
from igpsport_mcp.config import load_config


def show(label, value, limit=700):
    text = value if isinstance(value, str) else json.dumps(value, default=str)[:limit]
    print(f"  {label:<34} {text}")


svc = IGPSportService(load_config())

print("=== 1. Signatures tentees pour list_workouts ===")
for label, kwargs in [("list_workouts()", {}),
                      ("list_workouts(limit=100)", {"limit": 100}),
                      ("list_workouts(page=1)", {"page": 1}),
                      ("list_workouts(page=1, size=100)", {"page": 1, "size": 100}),
                      ("list_workouts(page_size=100)", {"page_size": 100})]:
    try:
        res = svc.list_workouts(**kwargs)
        if isinstance(res, dict):
            items = res.get("workouts") or res.get("items") or res.get("data") or []
            n = len(items) if isinstance(items, list) else "?"
            show(label, f"{n} seance(s) | cles: {list(res.keys())[:8]}")
            if items:
                show("  -> premier element", items[0])
        else:
            show(label, f"type inattendu {type(res).__name__} : {res}")
    except TypeError as exc:
        show(label, f"signature refusee ({str(exc)[:70]})")
    except Exception as exc:
        show(label, f"{type(exc).__name__}: {str(exc)[:90]}")

print("\n=== 2. Methodes du service liees aux seances ===")
for name in sorted(dir(svc)):
    if name.startswith("_") or not callable(getattr(svc, name, None)):
        continue
    if any(k in name for k in ("workout", "training", "plan", "course", "list")):
        try:
            sig = str(inspect.signature(getattr(svc, name)))
        except (TypeError, ValueError):
            sig = "(?)"
        print(f"  svc.{name}{sig}")

print("\n=== 3. Client HTTP interne (pour un appel brut) ===")
for attr in sorted(dir(svc)):
    if attr.startswith("__"):
        continue
    obj = getattr(svc, attr, None)
    if obj is None or callable(obj):
        continue
    kind = type(obj).__name__
    if any(k in kind.lower() for k in ("client", "session", "http", "api", "config")):
        print(f"  svc.{attr} : {kind}")
        for sub in sorted(dir(obj)):
            if sub.startswith("_") or not callable(getattr(obj, sub, None)):
                continue
            if sub in ("get", "post", "delete", "put", "request", "call"):
                print(f"      .{sub}()")

print("\n=== 4. Base URL / region effective ===")
cfg = load_config()
for attr in sorted(dir(cfg)):
    if attr.startswith("_"):
        continue
    val = getattr(cfg, attr, None)
    if isinstance(val, str) and any(k in attr.lower()
                                    for k in ("url", "region", "host", "base", "domain")):
        show(f"config.{attr}", val)
