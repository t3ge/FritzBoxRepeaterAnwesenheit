#!/usr/bin/env python3
"""Listet die am Repeater eingebuchten WLAN-Geraete auf und beschriftet sie
mit den Geraetenamen aus der Fritzbox (DHCP-/Hosts-Tabelle).

Umgebungsvariablen:
    REP_ADDRESS   IP des Repeaters (z.B. 192.168.178.3)
    REP_USER      Repeater-Benutzer (meist leer)
    REP_PASSWORD  Repeater-Passwort

    BOX_ADDRESS   IP der Fritzbox   (z.B. 192.168.178.1)
    BOX_USER      Box-Benutzer (meist leer)
    BOX_PASSWORD  Box-Passwort
"""
import os
import sys

from fritzconnection import FritzConnection
from fritzconnection.lib.fritzwlan import FritzWLAN
from fritzconnection.lib.fritzhosts import FritzHosts


def env(name):
    v = os.environ.get(name)
    return v if v else None


# --- Fritzbox: MAC -> Name Mapping aufbauen -------------------------------
mac_to_name = {}
box_addr = env("BOX_ADDRESS")
if box_addr and env("BOX_PASSWORD"):
    try:
        box = FritzConnection(address=box_addr, user=env("BOX_USER"),
                              password=env("BOX_PASSWORD"))
        hosts = FritzHosts(box).get_hosts_info()
        for h in hosts:
            mac = (h.get("mac") or "").upper()
            if mac:
                mac_to_name[mac] = h.get("name") or ""
        print(f"Fritzbox {box.modelname}: {len(mac_to_name)} bekannte Geraete.\n")
    except Exception as exc:  # noqa: BLE001
        print(f"(Warnung: Box-Abfrage fehlgeschlagen: {exc})\n")
else:
    print("(Keine Box-Zugangsdaten gesetzt -> ohne Namen)\n")

# --- Repeater: eingebuchte WLAN-Geraete -----------------------------------
rep_addr = env("REP_ADDRESS")
if not rep_addr or not env("REP_PASSWORD"):
    sys.exit("Bitte REP_ADDRESS und REP_PASSWORD setzen.")

try:
    rep = FritzConnection(address=rep_addr, user=env("REP_USER"),
                          password=env("REP_PASSWORD"))
except Exception as exc:  # noqa: BLE001
    sys.exit(f"Repeater-Verbindung fehlgeschlagen: {exc}")

wlan = FritzWLAN(rep)
band = {1: "2.4GHz", 2: "5GHz", 3: "Gast", 4: "?"}
rows = []
for service in range(1, 5):
    wlan.service = service
    try:
        hosts = wlan.get_hosts_info()
    except Exception:
        continue
    for h in hosts:
        mac = (h.get("mac") or "").upper()
        rows.append((
            mac_to_name.get(mac, ""),
            mac,
            h.get("ip", ""),
            band.get(service, str(service)),
            h.get("signal", ""),
        ))

rows.sort(key=lambda r: (r[0] == "", r[0].lower()))  # benannte zuerst
print(f"{'NAME':<28} {'MAC':<19} {'IP':<16} {'BAND':<7} SIGNAL")
print("-" * 82)
for name, mac, ip, bnd, sig in rows:
    print(f"{name:<28} {mac:<19} {ip:<16} {bnd:<7} {sig}%")
print(f"\nSumme: {len(rows)} Geraete am Repeater.")
