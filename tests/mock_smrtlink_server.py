"""A fake SMRT Link for end-to-end testing of
sensor -> ductus.pacbio_run_complete -> rules/on_run_complete_transfer ->
ductus.transfer_bams_to_cluster.

Serves the endpoints that sensors/smrtlink_sensor.py and lib/smrt_client.py
actually call:

    POST /token
    GET  /SMRTLink/1.0.0/status
    GET  /SMRTLink/1.0.0/smrt-link/runs
    GET  /SMRTLink/1.0.0/smrt-link/runs/<run_uuid>
    GET  /SMRTLink/1.0.0/smrt-link/runs/<run_uuid>/collections
    GET  /SMRTLink/1.0.0/smrt-link/runs/<run_uuid>/collections/<uuid>/barcodes
    GET  /SMRTLink/1.0.0/smrt-link/datasets/ccsreads/<ccs_id>

The run it serves is shaped to land on pacbio_run_complete (not the
native-demux trigger): numBarcodes 0, no multiJobId, no barcodes.

Every start serves a NEW run -- fresh uniqueId, a run name stamped with the
start time, createdAt = now -- so restarting the mock gives the sensor
something it has not triggered yet. (The sensor dedups on uniqueId in the st2
datastore key smrtlink.run_state and ignores runs created more than 30 days
ago, which is why the old fixed-date mock could never fire.)

Unless --no-seed is given, it also writes dummy HiFi BAMs where each
collection's collectionPathUri points, laid out as Revio does:

    <data-root>/<run_name>/<n>_<well>/hifi_reads/<movie>.hifi_reads.bam

That directory must be readable from the st2 host, since transfer_bams
resolves collection_paths on its own filesystem.

Usage (needs Flask: pip install flask):

    python3 tests/mock_smrtlink_server.py \\
        --data-root /tmp/smrtlink-mock/data --complete-after 120

Then point the pack's base_url at http://<this host>:9999 and set
smrtlink.username / smrtlink.password in the datastore to anything.
"""

import argparse
import os
import time
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request

FAKE_TOKEN = "fake-token-abc123"

app = Flask(__name__)
STATE = {}


def _build_run(run_name, data_root, wells, complete_after):
    now = datetime.now(timezone.utc)
    run_uuid = str(uuid.uuid4())
    stamp = now.strftime("%y%m%d_%H%M%S")
    collections = []
    for i, well in enumerate(wells, start=1):
        movie = "m84001_%s_s%d" % (stamp, i)
        collections.append({
            "name": "%s_%s" % (run_name, well),
            "uniqueId": str(uuid.uuid4()),
            "well": well,
            "context": movie,
            "collectionPathUri": os.path.join(data_root, run_name, "%d_%s" % (i, well)),
            "ccsId": str(uuid.uuid4()),
            "numBarcodes": 0,
            "movieMinutes": 1440,
        })
    return {
        "run": {
            "name": run_name,
            "uniqueId": run_uuid,
            "createdAt": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "summary": "Mock run served by tests/mock_smrtlink_server.py",
            "multiJobId": None,
        },
        "collections": collections,
        "complete_at": time.monotonic() + complete_after,
    }


def _seed_bams(collections, size_mb):
    chunk = b"\0" * (1024 * 1024)
    for c in collections:
        out_dir = os.path.join(c["collectionPathUri"], "hifi_reads")
        os.makedirs(out_dir, exist_ok=True)
        bam = os.path.join(out_dir, "%s.hifi_reads.bam" % c["context"])
        with open(bam, "wb") as fh:
            for _ in range(size_mb):
                fh.write(chunk)
        print("seeded %s (%d MB)" % (bam, size_mb))


def _authorized():
    return request.headers.get("Authorization") == "Bearer %s" % FAKE_TOKEN


@app.before_request
def _check_auth():
    if request.path.startswith("/SMRTLink/") and not _authorized():
        return jsonify({"message": "unauthorized"}), 401
    return None


def _run_or_404(run_uuid):
    run = STATE["run"]
    return run if run["uniqueId"] == run_uuid else None


def _collection_status():
    return "Complete" if time.monotonic() >= STATE["complete_at"] else "Collecting"


@app.post("/token")
def token():
    return jsonify({"access_token": FAKE_TOKEN, "expires_in": 7200})


@app.get("/SMRTLink/1.0.0/status")
def status():
    return jsonify({"version": "25.1-mock", "user": "testuser"})


@app.get("/SMRTLink/1.0.0/smrt-link/runs")
def runs():
    return jsonify([STATE["run"]])


@app.get("/SMRTLink/1.0.0/smrt-link/runs/<run_uuid>")
def run_details(run_uuid):
    run = _run_or_404(run_uuid)
    if run is None:
        return jsonify({"message": "no such run"}), 404
    return jsonify(run)


@app.get("/SMRTLink/1.0.0/smrt-link/runs/<run_uuid>/collections")
def collections(run_uuid):
    if _run_or_404(run_uuid) is None:
        return jsonify({"message": "no such run"}), 404
    status_now = _collection_status()
    return jsonify([dict(c, status=status_now) for c in STATE["collections"]])


@app.get("/SMRTLink/1.0.0/smrt-link/runs/<run_uuid>/collections/<collection_uuid>/barcodes")
def barcodes(run_uuid, collection_uuid):
    if _run_or_404(run_uuid) is None:
        return jsonify({"message": "no such run"}), 404
    return jsonify([])


@app.get("/SMRTLink/1.0.0/smrt-link/datasets/ccsreads/<ccs_id>")
def ccsreads(ccs_id):
    return jsonify({"uuid": ccs_id, "numChildren": 0})


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address; 0.0.0.0 if st2 runs elsewhere (default %(default)s)")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--data-root", default="/tmp/smrtlink-mock/data",
                        help="where collectionPathUri points and BAMs are seeded (default %(default)s)")
    parser.add_argument("--run-name", default=None,
                        help="default: mock_run_<YYYYmmdd_HHMMSS>, unique per start")
    parser.add_argument("--wells", default="A01",
                        help="comma-separated wells, one collection each (default %(default)s)")
    parser.add_argument("--complete-after", type=int, default=0,
                        help="seconds the collections report Collecting before Complete (default %(default)s)")
    parser.add_argument("--bam-size-mb", type=int, default=5)
    parser.add_argument("--no-seed", action="store_true", help="do not write dummy BAMs")
    args = parser.parse_args()

    run_name = args.run_name or datetime.now().strftime("mock_run_%Y%m%d_%H%M%S")
    wells = [w.strip() for w in args.wells.split(",") if w.strip()]
    STATE.update(_build_run(run_name, os.path.abspath(args.data_root), wells, args.complete_after))
    if not args.no_seed:
        _seed_bams(STATE["collections"], args.bam_size_mb)

    print("serving run %s (%s), %d collection(s), Complete in %ds"
          % (run_name, STATE["run"]["uniqueId"], len(wells), args.complete_after))
    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
