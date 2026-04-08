"""
Filter plugin to patch UniFi helm template output.
Replaces hardcoded MongoDB env vars (DB_URI, STATDB_URI) with
valueFrom.secretKeyRef references to the ESO-managed secret.
"""

import yaml


def patch_unifi_mongodb_env(helm_output, secret_name):
    """
    Patch the UniFi Deployment to use secretKeyRef for DB_URI and STATDB_URI
    instead of inline values.

    Args:
        helm_output: String output from helm template
        secret_name: Name of the K8s Secret containing DB_URI and STATDB_URI keys

    Returns:
        Patched YAML string
    """
    env_to_secret_key = {
        'DB_URI': 'DB_URI',
        'STATDB_URI': 'STATDB_URI',
    }

    helm_output = helm_output.replace('\t', '  ')
    documents = list(yaml.safe_load_all(helm_output))

    for doc in documents:
        if not doc:
            continue

        if doc.get('kind') != 'Deployment':
            continue

        pod_spec = (doc.get('spec', {})
                       .get('template', {})
                       .get('spec', {}))

        for container in pod_spec.get('containers', []):
            env_list = container.get('env', [])
            for env_var in env_list:
                name = env_var.get('name', '')
                if name in env_to_secret_key:
                    env_var.pop('value', None)
                    env_var['valueFrom'] = {
                        'secretKeyRef': {
                            'name': secret_name,
                            'key': env_to_secret_key[name],
                        }
                    }

    output_parts = []
    for doc in documents:
        if doc:
            output_parts.append(
                yaml.dump(doc, default_flow_style=False, sort_keys=False)
            )

    return '---\n' + '---\n'.join(output_parts)


class FilterModule(object):
    """Ansible filter plugin for patching UniFi helm output"""

    def filters(self):
        return {
            'patch_unifi_mongodb_env': patch_unifi_mongodb_env,
        }
