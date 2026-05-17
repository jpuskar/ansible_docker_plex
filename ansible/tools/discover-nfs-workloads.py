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
import subprocess
import sys


def kubectl(*args):
    cmd = ["kubectl"] + list(args) + ["-o", "json"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return json.loads(r.stdout)


def main():
    # 1. Identify NFS-backed PVCs from PV list
    pvs = kubectl("get", "pv")
    nfs_pvcs = set()
    for pv in (pvs or {}).get("items", []):
        spec = pv.get("spec", {})
        is_nfs = "nfs" in spec
        if "nfs" in spec.get("csi", {}).get("driver", "").lower():
            is_nfs = True
        if is_nfs:
            ref = spec.get("claimRef", {})
            if ref.get("namespace") and ref.get("name"):
                nfs_pvcs.add(f"{ref['namespace']}/{ref['name']}")

    # 2. Find pods mounting NFS (via matched PVC or direct nfs volume)
    pods = kubectl("get", "pods", "-A")
    nfs_pods = []
    for pod in (pods or {}).get("items", []):
        ns = pod["metadata"]["namespace"]
        for vol in pod.get("spec", {}).get("volumes", []):
            if "nfs" in vol:
                nfs_pods.append(pod)
                break
            pvc = vol.get("persistentVolumeClaim", {})
            if pvc and f"{ns}/{pvc.get('claimName', '')}" in nfs_pvcs:
                nfs_pods.append(pod)
                break

    # 3. Resolve each pod to its top-level controller
    seen = set()
    workloads = []
    for pod in nfs_pods:
        ns = pod["metadata"]["namespace"]
        owners = pod["metadata"].get("ownerReferences", [])
        if not owners:
            continue
        kind, name = owners[0]["kind"], owners[0]["name"]

        # Walk the owner chain to the top-level controller
        if kind == "ReplicaSet":
            rs = kubectl("get", "replicaset", name, "-n", ns)
            if rs:
                ro = rs.get("metadata", {}).get("ownerReferences", [])
                if ro and ro[0]["kind"] == "Deployment":
                    kind, name = "Deployment", ro[0]["name"]

        elif kind == "VirtualMachineInstance":
            vmi = kubectl("get", "virtualmachineinstances.kubevirt.io", name, "-n", ns)
            if vmi:
                vo = vmi.get("metadata", {}).get("ownerReferences", [])
                if vo and vo[0]["kind"] == "VirtualMachine":
                    kind, name = "VirtualMachine", vo[0]["name"]

        elif kind == "Job":
            job = kubectl("get", "job", name, "-n", ns)
            if job:
                jo = job.get("metadata", {}).get("ownerReferences", [])
                if jo and jo[0]["kind"] == "CronJob":
                    kind, name = "CronJob", jo[0]["name"]

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

        if kind in ("Deployment", "StatefulSet"):
            api_kind = "deployment" if kind == "Deployment" else "statefulset"
            obj = kubectl("get", api_kind, name, "-n", ns)
            if obj:
                entry["replicas"] = obj["spec"]["replicas"]

        elif kind == "VirtualMachine":
            obj = kubectl("get", "virtualmachines.kubevirt.io", name, "-n", ns)
            if obj:
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

        elif kind == "DaemonSet":
            entry["skip"] = True
            entry["reason"] = "DaemonSets cannot be scaled to zero"

        workloads.append(entry)

    workloads.sort(key=lambda w: (w["namespace"], w["name"]))
    json.dump(workloads, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
