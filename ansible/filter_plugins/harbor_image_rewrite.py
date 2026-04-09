"""
Filter plugin to rewrite container image references through Harbor proxy cache.

Parses rendered Kubernetes YAML (multi-doc) and rewrites every image: field
so that pulls go through the Harbor pull-through proxy instead of hitting
upstream registries directly.

Usage in a task:
  content: "{{ helm_output | harbor_rewrite_images(harbor_registry_map, harbor_proxy_images_enabled | default(false)) }}"
"""

import re
import yaml


# Registries that are implied when an image has no explicit registry prefix.
# "nginx:latest" really means "docker.io/library/nginx:latest".
_DEFAULT_REGISTRY = "docker.io"


# SafeLoader subclass that handles YAML merge keys and value tags
# that appear in Helm output (e.g. kube-prometheus-stack recording rules).
class _SafeLoaderWithValueTag(yaml.SafeLoader):
    pass

_SafeLoaderWithValueTag.add_constructor(
    'tag:yaml.org,2002:value',
    lambda loader, node: loader.construct_scalar(node),
)


def _parse_image(image_ref):
    """
    Split an image reference into (registry, path, tag_or_digest).

    Examples:
      "nginx:latest"                     -> ("docker.io", "library/nginx", "latest")
      "grafana/grafana:10.0"             -> ("docker.io", "grafana/grafana", "10.0")
      "quay.io/cilium/cilium:v1.18.5"    -> ("quay.io", "cilium/cilium", "v1.18.5")
      "ghcr.io/owner/repo@sha256:abc123" -> ("ghcr.io", "owner/repo", "sha256:abc123")
    """
    # Separate digest (@sha256:…) or tag (:…)
    tag_or_digest = ""
    if "@" in image_ref:
        image_ref, tag_or_digest = image_ref.rsplit("@", 1)
        tag_or_digest = "@" + tag_or_digest
    elif ":" in image_ref.split("/")[-1]:
        parts = image_ref.rsplit(":", 1)
        image_ref = parts[0]
        tag_or_digest = ":" + parts[1]

    # Determine if the first component is a registry (contains a dot or colon
    # or is "localhost").
    parts = image_ref.split("/", 1)
    if len(parts) == 1:
        # bare image like "nginx" → docker.io/library/nginx
        return _DEFAULT_REGISTRY, "library/" + parts[0], tag_or_digest
    first = parts[0]
    if "." in first or ":" in first or first == "localhost":
        registry = first
        path = parts[1]
    else:
        # e.g. "grafana/grafana" → docker.io
        registry = _DEFAULT_REGISTRY
        path = image_ref

    # docker.io official images: "docker.io/nginx" → "docker.io/library/nginx"
    if registry in (_DEFAULT_REGISTRY, "registry-1.docker.io"):
        if "/" not in path:
            path = "library/" + path

    return registry, path, tag_or_digest


def _rewrite_image(image_ref, registry_map):
    """
    Rewrite a single image reference using the registry map.
    Returns the original string unchanged if no map entry matches.
    """
    registry, path, tag_or_digest = _parse_image(image_ref)
    target = registry_map.get(registry)
    if target is None:
        return image_ref
    return target + "/" + path + tag_or_digest


def harbor_rewrite_images(helm_output, registry_map, enabled=True):
    """
    Rewrite all container image references in rendered Kubernetes YAML.

    Walks every document in the multi-doc YAML stream and rewrites image
    fields in pod specs (Deployment, StatefulSet, DaemonSet, Job, CronJob,
    Pod, ReplicaSet) using the provided registry_map.

    Args:
        helm_output: String — rendered multi-document YAML from helm template
        registry_map: Dict — maps upstream registry to Harbor proxy URL
                      e.g. {"docker.io": "harbor.example.com/dockerhub", ...}
        enabled: Bool — when False, returns helm_output unchanged (avoids
                 YAML parsing entirely so the filter is safe to call always)

    Returns:
        Rewritten YAML string
    """
    if not enabled or not registry_map:
        return helm_output

    # Helm output may contain stray tabs
    helm_output = helm_output.replace('\t', '  ')

    documents = list(yaml.load_all(helm_output, Loader=_SafeLoaderWithValueTag))

    for doc in documents:
        if not doc:
            continue
        _walk_and_rewrite(doc, registry_map)

    output_parts = []
    for doc in documents:
        if doc:
            output_parts.append(
                yaml.dump(doc, default_flow_style=False, sort_keys=False)
            )

    return '---\n' + '---\n'.join(output_parts)


def _walk_and_rewrite(obj, registry_map):
    """
    Recursively walk a parsed Kubernetes manifest and rewrite every
    'image' field that sits inside a container-like dict (has 'name'
    and 'image' keys).
    """
    if isinstance(obj, dict):
        # A container-like object: has both 'name' and 'image' keys
        if 'image' in obj and isinstance(obj['image'], str):
            obj['image'] = _rewrite_image(obj['image'], registry_map)
        for v in obj.values():
            _walk_and_rewrite(v, registry_map)
    elif isinstance(obj, list):
        for item in obj:
            _walk_and_rewrite(item, registry_map)


class FilterModule(object):
    """Ansible filter plugin for rewriting images through Harbor proxy."""

    def filters(self):
        return {
            'harbor_rewrite_images': harbor_rewrite_images,
        }
