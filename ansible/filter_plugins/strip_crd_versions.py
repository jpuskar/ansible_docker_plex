"""
Filter plugin to remove unwanted API versions from specific CRD resources
in a multi-document Kubernetes YAML stream.

Usage in a task:
  content: "{{ raw_yaml | strip_crd_versions({'cdis.cdi.kubevirt.io': ['v1alpha1']}) }}"

The argument is a dict mapping CRD metadata.name to a list of version names
to remove.  CRDs not listed in the dict are left untouched.
"""

import yaml


def strip_crd_versions(raw_yaml, crd_version_map):
    """Remove specified versions from named CRDs in a multi-doc YAML stream."""
    if not crd_version_map:
        return raw_yaml

    # Build {crd_name: set(versions_to_remove)}
    remove_map = {name: set(vers) for name, vers in crd_version_map.items()}

    docs = list(yaml.safe_load_all(raw_yaml))
    for doc in docs:
        if isinstance(doc, dict) and doc.get("kind") == "CustomResourceDefinition":
            crd_name = doc.get("metadata", {}).get("name", "")
            if crd_name in remove_map:
                versions = doc.get("spec", {}).get("versions", [])
                doc["spec"]["versions"] = [
                    v for v in versions if v.get("name") not in remove_map[crd_name]
                ]

    return yaml.safe_dump_all(
        docs,
        default_flow_style=False,
        sort_keys=False,
        explicit_start=True,
    )


class FilterModule:
    def filters(self):
        return {
            "strip_crd_versions": strip_crd_versions,
        }
