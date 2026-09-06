#!/usr/bin/env python3
"""FRITZ!Repeater WLAN-Anwesenheit -> Home Assistant (via MQTT-Discovery).

Fragt zyklisch die am Repeater eingebuchten WLAN-Geraete ab und meldet:
  * je konfiguriertem Geraet 'home'/'not_home' als device_tracker
    (source_type: router) -> nutzbar fuer Person-Entities
  * optional einen Sensor mit ALLEN aktuell eingebuchten WLAN-Geraeten
    (Anzahl als Wert, komplette Liste als Attribute)

Eigenschaften:
- Debounce ('consider_home'): kurze Aussetzer fuehren nicht sofort zu 'not_home'.
- Fail-safe: schlaegt die Repeater-Abfrage fehl, bleibt der Zustand erhalten.
- Retained MQTT: Zustaende ueberstehen HA-/Broker-Neustarts.
- Optionale Klarnamen aus der Fritzbox (Block "box" in config.json).

Konfiguration: config.json  (siehe config.example.json)
"""
import json
import logging
import os
import signal
import sys
import time

from fritzconnection import FritzConnection
from fritzconnection.lib.fritzwlan import FritzWLAN
from fritzconnection.lib.fritzhosts import FritzHosts
import paho.mqtt.client as mqtt

CONFIG_PATH = os.environ.get("PRESENCE_CONFIG", "config.json")
BAND = {1: "2.4GHz", 2: "5GHz", 3: "Gast", 4: "?"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("repeater-presence")
# Harmlose INFO-Meldungen von fritzconnection (z.B. fehlende igddesc.xml am
# Repeater) unterdruecken:
logging.getLogger("fritzconnection").setLevel(logging.WARNING)


def load_config(path):
    with open(path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg.setdefault("poll_interval", 30)
    cfg.setdefault("consider_home", 180)
    cfg.setdefault("discovery_prefix", "homeassistant")
    cfg.setdefault("publish_client_list", True)
    cfg.setdefault("name_refresh", 300)  # Box-Namen-Cache alle N Sekunden
    for dev in cfg["devices"].values():
        dev["macs"] = [m.strip().upper() for m in dev["macs"]]
    return cfg


def connect_repeater(rep_cfg):
    return FritzConnection(
        address=rep_cfg["address"],
        user=rep_cfg.get("user") or None,
        password=rep_cfg["password"],
    )


def get_repeater_clients(fc):
    """Liste aller aktuell am Repeater eingebuchten WLAN-Geraete.

    Nutzt eine bestehende FritzConnection (Wiederverwendung -> weniger Last
    und kein wiederholtes Verbindungs-Log). Rueckgabe: list[dict] mit
    mac (uppercase), ip, band, signal. Wirft eine Exception, wenn gar kein
    WLAN-Dienst erreichbar ist (Aufrufer baut die Verbindung dann neu auf).
    """
    wlan = FritzWLAN(fc)
    clients = []
    found_service = False
    for service in range(1, 5):
        wlan.service = service
        try:
            hosts = wlan.get_hosts_info()
        except Exception:  # Dienst existiert nicht
            continue
        found_service = True
        for h in hosts:
            mac = (h.get("mac") or "").upper()
            if not mac:
                continue
            clients.append({
                "mac": mac,
                "ip": h.get("ip", ""),
                "band": BAND.get(service, str(service)),
                "signal": h.get("signal", ""),
            })
    if not found_service:
        raise RuntimeError("Kein WLANConfiguration-Dienst erreichbar")
    return clients


class BoxNames:
    """Cached MAC->Name Aufloesung aus der Fritzbox (optional)."""

    def __init__(self, box_cfg, refresh):
        self.cfg = box_cfg
        self.refresh = refresh
        self.map = {}
        self.ts = 0.0

    @property
    def enabled(self):
        pw = (self.cfg or {}).get("password") or ""
        # Platzhalter aus der Vorlage nicht als aktiv werten
        return bool(self.cfg and pw and not pw.startswith("DEIN_"))

    def get(self, mac, now):
        if not self.enabled:
            return ""
        if now - self.ts > self.refresh or not self.map:
            self._refresh()
            self.ts = now
        return self.map.get(mac, "")

    def _refresh(self):
        try:
            box = FritzConnection(
                address=self.cfg["address"],
                user=self.cfg.get("user") or None,
                password=self.cfg["password"],
            )
            new = {}
            for h in FritzHosts(box).get_hosts_info():
                mac = (h.get("mac") or "").upper()
                if mac:
                    new[mac] = h.get("name") or ""
            self.map = new
            log.info("Box-Namen aktualisiert (%d Eintraege).", len(new))
        except Exception as exc:  # noqa: BLE001
            log.warning("Box-Namen-Abfrage fehlgeschlagen: %s", exc)


class MqttPublisher:
    def __init__(self, cfg):
        m = cfg["mqtt"]
        self.prefix = cfg["discovery_prefix"]
        try:  # paho-mqtt 2.x
            self.client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2, client_id="repeater-presence")
        except (AttributeError, TypeError):  # paho-mqtt 1.x
            self.client = mqtt.Client(client_id="repeater-presence")
        if m.get("username"):
            self.client.username_pw_set(m["username"], m.get("password"))
        self.avail_topic = f"{self.prefix}/device_tracker/repeater_presence/availability"
        self.client.will_set(self.avail_topic, "offline", retain=True)
        self.client.connect(m["host"], int(m.get("port", 1883)), keepalive=60)
        self.client.loop_start()
        self.client.publish(self.avail_topic, "online", retain=True)

    def announce_tracker(self, dev_id, dev):
        topic = f"{self.prefix}/device_tracker/{dev_id}/config"
        payload = {
            "name": dev.get("name", dev_id),
            "unique_id": f"repeater_presence_{dev_id}",
            "object_id": dev_id,
            "state_topic": f"{self.prefix}/device_tracker/{dev_id}/state",
            "availability_topic": self.avail_topic,
            "payload_home": "home",
            "payload_not_home": "not_home",
            "source_type": "router",
        }
        self.client.publish(topic, json.dumps(payload), retain=True)

    def publish_tracker(self, dev_id, state):
        self.client.publish(
            f"{self.prefix}/device_tracker/{dev_id}/state", state, retain=True)

    def announce_client_sensor(self):
        base = f"{self.prefix}/sensor/repeater_wlan_clients"
        payload = {
            "name": "Repeater WLAN Clients",
            "unique_id": "repeater_presence_clients",
            "object_id": "repeater_wlan_clients",
            "state_topic": f"{base}/state",
            "json_attributes_topic": f"{base}/attributes",
            "availability_topic": self.avail_topic,
            "unit_of_measurement": "Geräte",
            "state_class": "measurement",
            "icon": "mdi:wifi",
        }
        self.client.publish(f"{base}/config", json.dumps(payload), retain=True)

    def publish_client_sensor(self, count, clients):
        base = f"{self.prefix}/sensor/repeater_wlan_clients"
        self.client.publish(f"{base}/state", str(count), retain=True)
        attrs = {"clients": clients, "count": count}
        self.client.publish(
            f"{base}/attributes", json.dumps(attrs, ensure_ascii=False), retain=True)

    def close(self):
        try:
            self.client.publish(self.avail_topic, "offline", retain=True)
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass


def main():
    cfg = load_config(CONFIG_PATH)
    pub = MqttPublisher(cfg)
    boxnames = BoxNames(cfg.get("box"), cfg["name_refresh"])

    for dev_id, dev in cfg["devices"].items():
        pub.announce_tracker(dev_id, dev)
    if cfg["publish_client_list"]:
        pub.announce_client_sensor()
    log.info("Discovery veroeffentlicht: %d Tracker%s.", len(cfg["devices"]),
             " + Client-Sensor" if cfg["publish_client_list"] else "")

    last_seen = {dev_id: 0.0 for dev_id in cfg["devices"]}
    last_state = {dev_id: None for dev_id in cfg["devices"]}
    consider_home = cfg["consider_home"]
    interval = cfg["poll_interval"]

    running = {"go": True}
    signal.signal(signal.SIGTERM, lambda *_: running.update(go=False))
    signal.signal(signal.SIGINT, lambda *_: running.update(go=False))

    fc = None  # wiederverwendete Repeater-Verbindung
    while running["go"]:
        now = time.time()
        try:
            if fc is None:
                fc = connect_repeater(cfg["repeater"])
            clients = get_repeater_clients(fc)
        except Exception as exc:  # fail-safe: Zustand nicht aendern
            log.warning("Repeater-Abfrage fehlgeschlagen (Zustand bleibt): %s", exc)
            fc = None  # Verbindung im naechsten Zyklus neu aufbauen
            time.sleep(interval)
            continue

        present = {c["mac"] for c in clients}

        # --- device_tracker je konfiguriertem Geraet ---
        for dev_id, dev in cfg["devices"].items():
            if any(mac in present for mac in dev["macs"]):
                last_seen[dev_id] = now
                state = "home"
            else:
                state = "home" if (now - last_seen[dev_id]) < consider_home else "not_home"
            if state != last_state[dev_id]:
                pub.publish_tracker(dev_id, state)
                last_state[dev_id] = state
                log.info("%s -> %s", dev_id, state)

        # --- Sensor mit allen eingebuchten Clients ---
        if cfg["publish_client_list"]:
            enriched = []
            for c in sorted(clients, key=lambda x: x["mac"]):
                name = boxnames.get(c["mac"], now)
                enriched.append({
                    "name": name,
                    "mac": c["mac"],
                    "ip": c["ip"],
                    "band": c["band"],
                    "signal": c["signal"],
                })
            pub.publish_client_sensor(len(enriched), enriched)

        time.sleep(interval)

    pub.close()
    log.info("Beendet.")


if __name__ == "__main__":
    main()
