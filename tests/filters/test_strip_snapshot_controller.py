"""
Tests for the strip_snapshot_controller filter plugin.

The filter removes the 'snapshot-controller' container from the rawfile-localpv
node DaemonSet while preserving all other containers (csi-driver,
node-driver-registrar, external-provisioner, external-snapshotter).
"""

import os
import sys

import pytest
import yaml

# Import the filter plugin from the role
my_dir = os.path.dirname(os.path.realpath(__file__))
repo_root = os.path.dirname(os.path.dirname(my_dir))
sys.path.insert(0, os.path.join(
    repo_root, 'ansible', 'roles', 'argocd-apps-openebs-rawfile', 'filter_plugins'
))
from strip_snapshot_controller import strip_snapshot_controller  # noqa: E402


# --- Fixtures ---

DAEMONSET_WITH_SNAPSHOT_CONTROLLER = """\
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: rawfile-localpv-node
  namespace: openebs-rawfile
spec:
  selector:
    matchLabels:
      component: node
  template:
    metadata:
      labels:
        component: node
    spec:
      containers:
        - name: csi-driver
          image: docker.io/openebs/rawfile-localpv:v0.14.1
          args:
            - csi-driver
        - name: node-driver-registrar
          image: registry.k8s.io/sig-storage/csi-node-driver-registrar:v2.13.0
          args:
            - --csi-address=$(ADDRESS)
        - name: external-provisioner
          image: registry.k8s.io/sig-storage/csi-provisioner:v5.2.0
          args:
            - "--node-deployment=true"
        - name: external-snapshotter
          image: registry.k8s.io/sig-storage/csi-snapshotter:v8.2.1
          args:
            - "--csi-address=$(ADDRESS)"
            - "--node-deployment=true"
        - name: snapshot-controller
          image: registry.k8s.io/sig-storage/snapshot-controller:v8.2.1
          args:
            - "--v=2"
            - "--enable-distributed-snapshotting=true"
"""

SERVICE_ACCOUNT = """\
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: rawfile-localpv-driver
  namespace: openebs-rawfile
"""

CLUSTER_ROLE = """\
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: rawfile-localpv-snapshotter
rules:
  - apiGroups: ["snapshot.storage.k8s.io"]
    resources: ["volumesnapshotcontents"]
    verbs: ["create", "get", "list", "watch", "update", "delete", "patch"]
"""

MULTI_DOC_INPUT = SERVICE_ACCOUNT + DAEMONSET_WITH_SNAPSHOT_CONTROLLER + CLUSTER_ROLE


# --- Tests ---

class TestStripSnapshotController:
    """Tests for strip_snapshot_controller filter."""

    def test_removes_snapshot_controller_container(self):
        result = strip_snapshot_controller(DAEMONSET_WITH_SNAPSHOT_CONTROLLER)
        docs = list(yaml.safe_load_all(result))
        ds = next(d for d in docs if d and d.get('kind') == 'DaemonSet')
        container_names = [c['name'] for c in ds['spec']['template']['spec']['containers']]
        assert 'snapshot-controller' not in container_names

    def test_preserves_external_snapshotter(self):
        result = strip_snapshot_controller(DAEMONSET_WITH_SNAPSHOT_CONTROLLER)
        docs = list(yaml.safe_load_all(result))
        ds = next(d for d in docs if d and d.get('kind') == 'DaemonSet')
        container_names = [c['name'] for c in ds['spec']['template']['spec']['containers']]
        assert 'external-snapshotter' in container_names

    def test_preserves_csi_driver(self):
        result = strip_snapshot_controller(DAEMONSET_WITH_SNAPSHOT_CONTROLLER)
        docs = list(yaml.safe_load_all(result))
        ds = next(d for d in docs if d and d.get('kind') == 'DaemonSet')
        container_names = [c['name'] for c in ds['spec']['template']['spec']['containers']]
        assert 'csi-driver' in container_names

    def test_preserves_node_driver_registrar(self):
        result = strip_snapshot_controller(DAEMONSET_WITH_SNAPSHOT_CONTROLLER)
        docs = list(yaml.safe_load_all(result))
        ds = next(d for d in docs if d and d.get('kind') == 'DaemonSet')
        container_names = [c['name'] for c in ds['spec']['template']['spec']['containers']]
        assert 'node-driver-registrar' in container_names

    def test_preserves_external_provisioner(self):
        result = strip_snapshot_controller(DAEMONSET_WITH_SNAPSHOT_CONTROLLER)
        docs = list(yaml.safe_load_all(result))
        ds = next(d for d in docs if d and d.get('kind') == 'DaemonSet')
        container_names = [c['name'] for c in ds['spec']['template']['spec']['containers']]
        assert 'external-provisioner' in container_names

    def test_expected_container_count(self):
        """After removal, should have 4 containers (was 5)."""
        result = strip_snapshot_controller(DAEMONSET_WITH_SNAPSHOT_CONTROLLER)
        docs = list(yaml.safe_load_all(result))
        ds = next(d for d in docs if d and d.get('kind') == 'DaemonSet')
        containers = ds['spec']['template']['spec']['containers']
        assert len(containers) == 4

    def test_multi_document_preserves_other_resources(self):
        """Non-DaemonSet documents should pass through unchanged."""
        result = strip_snapshot_controller(MULTI_DOC_INPUT)
        docs = [d for d in yaml.safe_load_all(result) if d]
        kinds = [d['kind'] for d in docs]
        assert 'ServiceAccount' in kinds
        assert 'ClusterRole' in kinds
        assert 'DaemonSet' in kinds
        assert len(docs) == 3

    def test_non_rawfile_daemonset_untouched(self):
        """DaemonSets that aren't rawfile-localpv-node should not be modified."""
        other_ds = """\
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: some-other-daemonset
spec:
  template:
    spec:
      containers:
        - name: snapshot-controller
          image: registry.k8s.io/sig-storage/snapshot-controller:v8.2.1
        - name: main
          image: example.com/main:latest
"""
        result = strip_snapshot_controller(other_ds)
        docs = [d for d in yaml.safe_load_all(result) if d]
        ds = docs[0]
        container_names = [c['name'] for c in ds['spec']['template']['spec']['containers']]
        # snapshot-controller should still be present since this isn't rawfile
        assert 'snapshot-controller' in container_names

    def test_idempotent_when_no_snapshot_controller(self):
        """If snapshot-controller is already absent, should not error."""
        no_sc = """\
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: rawfile-localpv-node
spec:
  template:
    spec:
      containers:
        - name: csi-driver
          image: docker.io/openebs/rawfile-localpv:v0.14.1
        - name: external-snapshotter
          image: registry.k8s.io/sig-storage/csi-snapshotter:v8.2.1
"""
        result = strip_snapshot_controller(no_sc)
        docs = [d for d in yaml.safe_load_all(result) if d]
        ds = docs[0]
        container_names = [c['name'] for c in ds['spec']['template']['spec']['containers']]
        assert container_names == ['csi-driver', 'external-snapshotter']

    def test_output_is_valid_yaml(self):
        result = strip_snapshot_controller(MULTI_DOC_INPUT)
        # Should not raise
        docs = list(yaml.safe_load_all(result))
        assert all(isinstance(d, (dict, type(None))) for d in docs)

    def test_output_starts_with_doc_separator(self):
        result = strip_snapshot_controller(DAEMONSET_WITH_SNAPSHOT_CONTROLLER)
        assert result.startswith('---\n')
