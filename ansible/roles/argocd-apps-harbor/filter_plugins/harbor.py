"""
Filter plugin to patch Harbor helm template output.
Patches hardcoded securityContext values that the chart doesn't expose in values.
"""

import yaml
import json


def harbor_patch_pod_security(helm_output, run_as_user, fs_group=None):
    """
    Patch podSecurityContext (runAsUser, fsGroup) on all Harbor Deployments
    and StatefulSets rendered by helm template.

    Args:
        helm_output: String output from helm template
        run_as_user: UID to set for runAsUser and fsGroup
        fs_group: GID to set for fsGroup (defaults to run_as_user)

    Returns:
        Patched YAML string
    """
    if fs_group is None:
        fs_group = run_as_user

    # Helm output can contain stray tabs which are invalid in YAML
    helm_output = helm_output.replace('\t', '  ')

    documents = list(yaml.safe_load_all(helm_output))

    for doc in documents:
        if not doc:
            continue

        kind = doc.get('kind', '')
        if kind not in ('Deployment', 'StatefulSet'):
            continue

        spec = (doc.get('spec', {})
                   .get('template', {})
                   .get('spec', {}))

        sc = spec.get('securityContext')
        if sc is None:
            continue

        if 'runAsUser' in sc:
            sc['runAsUser'] = run_as_user
        if 'fsGroup' in sc:
            sc['fsGroup'] = fs_group

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
