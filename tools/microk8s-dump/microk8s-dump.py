#!/usr/bin/env python3
"""
Dump all Kubernetes resources (including CRDs) to YAML files.
Uses the kubernetes Python client for API access.
Validates each dump is parseable YAML with the expected structure.
"""

import json
import sys
import tarfile
from datetime import datetime
from pathlib import Path

import yaml
from kubernetes import client, config
from kubernetes.dynamic import DynamicClient
from kubernetes.dynamic.exceptions import (
    ForbiddenError,
    NotFoundError,
    ResourceNotFoundError,
)


def load_k8s_client() -> tuple[client.ApiClient, DynamicClient]:
    """Load kubeconfig and return both raw and dynamic clients."""
    try:
        config.load_kube_config()
    except config.ConfigException:
        config.load_incluster_config()
    api_client = client.ApiClient()
    dyn = DynamicClient(api_client)
    return api_client, dyn


def _call_api(api_client: client.ApiClient, path: str) -> dict:
    """Call a raw API path and return parsed JSON."""
    resp = api_client.call_api(
        path, "GET",
        response_type="str",
        auth_settings=["BearerToken"],
        _preload_content=False,
    )
    return json.loads(resp[0].data)


def discover_resources(api_client: client.ApiClient, namespaced: bool) -> list[dict]:
    """
    Discover all listable API resource types via the discovery API.
    Returns list of dicts with keys: name, kind, api_version.
    """
    resources = []

    def _collect(api_version: str, resource_list: list[dict]) -> None:
        for r in resource_list:
            if "/" in r["name"]:  # skip subresources
                continue
            if "list" not in r.get("verbs", []):
                continue
            if r.get("namespaced", False) != namespaced:
                continue
            resources.append({
                "name": r["name"],
                "kind": r["kind"],
                "api_version": api_version,
            })

    # Core API (v1)
    try:
        core = _call_api(api_client, "/api/v1")
        _collect("v1", core.get("resources", []))
    except Exception as exc:
        print(f"WARNING: Failed to discover core/v1 resources: {exc}", file=sys.stderr)

    # Named API groups — use each group's preferred version
    try:
        groups = _call_api(api_client, "/apis")
    except Exception as exc:
        print(f"ERROR: Failed to discover API groups: {exc}", file=sys.stderr)
        sys.exit(1)

    for group in groups.get("groups", []):
        gv = group["preferredVersion"]["groupVersion"]
        try:
            gv_data = _call_api(api_client, f"/apis/{gv}")
            _collect(gv, gv_data.get("resources", []))
        except Exception as exc:
            print(f"  WARNING: Failed to discover resources for {gv}: {exc}", file=sys.stderr)

    if not resources:
        scope = "namespaced" if namespaced else "cluster-scoped"
        print(f"WARNING: No {scope} resources found", file=sys.stderr)

    return resources


def validate_items(items: list, resource_name: str) -> list:
    """Validate that the items list is well-formed."""
    if items is None:
        raise ValueError(f"items is None for {resource_name}")
    if not isinstance(items, list):
        raise ValueError(f"Expected items to be a list for {resource_name}, got {type(items).__name__}")
    return items


def resource_list_to_yaml(items: list, resource_info: dict) -> str:
    """Convert a list of resource dicts to a Kubernetes-style List YAML document."""
    list_doc = {
        "apiVersion": "v1",
        "kind": f"{resource_info['kind']}List",
        "metadata": {"resourceVersion": ""},
        "items": [
            item.to_dict() if hasattr(item, "to_dict") else dict(item)
            for item in items
        ],
    }
    return yaml.dump(list_doc, default_flow_style=False, allow_unicode=True)


def dump_resources(
    dyn: DynamicClient,
    resources: list[dict],
    outdir: Path,
    namespaced: bool,
) -> tuple[int, int, int]:
    """Dump a list of resource types to YAML files. Returns (success, empty, error) counts."""
    success = 0
    empty = 0
    errors = 0
    scope = "namespaced" if namespaced else "cluster"

    for res_info in resources:
        name = res_info["name"]
        api_version = res_info["api_version"]
        kind = res_info["kind"]
        safe_name = f"{api_version.replace('/', '_')}__{name}" if "/" in api_version else name
        outfile = outdir / f"{safe_name}.yaml"

        # Resolve the resource via DynamicClient
        try:
            resource = dyn.resources.get(api_version=api_version, kind=kind)
        except Exception as exc:
            print(f"  SKIP {scope}/{name} ({api_version}): could not resolve resource: {exc}")
            errors += 1
            continue

        try:
            result = resource.get()
        except (ForbiddenError, NotFoundError, ResourceNotFoundError) as exc:
            print(f"  SKIP {scope}/{name}: {type(exc).__name__}: {exc}")
            errors += 1
            continue
        except Exception as exc:
            print(f"  SKIP {scope}/{name}: {type(exc).__name__}: {exc}")
            errors += 1
            continue

        # Extract items from the ResourceList
        raw_items = getattr(result, "items", None)
        if raw_items is None:
            # Some resources return the list at the attribute level
            raw_items = result.get("items", []) if hasattr(result, "get") else []

        try:
            items = validate_items(raw_items, name)
        except ValueError as exc:
            print(f"  FAIL {scope}/{name}: {exc}")
            errors += 1
            continue

        # Serialize to YAML
        try:
            yaml_content = resource_list_to_yaml(items, res_info)
        except Exception as exc:
            print(f"  FAIL {scope}/{name}: serialization error: {exc}")
            errors += 1
            continue

        # Validate the YAML we just produced is parseable
        try:
            roundtrip = yaml.safe_load(yaml_content)
            if not isinstance(roundtrip, dict) or "items" not in roundtrip:
                raise ValueError("roundtrip validation failed: missing structure")
        except Exception as exc:
            print(f"  FAIL {scope}/{name}: YAML roundtrip check failed: {exc}")
            errors += 1
            continue

        outfile.write_text(yaml_content)
        item_count = len(items)

        if item_count == 0:
            print(f"    OK {scope}/{name}: 0 items (empty list)")
            empty += 1
        else:
            print(f"    OK {scope}/{name}: {item_count} items")
            success += 1

    return success, empty, errors


def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = Path(f"./k8s-full-dump-{timestamp}")

    ns_dir = outdir / "namespaced"
    cl_dir = outdir / "cluster"
    ns_dir.mkdir(parents=True, exist_ok=True)
    cl_dir.mkdir(parents=True, exist_ok=True)

    # Load client and verify connectivity
    print("Connecting to cluster...")
    try:
        api_client, dyn = load_k8s_client()
        v1 = client.VersionApi()
        version_info = v1.get_code()
        print(f"Connected to cluster: {version_info.git_version}")
    except Exception as exc:
        print(f"ERROR: Cannot reach cluster: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Discovering API resources...")
    ns_resources = discover_resources(api_client, namespaced=True)
    cl_resources = discover_resources(api_client, namespaced=False)
    total = len(ns_resources) + len(cl_resources)
    print(f"Found {len(ns_resources)} namespaced + {len(cl_resources)} cluster-scoped = {total} resource types\n")

    print("=== Namespaced resources ===")
    ns_ok, ns_empty, ns_err = dump_resources(dyn, ns_resources, ns_dir, namespaced=True)

    print("\n=== Cluster-scoped resources ===")
    cl_ok, cl_empty, cl_err = dump_resources(dyn, cl_resources, cl_dir, namespaced=False)

    # Summary
    total_ok = ns_ok + cl_ok
    total_empty = ns_empty + cl_empty
    total_err = ns_err + cl_err

    print(f"\n{'=' * 50}")
    print("SUMMARY:")
    print(f"  With resources : {total_ok}")
    print(f"  Empty lists    : {total_empty}")
    print(f"  Errors/skipped : {total_err}")
    print(f"  Total          : {total_ok + total_empty + total_err}")
    print(f"{'=' * 50}")

    # Create tarball
    tarball = f"{outdir}.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(str(outdir), arcname=outdir.name)
    print(f"\nArchive: {tarball}")
    print(f"Directory: {outdir}")

    if total_err > 0:
        print(f"\nWARNING: {total_err} resources had errors (see above)", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
