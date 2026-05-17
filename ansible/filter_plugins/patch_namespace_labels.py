"""
Filter plugin to merge labels into the Namespace resource inside a
multi-document Kubernetes YAML stream.

Usage in a task:
  content: "{{ lookup('file', ...) | patch_namespace_labels(extra_labels) }}"

Where extra_labels is a dict, e.g.:
  { "pod-security.kubernetes.io/enforce": "privileged",
    "pod-security.kubernetes.io/audit":   "privileged",
    "pod-security.kubernetes.io/warn":    "privileged" }
"""

import json

import yaml


def _to_plain(obj):
    """Convert Ansible variable types to plain Python types via JSON round-trip."""
    return json.loads(json.dumps(obj))


def patch_namespace_labels(raw_yaml, extra_labels):
    """Find every Namespace doc in a multi-doc YAML stream and merge labels."""
    if not extra_labels:
        return raw_yaml

    plain_labels = _to_plain(extra_labels)

    docs = list(yaml.safe_load_all(raw_yaml))
    for doc in docs:
        if isinstance(doc, dict) and doc.get("kind") == "Namespace":
            meta = doc.setdefault("metadata", {})
            labels = meta.setdefault("labels", {})
            labels.update(plain_labels)

    return yaml.safe_dump_all(
        docs,
        default_flow_style=False,
        sort_keys=False,
        explicit_start=True,
    )


class FilterModule:
    def filters(self):
        return {
            "patch_namespace_labels": patch_namespace_labels,
        }
