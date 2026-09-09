#!/usr/bin/env python

"""Resolves the LongPlex pools of a SMRT Link run into a pool manifest.

The st2-side half of building the LongPlex pipeline's CSV inputs. This half
does the network work and no filesystem work; scripts/build_longplex_inputs.py
does the filesystem work and no network work, on the cluster. The manifest
this produces is the only thing that crosses between them.

The split is not stylistic. SMRT Link credentials are encrypted st2 datastore
keys (smrtlink.username / smrtlink.password) and must not be shipped to a
processing cluster, while dest_dir and the pool BAMs live on cluster scratch
that the st2 host does not mount. It also happens to be the seam the LongPlex
input spec asks for -- no network in the file-writing path -- which is what
makes the CSV half testable against fixtures.

Two nested barcode layers matter here, and only one is in scope:

  outer  PacBio barcoded SMRTbell adapters (bc1015, bc1016...), one per
         LongPlex pool, declared in the run sheet. SMRT Link demultiplexes
         these itself into one child ConsensusReadSet per pool. That child
         list is exactly what this action reads.
  inner  seqWell i7/i5 indices, one pair per plate well (A01-H12), invisible
         to SMRT Link. Demultiplexed downstream by the LongPlex Nextflow
         pipeline. Nothing here can see them.

This action does not decide *whether* a run is LongPlex -- that routing
happens upstream, from a flag in the sample sheet. It assumes it has been
told.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.smrt_client import SMRTClient  # noqa: E402

from st2common.runners.base_action import Action  # noqa: E402

# The LongPlex pipeline's own constraint on pool_ID (schemas/input_schema.json
# gives "^[a-zA-Z0-9]*$"), tightened to require at least one character.
POOL_ID_RE = re.compile(r"^[A-Za-z0-9]+$")


class ResolveLongplexPoolsError(Exception):
    """Pools could not be resolved from SMRT Link.

    Distinct from the cluster-side LongPlexInputError so a workflow can tell
    an API problem from a bad sample sheet.
    """


class ResolveLongplexPools(Action):
    def _client(self):
        return SMRTClient(
            base_url=self.config.get("base_url"),
            username=self.action_service.get_value(
                "smrtlink.username", local=False, decrypt=True
            ),
            password=self.action_service.get_value(
                "smrtlink.password", local=False, decrypt=True
            ),
            ssl_verify=self.config.get("ssl_verify", True),
        )

    def _pool_id_from_barcode(self, barcode_name):
        """bc1015--bc1015 -> bc1015.

        SMRT Link reports a dnaBarcodeName as the pair of barcoded SMRTbell
        adapters joined by '--', and it is symmetric for the outer barcodes we
        use. Note these are the OUTER adapters, not the inner seqWell i7/i5
        indices that "i7/i5" means everywhere else here. pool_ID forbids '-',
        so the pair has to collapse to one token.

        An asymmetric pair is rejected rather than resolved. Taking the first
        half would silently throw away half the identity, and concatenating
        both would invent an identifier that appears in no run sheet and no
        LIMS record -- and pool_ID prefixes every rename_map key and every
        output filename, so either guess propagates into clinical output
        names. If asymmetric outer barcodes ever become real here, decide
        deliberately what they should be called.
        """
        name = (barcode_name or "").strip()
        if not name:
            raise ResolveLongplexPoolsError("child dataset has an empty dnaBarcodeName")
        halves = name.split("--")
        if len(halves) == 2:
            if halves[0] != halves[1]:
                raise ResolveLongplexPoolsError(
                    "dnaBarcodeName %r is an asymmetric barcode pair; this action "
                    "only derives a pool_ID from a symmetric pair (bcNNNN--bcNNNN). "
                    "Decide what an asymmetric pool should be named before running "
                    "this run." % name
                )
            candidate = halves[0]
        elif len(halves) == 1:
            candidate = halves[0]
        else:
            raise ResolveLongplexPoolsError(
                "dnaBarcodeName %r has %d '--'-separated parts; expected 1 or 2"
                % (name, len(halves))
            )

        if not POOL_ID_RE.match(candidate):
            raise ResolveLongplexPoolsError(
                "pool_ID %r derived from dnaBarcodeName %r is not letters and "
                "digits only, which the LongPlex pipeline requires"
                % (candidate, name)
            )
        return candidate

    def _bam_basename(self, client, dataset_uuid, pool_id):
        """The HiFi BAM's filename for a pool's ConsensusReadSet.

        The dataset's own `path` is the .consensusreadset.xml, not the BAM, so
        the BAM comes out of the DataSet XML's ExternalResources. Not globbed:
        a glob over the dataset directory also matches the .pbi sidecars and,
        on older layouts, scraps BAMs.
        """
        bam_paths = client.get_dataset_bam_paths(dataset_uuid)
        if not bam_paths:
            raise ResolveLongplexPoolsError(
                "pool %s (dataset %s) lists no .bam in its ExternalResources"
                % (pool_id, dataset_uuid)
            )
        if len(bam_paths) > 1:
            raise ResolveLongplexPoolsError(
                "pool %s (dataset %s) lists %d BAMs in its ExternalResources "
                "(%s); a LongPlex pool is expected to be one HiFi BAM, so which "
                "one to demultiplex is not ours to guess"
                % (pool_id, dataset_uuid, len(bam_paths), ", ".join(bam_paths))
            )
        return os.path.basename(bam_paths[0])

    def _pools_for_collection(self, client, collection, warnings):
        """Every pool on one SMRT cell.

        Normally one per outer barcode. A collection with no outer barcode
        declared is a valid single-pool case too, and is handled: it has no
        children, so the collection's own CCS dataset *is* the pool.
        """
        ccs_id = collection.get("ccsId")
        collection_name = collection.get("name") or collection.get("uniqueId")
        if not ccs_id:
            warnings.append(
                "collection %s has no ccsId, so it has no reads to pool; skipped"
                % collection_name
            )
            return []

        children = client.get_child_ccsreads(ccs_id) or []
        if children:
            return [
                {
                    "pool_ID": self._pool_id_from_barcode(child.get("dnaBarcodeName")),
                    "dataset_uuid": child.get("uuid") or child.get("id"),
                    "collection": collection_name,
                }
                for child in children
            ]

        # Undemultiplexed single pool. pool_ID cannot come from the run name:
        # run names carry '_' and '-', which pool_ID forbids, and they collide
        # the moment a run has more than one pool or cell. The well is the
        # only naturally alphanumeric per-collection identifier available;
        # collisions across cells are checked by the caller.
        well = collection.get("well") or collection.get("wellName") or ""
        pool_id = str(well).strip()
        if not POOL_ID_RE.match(pool_id):
            raise ResolveLongplexPoolsError(
                "collection %s has no outer barcode and its well %r cannot be "
                "used as a pool_ID (letters and digits only)"
                % (collection_name, well)
            )
        warnings.append(
            "collection %s has no outer barcode; treating it as a single "
            "undemultiplexed pool named %s after its well"
            % (collection_name, pool_id)
        )
        return [
            {
                "pool_ID": pool_id,
                "dataset_uuid": ccs_id,
                "collection": collection_name,
            }
        ]

    def run(
        self,
        run_id,
        dest_dir,
        barcode_set=None,
        bam_path_overrides=None,
        manifest_path=None,
    ):
        client = self._client()
        overrides = bam_path_overrides or {}
        warnings = []

        run = client.get_run(run_id) or {}
        run_name = run.get("name")
        collections = client.get_run_collections(run_id) or []
        if not collections:
            raise ResolveLongplexPoolsError(
                "run %s has no collections; nothing to resolve" % run_id
            )
        self.logger.info(
            "run %s (%s): %d collection(s)", run_id, run_name, len(collections)
        )

        pools = []
        seen = {}
        for collection in collections:
            for pool in self._pools_for_collection(client, collection, warnings):
                pool_id = pool["pool_ID"]
                if pool_id in seen:
                    raise ResolveLongplexPoolsError(
                        "pool_ID %s is produced by both collection %s and %s. "
                        "pool_ID prefixes every rename_map key and every output "
                        "path, so it has to be unique across the run."
                        % (pool_id, seen[pool_id], pool["collection"])
                    )
                seen[pool_id] = pool["collection"]
                pools.append(pool)

        for pool in pools:
            pool_id = pool["pool_ID"]
            override = overrides.get(pool_id)
            if override:
                # Escape hatch for reprocessing and for a pool whose BAM was
                # staged out of band. Wins over the derived path, and skips
                # the ExternalResources lookup entirely.
                pool["pool_path"] = override
                pool["bam_basename"] = os.path.basename(override)
                warnings.append(
                    "pool %s uses the supplied bam_path_override %s"
                    % (pool_id, override)
                )
            else:
                basename = self._bam_basename(client, pool["dataset_uuid"], pool_id)
                pool["bam_basename"] = basename
                # The compute-side path, not the SMRT Link one: this action
                # runs after the transfer step, and pool_path must point at
                # where the BAM landed.
                pool["pool_path"] = os.path.join(dest_dir, pool_id, basename)
            self.logger.info(
                "pool %s -> %s (dataset %s)",
                pool_id, pool["pool_path"], pool["dataset_uuid"],
            )

        unknown = sorted(set(overrides) - {pool["pool_ID"] for pool in pools})
        if unknown:
            # Fail rather than warn: an override keyed on a pool that does not
            # exist means the caller believes something about this run that is
            # not true, and the pool they meant to redirect is quietly still
            # pointing at the derived path.
            raise ResolveLongplexPoolsError(
                "bam_path_overrides names pool(s) %s which this run does not "
                "have (it has %s)"
                % (", ".join(unknown), ", ".join(sorted(seen)))
            )

        manifest = {
            "run_id": run_id,
            "run_name": run_name,
            "dest_dir": dest_dir,
            "barcode_set": barcode_set or self.config.get(
                "longplex_default_barcode_set", "set1"
            ),
            # Sorted so the manifest, like the CSVs built from it, is
            # byte-identical for identical input.
            "pools": sorted(pools, key=lambda p: p["pool_ID"]),
            "warnings": warnings,
        }

        if manifest_path:
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle, indent=2, sort_keys=True)
                handle.write("\n")
            self.logger.info("wrote pool manifest to %s", manifest_path)

        for warning in warnings:
            self.logger.info("warning: %s", warning)

        return (True, {"manifest": manifest, "manifest_path": manifest_path})
