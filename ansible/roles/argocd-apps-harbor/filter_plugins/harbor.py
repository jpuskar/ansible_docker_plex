"""
Filter plugin to patch Harbor helm template output.
Patches hardcoded securityContext values that the chart doesn't expose in values.
"""

import yaml
import json


def harbor_patch_pod_security(helm_output, run_as_group, components=None):
    """
    Patch securityContext on specific Harbor components to set the container GID.

    NFS ignores fsGroup - the process must actually run with the correct GID.
    This patches runAsGroup in both pod and container securityContexts.

    Args:
        helm_output: String output from helm template
        run_as_group: GID to set for runAsGroup
        components: List of component names to patch (defaults to ['harbor-registry'])

    Returns:
        Patched YAML string
    """
    if components is None:
        components = ['harbor-registry']

    run_as_group = int(run_as_group)

    # Helm output can contain stray tabs which are invalid in YAML
    helm_output = helm_output.replace('\t', '  ')

    documents = list(yaml.safe_load_all(helm_output))

    for doc in documents:
        if not doc:
            continue

        kind = doc.get('kind', '')
        if kind not in ('Deployment', 'StatefulSet'):
            continue

        name = doc.get('metadata', {}).get('name', '')
        if name not in components:
            continue

        pod_spec = (doc.get('spec', {})
                       .get('template', {})
                       .get('spec', {}))

        # Patch pod-level securityContext
        pod_sc = pod_spec.get('securityContext')
        if pod_sc is not None:
            pod_sc['runAsGroup'] = run_as_group
            pod_sc['fsGroup'] = run_as_group

        # Patch each container's securityContext
        for container in pod_spec.get('containers', []):
            csc = container.get('securityContext')
            if csc is None:
                container['securityContext'] = {'runAsGroup': run_as_group}
            else:
                csc['runAsGroup'] = run_as_group

    output_parts = []
    for doc in documents:
        if doc:
            output_parts.append(yaml.dump(doc, default_flow_style=False, sort_keys=False))

    return '---\n' + '---\n'.join(output_parts)


class FilterModule(object):
    """Ansible filter plugin for patching Harbor helm output"""

    def filters(self):
        return {
            'harbor_patch_pod_security': harbor_patch_pod_security,
        }
