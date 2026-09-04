#!/usr/bin/env python3
"""Spawn pseudo virtual objects into the V2X MQTT broker.

For the demo and practice event: publishes V2XVehiclePositionJson payloads as
if extra karts were on the track, so a single real kart sees peers on
``/v2x/vehicle_positions`` and can practise avoidance and overtaking without
another kart being driven.

    ./v2x_virtual_objects.py --scenario v2x-scenarios/kashiwanoha-demo.yaml
    ./v2x_virtual_objects.py --scenario … --dry-run     # 座標と動きだけ確認
    ./v2x_virtual_objects.py --scenario … --only d8     # 1 台だけ出す

Each object publishes ``v2x/vehicles/{id}/position`` at the scenario rate. The
broker's own fan-out is the relay (R6.4.1), so nothing has to run on the kart.
The kart must, however, list these ids in ``V2X_VEHICLE_IDS`` — its receive
routes are generated from that list — and with the broker's strict ACL each id
needs its own certificate, because the CN becomes the MQTT username. Both are
checked before the first publish; see vehicle/v2x-virtual-objects.md.

The rules live in vehicle/v2x_virtual_objects_core.py; this file is the I/O.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from v2x_virtual_objects_core import (  # noqa: E402
    MODE_RACELINE,
    ObjectState,
    Raceline,
    Scenario,
    ScenarioError,
    VirtualObject,
    advance,
    build_payload,
    common_name_of,
    initial_state,
    missing_files,
    parse_raceline,
    parse_scenario,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# v2x_kart_position.launch.py の vehicle_ids 既定値。
KART_DEFAULT_IDS = ("d1", "d2", "d3", "d4")
TRANSPORT_AUTO = "auto"
TRANSPORT_PAHO = "paho"
TRANSPORT_MOSQUITTO = "mosquitto_pub"


# --- 引数 -------------------------------------------------------------------
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--scenario", required=True, help="scenario YAML (v2x-scenarios/*.yaml)")
    parser.add_argument("--only", help="comma separated ids; spawn just these objects")
    parser.add_argument("--host", help="override broker.host")
    parser.add_argument("--port", type=int, help="override broker.port")
    parser.add_argument("--certs-dir", help="override broker.certs_dir")
    parser.add_argument("--no-tls", action="store_true", help="plain MQTT (local mosquitto test)")
    parser.add_argument("--rate", type=float, help="override rate_hz")
    parser.add_argument("--duration", type=float, help="[s] stop after this long (default endless)")
    parser.add_argument(
        "--transport",
        default=TRANSPORT_AUTO,
        choices=(TRANSPORT_AUTO, TRANSPORT_PAHO, TRANSPORT_MOSQUITTO),
        help="MQTT client; auto prefers paho-mqtt and falls back to mosquitto_pub",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print payloads instead of connecting to the broker"
    )
    parser.add_argument(
        "--skip-cert-check", action="store_true", help="do not verify certificate CN against the id"
    )
    parser.add_argument("--quiet", action="store_true", help="no per-second status line")
    return parser.parse_args(argv)


# --- パス解決 ---------------------------------------------------------------
def resolve_path(value: str, scenario_path: str) -> str:
    """Resolve a scenario path against the repo root, then the scenario's dir."""
    expanded = os.path.expanduser(value)
    if os.path.isabs(expanded):
        return expanded
    candidates = (
        os.path.join(REPO_ROOT, expanded),
        os.path.join(os.path.dirname(os.path.abspath(scenario_path)), expanded),
        os.path.abspath(expanded),
    )
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0]


def display_path(path: str) -> str:
    """Repo-relative when the path is inside the repo, absolute otherwise.

    certs_dir normally sits in a sibling checkout (aichallenge-aws), where a
    relative path would be a wall of ``../``.
    """
    relative = os.path.relpath(path, REPO_ROOT)
    return path if relative.startswith("..") else relative


def load_scenario(path: str, args: argparse.Namespace) -> Scenario:
    """Read the YAML, apply the command line overrides and validate."""
    try:
        import yaml
    except ImportError:  # pragma: no cover - depends on the machine
        raise SystemExit("ERROR: PyYAML is required. pip3 install pyyaml")

    try:
        with open(path, encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except OSError as error:
        raise SystemExit(f"ERROR: cannot read scenario: {error}")
    except yaml.YAMLError as error:
        raise SystemExit(f"ERROR: scenario is not valid YAML: {error}")

    if not isinstance(document, dict):
        raise SystemExit("ERROR: scenario must be a YAML mapping")

    broker = document.setdefault("broker", {}) or {}
    document["broker"] = broker
    if args.host:
        broker["host"] = args.host
    if args.no_tls:
        broker["tls"] = False
        broker.pop("certs_dir", None)
    if args.port is not None:
        broker["port"] = args.port
    if args.certs_dir:
        broker["certs_dir"] = args.certs_dir
    if args.rate is not None:
        document["rate_hz"] = args.rate

    try:
        scenario = parse_scenario(document)
    except ScenarioError as error:
        raise SystemExit(f"ERROR: {error}")

    if args.only:
        wanted = tuple(part.strip() for part in args.only.split(",") if part.strip())
        unknown = [name for name in wanted if name not in scenario.vehicle_ids]
        if unknown:
            raise SystemExit(
                f"ERROR: --only names {unknown} which the scenario does not define "
                f"(it has {list(scenario.vehicle_ids)})"
            )
        scenario = Scenario(
            broker=scenario.broker,
            objects=tuple(item for item in scenario.objects if item.vehicle_id in wanted),
            rate_hz=scenario.rate_hz,
        )
    return scenario


def load_racelines(scenario: Scenario, scenario_path: str) -> Dict[str, Raceline]:
    """Load every raceline the scenario refers to, keyed by its scenario value."""
    racelines: Dict[str, Raceline] = {}
    for reference in scenario.raceline_paths:
        path = resolve_path(reference, scenario_path)
        try:
            with open(path, encoding="utf-8") as handle:
                racelines[reference] = parse_raceline(handle.read())
        except OSError as error:
            raise SystemExit(f"ERROR: cannot read raceline {reference}: {error}")
        except ScenarioError as error:
            raise SystemExit(f"ERROR: raceline {path}: {error}")
        line = racelines[reference]
        shape = "loop" if line.closed else "open line"
        print(
            f"raceline {display_path(path)}: "
            f"{len(line.points)} points, {line.length:.1f} m, {shape}"
        )
    return racelines


# --- 事前確認 ---------------------------------------------------------------
def check_certificates(scenario: Scenario, scenario_path: str, verify_cn: bool) -> None:
    """Fail early on a certificate that is absent or issued to another id.

    With the broker's strict ACL a certificate may only publish under the id in
    its own CN, and the failure mode is a silent disconnect — much easier to
    diagnose here than at the track.
    """
    if not scenario.broker.tls:
        print("broker: plain MQTT, no client certificate")
        return

    certs_dir = resolve_path(scenario.broker.certs_dir or "", scenario_path)
    openssl = shutil.which("openssl") if verify_cn else None
    print(f"certificates: {display_path(certs_dir)}/<id>/{{ca.crt,kart.crt,kart.key}}")
    if verify_cn and not openssl:
        print("WARN: openssl not found; the certificate CN is not verified", file=sys.stderr)
    for spawned in scenario.objects:
        paths = tuple(
            os.path.join(certs_dir, spawned.vehicle_id, name)
            for name in ("ca.crt", "kart.crt", "kart.key")
        )
        absent = missing_files(paths)
        if absent:
            raise SystemExit(
                f"ERROR: {spawned.vehicle_id}: certificate file(s) missing: "
                f"{[display_path(item) for item in absent]}\n"
                f"       issue them with aichallenge-aws/cloudformation_templates/"
                f"v2x-mqtt-broker/issue-kart-cert.sh"
            )
        if not openssl:
            continue
        result = subprocess.run(
            [openssl, "x509", "-noout", "-subject", "-in", paths[1]],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            print(
                f"WARN: {spawned.vehicle_id}: cannot read the certificate subject",
                file=sys.stderr,
            )
            continue
        common_name = common_name_of(result.stdout.strip())
        if common_name != spawned.vehicle_id:
            raise SystemExit(
                f"ERROR: {spawned.vehicle_id}: the certificate's CN is '{common_name}'.\n"
                f"       Under the strict ACL it may only publish "
                f"v2x/vehicles/{common_name}/position, so this object would be "
                f"disconnected. Use the '{spawned.vehicle_id}' bundle, deploy the broker "
                f"with --acl-mode open, or rename the object to '{common_name}'."
            )


def print_plan(
    scenario: Scenario, racelines: Dict[str, Raceline], states: Dict[str, ObjectState]
) -> None:
    """Show what will be published, before anything is."""
    transport = "mqtts" if scenario.broker.tls else "mqtt"
    print(
        f"broker: {transport}://{scenario.broker.host}:{scenario.broker.port} "
        f"qos={scenario.broker.qos}  rate={scenario.rate_hz:g} Hz"
    )
    print(f"{'id':<6} {'mode':<9} {'x':>12} {'y':>12} {'z':>7} {'speed':>7}  topic")
    for spawned in scenario.objects:
        state = states[spawned.vehicle_id]
        speed = f"{state.speed_mps:.2f}" if spawned.mode == MODE_RACELINE else "-"
        print(
            f"{spawned.vehicle_id:<6} {spawned.mode:<9} {state.position[0]:>12.2f} "
            f"{state.position[1]:>12.2f} {state.position[2]:>7.2f} {speed:>7}  {spawned.topic}"
        )
    print(
        "\nthe karts must be launched with these ids in their receive routes:\n"
        f"  V2X_VEHICLE_IDS={vehicle_ids_hint(scenario)}"
        "\n  (its own id is excluded automatically; ids outside the list are dropped)\n"
    )


def vehicle_ids_hint(scenario: Scenario) -> str:
    """The V2X_VEHICLE_IDS value that lets a kart receive these objects.

    v2x_kart_position.launch.py generates one receive route per id in that list,
    so an object whose id is missing from it is silently dropped by the kart.
    d1..d4 is the launch default and is kept so a real kart is never lost.
    """
    ids = set(KART_DEFAULT_IDS) | set(scenario.vehicle_ids)

    def natural(vehicle_id: str):
        match = re.fullmatch(r"([^0-9]*)([0-9]+)", vehicle_id)
        return (match.group(1), int(match.group(2))) if match else (vehicle_id, -1)

    return ",".join(sorted(ids, key=natural))


# --- 送信 -------------------------------------------------------------------
class Publisher:
    """Publishes one object's payloads. Subclasses own the transport."""

    def publish(self, payload: str) -> None:
        raise NotImplementedError

    def status(self) -> str:
        return ""

    def close(self) -> None:
        pass


class DryRunPublisher(Publisher):
    def __init__(self, topic: str) -> None:
        self.topic = topic
        self.printed = 0

    def publish(self, payload: str) -> None:
        # 全部出すと 20 Hz × 台数で読めないので 1 秒相当に間引く。
        self.printed += 1
        print(f"{self.topic} {payload}")

    def status(self) -> str:
        return "dry-run"


class PahoPublisher(Publisher):
    """One persistent MQTT connection, reconnecting in the background."""

    def __init__(self, spawned: VirtualObject, scenario: Scenario, certs_dir: str) -> None:
        import paho.mqtt.client as mqtt

        self.topic = spawned.topic
        self.qos = scenario.broker.qos
        client_id = f"v2x-virtual-{spawned.vehicle_id}"
        if hasattr(mqtt, "CallbackAPIVersion"):  # paho-mqtt 2.x
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
        else:  # paho-mqtt 1.x
            self.client = mqtt.Client(client_id=client_id)
        if scenario.broker.tls:
            ca_file, cert_file, key_file = (
                os.path.join(certs_dir, spawned.vehicle_id, name)
                for name in ("ca.crt", "kart.crt", "kart.key")
            )
            self.client.tls_set(ca_certs=ca_file, certfile=cert_file, keyfile=key_file)
        self.client.reconnect_delay_set(min_delay=1, max_delay=8)
        self.client.connect_async(scenario.broker.host, scenario.broker.port, keepalive=15)
        self.client.loop_start()

    def publish(self, payload: str) -> None:
        self.client.publish(self.topic, payload, qos=self.qos)

    def status(self) -> str:
        return "up" if self.client.is_connected() else "down"

    def close(self) -> None:
        # disconnect が先。loop_stop を先に呼ぶとネットワークスレッドの select が
        # タイムアウトするまで join を待ち、台数ぶんの秒数を終了に足してしまう。
        try:
            self.client.disconnect()
        except OSError:
            pass
        self.client.loop_stop()


class MosquittoPubPublisher(Publisher):
    """A long-lived ``mosquitto_pub -l``, fed one payload per line.

    The fallback for a machine without paho-mqtt. ``-l`` keeps a single
    connection open, unlike one process per message.
    """

    def __init__(self, spawned: VirtualObject, scenario: Scenario, certs_dir: str) -> None:
        command = [
            "mosquitto_pub",
            "-h",
            scenario.broker.host,
            "-p",
            str(scenario.broker.port),
            "-t",
            spawned.topic,
            "-q",
            str(scenario.broker.qos),
            "-i",
            f"v2x-virtual-{spawned.vehicle_id}",
            "-k",
            "15",
            "-l",
        ]
        if scenario.broker.tls:
            base = os.path.join(certs_dir, spawned.vehicle_id)
            command += [
                "--cafile",
                os.path.join(base, "ca.crt"),
                "--cert",
                os.path.join(base, "kart.crt"),
                "--key",
                os.path.join(base, "kart.key"),
            ]
        self.vehicle_id = spawned.vehicle_id
        self.process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            command, stdin=subprocess.PIPE, text=True, bufsize=1
        )

    def publish(self, payload: str) -> None:
        if self.process.poll() is not None:
            return
        try:
            self.process.stdin.write(payload + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass

    def status(self) -> str:
        return "up" if self.process.poll() is None else f"exit {self.process.returncode}"

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.process.stdin.close()
            except (BrokenPipeError, ValueError):
                pass
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()


def choose_transport(requested: str) -> str:
    """Pick the MQTT client, preferring paho-mqtt when it is importable."""
    have_paho = False
    try:
        import paho.mqtt.client  # noqa: F401

        have_paho = True
    except ImportError:
        pass

    if requested == TRANSPORT_PAHO:
        if not have_paho:
            raise SystemExit("ERROR: paho-mqtt is not installed. pip3 install paho-mqtt")
        return TRANSPORT_PAHO
    if requested == TRANSPORT_MOSQUITTO:
        if not shutil.which("mosquitto_pub"):
            raise SystemExit("ERROR: mosquitto_pub not found. apt install mosquitto-clients")
        return TRANSPORT_MOSQUITTO
    if have_paho:
        return TRANSPORT_PAHO
    if shutil.which("mosquitto_pub"):
        print("paho-mqtt not installed; falling back to mosquitto_pub")
        return TRANSPORT_MOSQUITTO
    raise SystemExit(
        "ERROR: neither paho-mqtt nor mosquitto_pub is available.\n"
        "       pip3 install paho-mqtt  /  apt install mosquitto-clients"
    )


def build_publishers(
    scenario: Scenario, transport: str, certs_dir: str, dry_run: bool
) -> Dict[str, Publisher]:
    publishers: Dict[str, Publisher] = {}
    for spawned in scenario.objects:
        if dry_run:
            publishers[spawned.vehicle_id] = DryRunPublisher(spawned.topic)
        elif transport == TRANSPORT_PAHO:
            publishers[spawned.vehicle_id] = PahoPublisher(spawned, scenario, certs_dir)
        else:
            publishers[spawned.vehicle_id] = MosquittoPubPublisher(spawned, scenario, certs_dir)
    return publishers


# --- ループ -----------------------------------------------------------------
def run(
    scenario: Scenario,
    racelines: Dict[str, Raceline],
    states: Dict[str, ObjectState],
    publishers: Dict[str, Publisher],
    duration: Optional[float],
    quiet: bool,
) -> int:
    """Publish every object at the scenario rate until stopped."""
    period = 1.0 / scenario.rate_hz
    stopping = {"now": False}

    def request_stop(signum, frame):  # noqa: ARG001
        stopping["now"] = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    started = time.monotonic()
    deadline = started
    next_status = started + 1.0
    sent = 0

    while not stopping["now"]:
        now = time.monotonic()
        if duration is not None and now - started >= duration:
            break

        stamp = datetime.now(timezone.utc)
        for spawned in scenario.objects:
            state = states[spawned.vehicle_id]
            publishers[spawned.vehicle_id].publish(
                build_payload(
                    position=state.position,
                    covariance=spawned.covariance,
                    frame_id=spawned.frame_id,
                    stamp=stamp,
                )
            )
            states[spawned.vehicle_id] = advance(
                spawned, racelines.get(spawned.raceline or ""), state, period
            )
        sent += len(scenario.objects)

        if not quiet and time.monotonic() >= next_status:
            print(status_line(scenario, states, publishers, time.monotonic() - started, sent))
            next_status += 1.0

        # 次の時刻を積算で決める。処理時間ぶんのドリフトを溜めない。
        deadline += period
        sleep_for = deadline - time.monotonic()
        if sleep_for > 0.0:
            time.sleep(sleep_for)
        else:
            # 遅れが溜まったら追いつくのを諦めて基準を今に置き直す。
            deadline = time.monotonic()

    for publisher in publishers.values():
        publisher.close()
    print(f"\nstopped after {time.monotonic() - started:.1f} s, {sent} payload(s) published")
    return 0


def status_line(
    scenario: Scenario,
    states: Dict[str, ObjectState],
    publishers: Dict[str, Publisher],
    elapsed: float,
    sent: int,
) -> str:
    parts = []
    for spawned in scenario.objects:
        state = states[spawned.vehicle_id]
        parts.append(
            f"{spawned.vehicle_id}[{publishers[spawned.vehicle_id].status()}] "
            f"s={state.s_m:6.1f} x={state.position[0]:9.2f} y={state.position[1]:9.2f} "
            f"v={state.speed_mps:4.1f}"
        )
    return f"t={elapsed:6.1f}s sent={sent:<7} " + " | ".join(parts)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    scenario = load_scenario(args.scenario, args)
    racelines = load_racelines(scenario, args.scenario)

    try:
        states = {
            spawned.vehicle_id: initial_state(spawned, racelines.get(spawned.raceline or ""))
            for spawned in scenario.objects
        }
    except ScenarioError as error:
        raise SystemExit(f"ERROR: {error}")

    print_plan(scenario, racelines, states)

    certs_dir = resolve_path(scenario.broker.certs_dir or "", args.scenario)
    if not args.dry_run:
        check_certificates(scenario, args.scenario, verify_cn=not args.skip_cert_check)
    transport = TRANSPORT_AUTO if args.dry_run else choose_transport(args.transport)
    publishers = build_publishers(scenario, transport, certs_dir, args.dry_run)

    return run(scenario, racelines, states, publishers, args.duration, args.quiet)


if __name__ == "__main__":
    sys.exit(main())
