"""
Filter plugin to patch UniFi helm template output.

1. Replaces hardcoded MongoDB env vars (DB_URI, STATDB_URI) with
   valueFrom.secretKeyRef references to the ESO-managed secret.
2. Injects a python-slim sidecar that stubs the ULP manifest endpoint
   on 127.0.0.1:9080 to suppress log spam from UniFi >= 9.4.19.
"""

import yaml


ULP_STUB_SIDECAR = {
    'name': 'ulp-stub',
    'image': 'python:3.12-slim',
    'command': ['python', '/scripts/ulp-stub.py'],
    'volumeMounts': [
        {
            'name': 'ulp-stub-script',
            'mountPath': '/scripts',
            'readOnly': True,
        }
    ],
    'resources': {
        'requests': {'cpu': '1m', 'memory': '16Mi'},
        'limits': {'cpu': '10m', 'memory': '32Mi'},
    },
    'securityContext': {
        'runAsUser': 65534,
        'runAsGroup': 65534,
        'runAsNonRoot': True,
        'allowPrivilegeEscalation': False,
        'readOnlyRootFilesystem': True,
    },
}

ULP_STUB_VOLUME = {
    'name': 'ulp-stub-script',
    'configMap': {
        'name': 'ulp-stub-script',
        'defaultMode': 0o444,
    },
}


def patch_unifi_mongodb_env(helm_output, secret_name):
    """
    Patch the UniFi Deployment:
    - Replace DB_URI/STATDB_URI with secretKeyRef from ESO secret
    - Inject ULP stub sidecar + volume

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

        # Patch MongoDB env vars to use secretKeyRef
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

        # Inject ULP stub sidecar
        containers = pod_spec.setdefault('containers', [])
        containers.append(ULP_STUB_SIDECAR.copy())

        # Inject ConfigMap volume
        volumes = pod_spec.setdefault('volumes', [])
        volumes.append(ULP_STUB_VOLUME.copy())

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
