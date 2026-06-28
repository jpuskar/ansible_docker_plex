"""
Filter plugin to remove the snapshot-controller container from rawfile-localpv
DaemonSet.

We deploy snapshot-controller as a separate cluster-wide Deployment with
leader-election, rather than having one per node in the rawfile DaemonSet.
The per-node csi-snapshotter (external-snapshotter) sidecar is kept.

See: https://github.com/kubernetes-csi/external-snapshotter
      https://github.com/openebs/rawfile-localpv/pull/284
"""

import yaml


def strip_snapshot_controller(helm_output):
    """
    Remove the snapshot-controller container from rawfile-localpv DaemonSet.

    Args:
        helm_output: String output from helm template command

    Returns:
        String with modified YAML (snapshot-controller container removed)
    """
    documents = list(yaml.safe_load_all(helm_output))

    for doc in documents:
        if not doc:
            continue
        if (doc.get('kind') == 'DaemonSet' and
                'rawfile-localpv' in doc.get('metadata', {}).get('name', '') and
                doc.get('metadata', {}).get('name', '').endswith('-node')):

            containers = (doc.get('spec', {})
                          .get('template', {})
                          .get('spec', {})
                          .get('containers', []))

            # Remove only the snapshot-controller container; keep external-snapshotter
            doc['spec']['template']['spec']['containers'] = [
                c for c in containers
                if c.get('name') != 'snapshot-controller'
            ]

    # Re-serialize
    output_parts = []
    for doc in documents:
        if doc is None:
            continue
        output_parts.append(yaml.dump(doc, default_flow_style=False, sort_keys=False))

    return '---\n' + '\n---\n'.join(output_parts)


class FilterModule(object):
    def filters(self):
        return {
            'strip_snapshot_controller': strip_snapshot_controller
        }
