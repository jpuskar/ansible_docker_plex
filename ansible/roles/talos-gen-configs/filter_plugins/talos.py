"""
Custom Ansible filter to patch the HostnameConfig document in a
multi-document Talos machine config YAML.

Finds the document with 'kind: HostnameConfig', removes 'auto: stable',
and sets 'hostname: <desired_hostname>'.
"""

import yaml


def patch_hostname_config(raw_yaml, hostname):
    """
    Patch the HostnameConfig document in a multi-document YAML string.

    Args:
        raw_yaml: String containing one or more YAML documents (--- separated)
        hostname: The hostname to set in the HostnameConfig document

    Returns:
        The patched multi-document YAML string
    """
    documents = list(yaml.safe_load_all(raw_yaml))
    patched = False

    for doc in documents:
        if not isinstance(doc, dict):
            continue
        if doc.get('kind') == 'HostnameConfig':
            doc.pop('auto', None)
            doc['hostname'] = hostname
            patched = True

    if not patched:
        raise ValueError(
            "No document with 'kind: HostnameConfig' found in the config"
        )

    # Re-serialize all documents back to multi-document YAML
    parts = []
    for doc in documents:
        parts.append(yaml.dump(doc, default_flow_style=False).rstrip())
    return '---\n' + '\n---\n'.join(parts) + '\n'


class FilterModule(object):
    """Ansible filter module for patching HostnameConfig"""

    def filters(self):
        return {
            'patch_hostname_config': patch_hostname_config,
        }
