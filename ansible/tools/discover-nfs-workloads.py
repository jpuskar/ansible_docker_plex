#!/usr/bin/env python3
"""
Discover Kubernetes workloads that depend on NFS storage.

Finds NFS dependencies via:
  - PVs with spec.nfs (kernel NFS mounts)
  - PVs with NFS CSI driver (nfs.csi.k8s.io)
  - Pods with inline spec.volumes[].nfs mounts (not backed by a PVC)

Walks pod ownerReferences to resolve top-level controllers
(Deployment, StatefulSet, VirtualMachine, DaemonSet, CronJob).

Outputs JSON array with current state for each workload.
Respects KUBECONFIG environment variable.
"""
import json
import sys

from kubernetes import client, config


def load_kube_config():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def get_owner_refs(obj):
    """Return ownerReferences list from a k8s object, or empty list."""
    if hasattr(obj, "metadata") and obj.metadata.owner_references:
        return obj.metadata.owner_references
    return []


def main():
    load_kube_config()
    core = client.CoreV1Api()
    apps = client.AppsV1()
    custom = client.CustomObjectsApi()

    # 1. Identify NFS-backed PVCs from PV list
    nfs_pvcs = set()
    for pv in core.list_persistent_volume().items:
        spec = pv.spec
        is_nfs = spec.nfs is not None
        if spec.csi and spec.csi.driver and "nfs" in spec.csi.driver.lower():
            is_nfs = True
        if is_nfs and spec.claim_ref:
            ns = spec.claim_ref.namespace
            name = spec.claim_ref.name
            if ns and name:
                nfs_pvcs.add(f"{ns}/{name}")

    # 2. Find pods mounting NFS (via matched PVC or direct nfs volume)
    nfs_pods = []
    for pod in core.list_pod_for_all_namespaces().items:
        ns = pod.metadata.namespace
        for vol in (pod.spec.volumes or []):
            if vol.nfs is not None:
                nfs_pods.append(pod)
                break
            if vol.persistent_volume_claim:
                pvc_key = f"{ns}/{vol.persistent_volume_claim.claim_name}"
                if pvc_key in nfs_pvcs:
                    nfs_pods.append(pod)
                    break

    # 3. Resolve each pod to its top-level controller
    seen = set()
    workloads = []
    for pod in nfs_pods:
        ns = pod.metadata.namespace
        owners = get_owner_refs(pod)
        if not owners:
            continue
        kind, name = owners[0].kind, owners[0].name

        # Walk the owner chain to the top-level controller
        if kind == "ReplicaSet":
            try:
                rs = apps.read_namespaced_replica_set(name, ns)
                ro = get_owner_refs(rs)
                if ro and ro[0].kind == "Deployment":
                    kind, name = "Deployment", ro[0].name
            except client.ApiException:
                pass

        elif kind == "VirtualMachineInstance":
            try:
                vmi = custom.get_namespaced_custom_object(
                    "kubevirt.io", "v1", ns, "virtualmachineinstances", name
                )
                vo = vmi.get("metadata", {}).get("ownerReferences", [])
                if vo and vo[0]["kind"] == "VirtualMachine":
                    kind, name = "VirtualMachine", vo[0]["name"]
            except client.ApiException:
                pass

        elif kind == "Job":
            try:
                job = client.BatchV1Api().read_namespaced_job(name, ns)
                jo = get_owner_refs(job)
                if jo and jo[0].kind == "CronJob":
                    kind, name = "CronJob", jo[0].name
            except client.ApiException:
                pass

        type_map = {
            "Deployment": "deploy",
            "StatefulSet": "sts",
            "DaemonSet": "ds",
            "VirtualMachine": "vm",
            "CronJob": "cronjob",
        }
        wtype = type_map.get(kind, kind.lower())
        key = f"{wtype}/{ns}/{name}"
        if key in seen:
            continue
        seen.add(key)

        # 4. Fetch current state of the controller
        entry = {"namespace": ns, "name": name, "type": wtype, "kind": kind}

        if kind == "Deployment":
            try:
                obj = apps.read_namespaced_deployment(name, ns)
                entry["replicas"] = obj.spec.replicas
            except client.ApiException:
                pass

        elif kind == "StatefulSet":
            try:
                obj = apps.read_namespaced_stateful_set(name, ns)
                entry["replicas"] = obj.spec.replicas
            except client.ApiException:
                pass

        elif kind == "VirtualMachine":
            try:
                obj = custom.get_namespaced_custom_object(
                    "kubevirt.io", "v1", ns, "virtualmachines", name
                )
                spec = obj.get("spec", {})
                if "running" in spec:
                    entry["running"] = spec["running"]
                    entry["vm_stop_spec"] = {"running": False}
                    entry["vm_restore_spec"] = {"running": spec["running"]}
                elif "runStrategy" in spec:
                    strategy = spec["runStrategy"]
                    entry["running"] = strategy not in ("Halted", "Stopped")
                    entry["vm_stop_spec"] = {"runStrategy": "Halted"}
                    entry["vm_restore_spec"] = {"runStrategy": strategy}
                else:
                    entry["running"] = False
            except client.ApiException:
                pass

        elif kind == "DaemonSet":
            entry["skip"] = True
            entry["reason"] = "DaemonSets cannot be scaled to zero"

        workloads.append(entry)

    workloads.sort(key=lambda w: (w["namespace"], w["name"]))
    json.dump(workloads, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
