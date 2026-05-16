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

    docs_out = []
    for raw_doc in raw_yaml.split("\n---"):
        # Try to parse the document
        stripped = raw_doc.lstrip("\n")
        if not stripped or stripped.isspace():
            docs_out.append(raw_doc)
            continue
        try:
            doc = yaml.safe_load(stripped)
        except yaml.YAMLError:
            docs_out.append(raw_doc)
            continue

        if not isinstance(doc, dict) or doc.get("kind") != "Namespace":
            docs_out.append(raw_doc)
            continue

        # Merge labels into the Namespace document
        meta = doc.setdefault("metadata", {})
        labels = meta.setdefault("labels", {})
        labels.update(plain_labels)

        # Re-serialize just this document
        dumped = yaml.dump(doc, default_flow_style=False, sort_keys=False)
        # Preserve the leading newline/whitespace pattern of the original chunk
        leading = ""
        for ch in raw_doc:
            if ch in ("\n", " "):
                leading += ch
            else:
                break
        docs_out.append(leading + dumped.rstrip("\n"))

    return "\n---".join(docs_out)


class FilterModule:
    def filters(self):
        return {
            "patch_namespace_labels": patch_namespace_labels,
        }
