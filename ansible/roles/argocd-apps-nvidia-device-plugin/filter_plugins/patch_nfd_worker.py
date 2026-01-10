"""
Filter plugin to patch NVIDIA Device Plugin helm output
Adds nodeSelector to the NFD worker daemonset
"""

import yaml


def patch_nfd_worker_nodeSelector(helm_output, node_selector):
    """
    Process helm template output and add nodeSelector to NFD worker daemonset.

    Args:
        helm_output: String output from helm template command (normalized by yq)
        node_selector: Dict of nodeSelector labels to add

    Returns:
        String with modified YAML
    """
    # Parse the YAML documents (should be normalized by yq already)
    documents = list(yaml.safe_load_all(helm_output))

    # Find and patch the NFD worker daemonset
    for doc in documents:
        if (doc and
            doc.get('kind') == 'DaemonSet' and
            doc.get('metadata', {}).get('name', '').endswith('node-feature-discovery-worker')):

            # Add nodeSelector to spec.template.spec
            if 'spec' in doc and 'template' in doc['spec'] and 'spec' in doc['spec']['template']:
                template_spec = doc['spec']['template']['spec']
                if 'nodeSelector' not in template_spec:
                    template_spec['nodeSelector'] = {}
                template_spec['nodeSelector'].update(node_selector)

    # Convert back to YAML string
    output_parts = []
    for doc in documents:
        if doc:  # Skip None/empty documents
            output_parts.append(yaml.dump(doc, default_flow_style=False, sort_keys=False))

    return '---\n'.join(output_parts)


class FilterModule(object):
    """Ansible filter module for patching NFD worker"""

    def filters(self):
        return {
            'patch_nfd_worker_nodeSelector': patch_nfd_worker_nodeSelector
        }
