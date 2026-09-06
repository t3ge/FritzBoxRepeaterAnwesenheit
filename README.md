# FritzBox Repeater Anwesenheit → Home Assistant

Zuverlässige **Anwesenheitserkennung in Home Assistant**, wenn Geräte über das
WLAN eines **FRITZ!Repeaters** (statt über die FRITZ!Box) verbunden sind — auch
wenn das **WLAN der FRITZ!Box ausgeschaltet** ist und der Repeater **nicht im
Mesh** läuft.

Der Dienst fragt zyklisch die am Repeater eingebuchten WLAN-Clients über
**TR-064** ab und meldet je konfiguriertem Gerät `home` / `not_home` per
**MQTT-Autodiscovery** an Home Assistant. Optional wird zusätzlich ein Sensor
mit *allen* aktuell eingebuchten WLAN-Geräten veröffentlicht.

> **Kernvorteil:** Die Erkennung ist rein netzwerkbasiert (physische
> WLAN-Einbuchung) — **kein GPS**, keine IP-Erreichbarkeit — und damit **immun
> gegen VPNs wie Tailscale**, die klassische App-/Netzwerk-Presence austricksen.

---

## Inhaltsverzeichnis
- [Das Problem](#das-problem)
- [Warum die üblichen Wege scheitern](#warum-die-üblichen-wege-scheitern)
- [Die Lösung](#die-lösung)
- [Funktionen](#funktionen)
- [Voraussetzungen](#voraussetzungen)
- [Schritt 1 – Repeater vorbereiten (TR-064)](#schritt-1--repeater-vorbereiten-tr-064)
- [Schritt 2 – Geräte-MACs ermitteln](#schritt-2--geräte-macs-ermitteln)
- [Schritt 3 – Konfiguration](#schritt-3--konfiguration)
- [Schritt 4 – Deployment](#schritt-4--deployment)
- [Schritt 5 – In Home Assistant zuordnen](#schritt-5--in-home-assistant-zuordnen)
- [Der Client-Sensor](#der-client-sensor)
- [Konfigurationsreferenz](#konfigurationsreferenz)
- [Funktionsweise](#funktionsweise)
- [Fehlerbehebung](#fehlerbehebung)
- [Sicherheitshinweise](#sicherheitshinweise)

---

## Das Problem

Ausgangslage: Das WLAN der FRITZ!Box wurde **bewusst deaktiviert**, um
stattdessen das WLAN eines per Powerline angebundenen **FRITZ!Repeaters** zu
nutzen. Der Repeater wurde zurückgesetzt und ist **aus dem Mesh** entfernt
(arbeitet als reine WLAN-Bridge).

Folge: Die **Anwesenheitserkennung in Home Assistant funktioniert nicht mehr** —
alle Personen erscheinen dauerhaft als abwesend (`not_home`).

## Warum die üblichen Wege scheitern

| Ansatz | Warum er hier nicht funktioniert |
|---|---|
| **FRITZ!Box-Integration** (device_tracker) | Liest die *Hosts*-Tabelle der Box, deren Anwesenheit über WLAN-Assoziation läuft. WLAN aus → keine WLAN-Clients → Tracker frieren auf `unknown`/`not_home` ein. |
| **FRITZ!Repeater-Integration** | Der Repeater arbeitet als Bridge; die Clients ziehen IP/DHCP von der Box. Die von der Integration gelesene *Hosts*-Tabelle des Repeaters ist deshalb **leer**. |
| **Repeater ins Mesh holen** | Im Mesh synchronisiert der Master (Box) den WLAN-Zustand auf **alle** Mesh-Knoten. Box-WLAN ausschalten würde also **auch das Repeater-WLAN abschalten** — inkompatibel mit dem Wunsch „Box-WLAN aus". |
| **HA Companion App (Netzwerk/Reachability)** | Ein VPN wie **Tailscale** lässt das Handy dauerhaft „lokal/erreichbar" wirken → immer `home`. |
| **HA Companion App (GPS-Zonen)** | Wäre Tailscale-immun, scheidet hier aber aus Batterie-/Datenschutzgründen aus. |
| **Ping/Nmap** | Bei modernen Handys unzuverlässig (Schlafmodus → keine Antwort → Fehlalarme). |

## Die Lösung

Auch als reine Bridge **kennt der Repeater exakt die MAC-Adressen, die in seinem
WLAN eingebucht sind** — das ist seine Kernfunktion. Diese Information liegt im
TR-064-Dienst `WLANConfiguration` (`GetGenericAssociatedDeviceInfo`), den die
Standard-HA-Integration *nicht* ausliest.

Dieser Dienst fragt genau diese Liste ab und meldet pro Gerät `home`/`not_home`
an Home Assistant. Das ist:

- ✅ **netzwerkbasiert, kein GPS** — batterieschonend
- ✅ **immun gegen Tailscale/VPN** — es zählt die physische WLAN-Einbuchung
- ✅ **funktioniert mit ausgeschaltetem Box-WLAN und ohne Mesh**

## Funktionen

- `device_tracker.*` je konfiguriertem Gerät (`source_type: router`), direkt in
  Person-Entities nutzbar
- **Entprellung** (`consider_home`): kurze Aussetzer (Roaming, WLAN-Scan) führen
  nicht sofort zu `not_home`
- **Fail-safe**: fällt die Repeater-Abfrage aus, bleibt der letzte Zustand —
  niemand wird fälschlich „abwesend"
- **Persistent** (retained MQTT): Zustände überstehen HA-/Broker-Neustarts
- **Verbindungs-Wiederverwendung**: eine TR-064-Verbindung, Neuaufbau nur bei
  Fehler
- Optionaler Sensor `sensor.repeater_wlan_clients` mit allen eingebuchten
  Geräten (Anzahl + Liste), optional mit Klarnamen aus der FRITZ!Box

## Voraussetzungen

- Ein FRITZ!Repeater mit aktivem WLAN (getestet mit **FRITZ!Repeater 2400**)
- Eine FRITZ!Box als Router/DHCP (für optionale Klarnamen)
- Ein **MQTT-Broker**, den Home Assistant per MQTT-Integration nutzt
  (z. B. Mosquitto)
- Ein Dauerläufer für den Dienst: Docker/CasaOS oder ein Linux-Host (systemd)
- Python 3.9+ (im Docker-Image bereits enthalten)

---

## Schritt 1 – Repeater vorbereiten (TR-064)

Für die HA-Anwesenheit wird **TR-064** benötigt (nicht die UPnP-Statusinfos —
die hat ein Repeater ohnehin nicht). Bei vielen Repeater-Firmwares ist TR-064
bereits standardmäßig aktiv. Falls vorhanden, prüfe im Repeater-Webinterface:

**Heimnetz → Netzwerk → Netzwerkeinstellungen** → „Zugriff für Anwendungen
zulassen".

Am einfachsten: Schritt 2 ausführen — klappt die Abfrage, ist alles nötige aktiv.

## Schritt 2 – Geräte-MACs ermitteln

Die Skripte lesen Zugangsdaten aus **Umgebungsvariablen** (kein Passwort in der
Shell-History). Zuerst eine Python-Umgebung:

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

**Alle aktuell eingebuchten Geräte auflisten** (`wlan_probe.py`):

```bash
FRITZ_ADDRESS=192.168.178.25 FRITZ_USER="" FRITZ_PASSWORD="..." \
  ./venv/bin/python wlan_probe.py
```

**Mit Klarnamen aus der FRITZ!Box** (`label_devices.py`, empfohlen zur
Identifikation) — beschriftet die Repeater-Liste mit den Gerätenamen aus der
Box:

```bash
BOX_ADDRESS=192.168.178.1 BOX_USER="" BOX_PASSWORD="..." \
REP_ADDRESS=192.168.178.25 REP_USER="" REP_PASSWORD="..." \
  ./venv/bin/python label_devices.py
```

Notiere die MAC-Adressen der Geräte, deren Anwesenheit du tracken willst
(z. B. die Handys der Bewohner).

> ⚠️ **MAC-Randomisierung:** Moderne Handys nutzen pro WLAN eine **zufällige
> MAC**. Das ist okay, *solange sie pro Netzwerk stabil* ist. Stelle am Handy
> für dieses WLAN sicher: „Zufällige/Private MAC" = **fest/stabil pro Netzwerk**
> (nicht „ändernd/rotierend"), sonst bricht die Erkennung, sobald die MAC neu
> gewürfelt wird. Eine locally-administered MAC (2. Zeichen ist 2, 6, A oder E)
> erkennst du daran, dass sie nicht dem Gerätehersteller zugeordnet ist.

## Schritt 3 – Konfiguration

```bash
cp config.example.json config.json
# config.json bearbeiten: Repeater-PW, MQTT-Zugang, Geräte-MACs
chmod 600 config.json   # enthält Passwörter
```

Für **Klarnamen** im Client-Sensor zusätzlich den Block `box` mit dem
FRITZ!Box-Passwort ausfüllen (siehe [Konfigurationsreferenz](#konfigurationsreferenz)).
Platzhalter `DEIN_...` = deaktiviert.

## Schritt 4 – Deployment

### Variante A: CasaOS / Docker (ohne `docker build`)

Nutzt das offizielle `python`-Image und installiert die Pakete beim Start.
Alle Dateien in **einen** Host-Ordner legen (nach `/app` gemountet):

```bash
mkdir -p /DATA/repeater-presence && cd /DATA/repeater-presence
# repeater_presence.py, requirements.txt, config.json (+ docker-compose.casaos.yml) hierher
docker compose -f docker-compose.casaos.yml up -d
docker logs -f repeater-presence
```

In CasaOS alternativ über *Apps → eigene App → Import* die
`docker-compose.casaos.yml` einfügen (die drei Dateien müssen trotzdem in
`/DATA/repeater-presence` liegen). Der Terminal-Weg ist zuverlässiger.

### Variante B: Docker mit eigenem Image

```bash
docker compose up -d --build   # nutzt Dockerfile + docker-compose.yml
```

### Variante C: systemd (auf einem Linux-Host)

```bash
sudo mkdir -p /opt/repeater-presence
sudo cp repeater_presence.py config.json requirements.txt /opt/repeater-presence/
sudo python3 -m venv /opt/repeater-presence/venv
sudo /opt/repeater-presence/venv/bin/pip install -r /opt/repeater-presence/requirements.txt
sudo cp repeater-presence.service /etc/systemd/system/
sudo chmod 600 /opt/repeater-presence/config.json
sudo systemctl daemon-reload
sudo systemctl enable --now repeater-presence
journalctl -u repeater-presence -f
```

Erwartete Logausgabe:
```
Discovery veroeffentlicht: 2 Tracker + Client-Sensor.
handy_person1 -> home
handy_person2 -> not_home
Box-Namen aktualisiert (N Eintraege).
```

## Schritt 5 – In Home Assistant zuordnen

Nach dem Start erscheinen per MQTT-Discovery automatisch:
`device_tracker.handy_person1`, `device_tracker.handy_person2`, … sowie
`sensor.repeater_wlan_clients`.

Diese pro Person zuordnen:
**Einstellungen → Personen →** (Person öffnen) **→ Gerät hinzufügen →** den
passenden `device_tracker.*` wählen. Vorhandene, nun tote FRITZ-Tracker aus der
Person entfernen.

## Der Client-Sensor

`sensor.repeater_wlan_clients`:
- **Wert** = Anzahl aktuell am Repeater eingebuchter Geräte
- **Attribut `clients`** = Liste mit `name`, `mac`, `ip`, `band`, `signal`

Abschalten via `"publish_client_list": false`. Beispiel-Anzeige (Lovelace
Markdown-Karte):

```yaml
type: markdown
content: >
  {% for c in state_attr('sensor.repeater_wlan_clients','clients') %}
  - {{ c.name or c.mac }} ({{ c.band }}, {{ c.signal }}%)
  {% endfor %}
```

## Konfigurationsreferenz

| Schlüssel | Pflicht | Beschreibung |
|---|---|---|
| `repeater.address` | ✅ | IP des Repeaters |
| `repeater.user` / `repeater.password` | ✅ | TR-064-Zugang des Repeaters (`user` oft leer) |
| `mqtt.host` / `mqtt.port` | ✅ | MQTT-Broker (Port Standard 1883) |
| `mqtt.username` / `mqtt.password` | – | MQTT-Zugang (falls Broker Auth verlangt) |
| `box` | – | FRITZ!Box-Zugang für Klarnamen im Client-Sensor; Platzhalter `DEIN_...` = aus |
| `publish_client_list` | – | Client-Sensor veröffentlichen (Standard `true`) |
| `poll_interval` | – | Abfrageintervall in Sekunden (Standard `30`) |
| `consider_home` | – | Entprellung: erst nach so vielen Sekunden Abwesenheit `not_home` (Standard `180`) |
| `name_refresh` | – | Box-Namen-Cache alle N Sekunden erneuern (Standard `300`) |
| `discovery_prefix` | – | MQTT-Discovery-Präfix (Standard `homeassistant`) |
| `devices` | ✅ | Zu trackende Geräte: `id → { name, macs[] }`. Mehrere MACs pro Gerät möglich (z. B. 2.4 + 5 GHz). |

## Funktionsweise

```
                 TR-064 (WLANConfiguration)         MQTT (retained, discovery)
 FRITZ!Repeater ───────────────────────────▶ repeater_presence.py ──────────────▶ Home Assistant
   (WLAN-AP)      Liste eingebuchter MACs                                          device_tracker.* / sensor.*
                                                     ▲
                 optional TR-064 (Hosts)             │ MAC → Name
 FRITZ!Box ──────────────────────────────────────────┘
   (DHCP)
```

Alle `poll_interval` Sekunden werden die eingebuchten WLAN-MACs des Repeaters
gelesen. Pro konfiguriertem Gerät wird `home` gemeldet, wenn eine seiner MACs
präsent ist; sonst nach Ablauf von `consider_home` `not_home`. Zustände werden
per MQTT (retained) veröffentlicht; die Entities entstehen per Autodiscovery.

## Fehlerbehebung

| Symptom | Ursache / Lösung |
|---|---|
| `Could not open requirements file` | Dateien liegen nicht im gemounteten Ordner (`/app` bzw. `/DATA/repeater-presence`). Alle Dateien dorthin kopieren. |
| `executable file not found in $PATH: "sh -c …"` | `command` in Compose muss **Listenform** haben (siehe `docker-compose.casaos.yml`). |
| `Unable to retrieve resource '…/igddesc.xml'` | **Harmlos** — ein Repeater ist kein Internet-Gateway; fritzconnection fällt korrekt auf `tr64desc.xml` zurück. Wird unterdrückt. |
| Verbindung/Anmeldung fehlgeschlagen | Falsches Passwort, oder TR-064 am Repeater deaktiviert, oder falscher `user` (manche Geräte brauchen einen Benutzernamen). |
| Person schaltet nicht um | `device_tracker.*` in **Einstellungen → Personen** zuordnen; alte tote Tracker entfernen. |
| Gerät wird nie `home` | Handy nutzt rotierende MAC → auf „fest pro Netzwerk" stellen und MAC neu ermitteln. |

## Sicherheitshinweise

- `config.json` enthält Passwörter → `chmod 600`, **niemals committen**
  (steht in `.gitignore`).
- Die mitgelieferte `config.example.json` enthält nur Platzhalter und
  Beispiel-MACs.
- Der Dienst benötigt nur LAN-Zugriff auf Repeater, (optional) FRITZ!Box und
  MQTT-Broker.

---

*Erstellt mit Unterstützung von Claude Code.*
