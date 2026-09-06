#!/usr/bin/env python3
"""Listet die aktuell am FRITZ!Repeater eingebuchten WLAN-Geraete auf.
Zugangsdaten kommen aus Umgebungsvariablen, damit kein Passwort in der
Kommandozeile / im Verlauf landet:

    FRITZ_ADDRESS   IP des Repeaters, z.B. 192.168.178.3
    FRITZ_USER      Benutzername (bei manchen Repeatern leer lassen)
    FRITZ_PASSWORD  Passwort des Repeaters

Aufruf:
    FRITZ_ADDRESS=192.168.178.x FRITZ_USER=... FRITZ_PASSWORD=... \
        ./venv/bin/python wlan_probe.py
"""
import os
import sys

from fritzconnection import FritzConnection
from fritzconnection.lib.fritzwlan import FritzWLAN

address = os.environ.get("FRITZ_ADDRESS")
user = os.environ.get("FRITZ_USER") or None
password = os.environ.get("FRITZ_PASSWORD")

if not address or not password:
    sys.exit("Bitte FRITZ_ADDRESS und FRITZ_PASSWORD als Umgebungsvariablen setzen.")

try:
    fc = FritzConnection(address=address, user=user, password=password)
except Exception as exc:  # noqa: BLE001
    sys.exit(f"Verbindung/Anmeldung fehlgeschlagen: {exc}")

print(f"Verbunden mit: {fc.modelname}  (FRITZ!OS {fc.system_version})")
wlan = FritzWLAN(fc)

seen = 0
# WLANConfiguration-Dienste durchgehen (1=2.4GHz, 2=5GHz, 3/4=Gast/weitere)
for service in range(1, 5):
    wlan.service = service
    try:
        hosts = wlan.get_hosts_info()
    except Exception:  # Dienst existiert nicht -> ueberspringen
        continue
    if not hosts:
        continue
    print(f"\n=== WLANConfiguration{service}  ({len(hosts)} Geraet(e)) ===")
    for h in hosts:
        seen += 1
        name = h.get("host_name") or h.get("hostname") or ""
        print(
            f"  MAC={h.get('mac')}  IP={h.get('ip','')}"
            f"  Signal={h.get('signal','')}%  Speed={h.get('speed','')}  {name}"
        )

if not seen:
    print("\nKeine eingebuchten WLAN-Geraete gefunden.")
print(f"\nSumme eingebucht: {seen}")
